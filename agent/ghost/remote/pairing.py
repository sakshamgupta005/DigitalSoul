import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..execution.canonical import canonical
from ..execution.errors import GhostError


class RemoteStore:
    def __init__(self, path, clock=time.time):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS m2_devices (id TEXT PRIMARY KEY, pubkey TEXT, active INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS m2_nonces (device TEXT, nonce TEXT, command TEXT, consumed REAL, PRIMARY KEY(device,nonce), UNIQUE(device,command))")
        self.sessions = {}
        self.clock = clock

    def begin(self, desktop_id, desktop_pubkey):
        pid, capability = uuid4().hex, secrets.token_urlsafe(32)
        self.sessions[pid] = {"hash": hashlib.sha256(capability.encode()).hexdigest(),
                              "expires": self.clock() + 300, "attempts": 0, "claim": None}
        return {"pairing_id": pid, "capability": capability, "expires_at_unix": self.clock() + 300,
                "desktop_id": desktop_id, "desktop_pubkey": desktop_pubkey}

    def claim(self, pairing_id, capability, device_id, public_key, signature):
        session = self.sessions.get(pairing_id)
        if not session or session["expires"] <= self.clock() or session["attempts"] >= 5 or session["claim"]:
            raise GhostError("POLICY_BLOCKED", "Pairing session unavailable", 403)
        session["attempts"] += 1
        if not secrets.compare_digest(hashlib.sha256(capability.encode()).hexdigest(), session["hash"]):
            raise GhostError("UNAUTHORIZED", "Pairing capability invalid", 401)
        if not device_id or len(device_id) > 128:
            raise GhostError("VALIDATION_ERROR", "Invalid device identity", 422)
        payload = {"pairing_id": pairing_id, "capability": capability, "device_id": device_id, "public_key": public_key}
        try:
            key = bytes.fromhex(public_key)
            Ed25519PublicKey.from_public_bytes(key).verify(bytes.fromhex(signature), b"digitalghost.pair.v1\n" + canonical(payload))
        except (ValueError, InvalidSignature) as exc:
            raise GhostError("SIGNATURE_INVALID", "Pairing proof invalid", 403) from exc
        fingerprint = hashlib.sha256(key).hexdigest()
        session["claim"] = {"device_id": device_id, "public_key": public_key, "fingerprint": fingerprint}
        return {"pairing_id": pairing_id, "fingerprint": fingerprint, "device_id": device_id}

    def confirm(self, pairing_id, fingerprint):
        # Only called through the authenticated desktop/local route.
        session = self.sessions.get(pairing_id)
        claim = session.get("claim") if session else None
        if not claim or session["expires"] <= self.clock() or not secrets.compare_digest(claim["fingerprint"], fingerprint):
            raise GhostError("POLICY_BLOCKED", "Pairing fingerprint/session mismatch", 403)
        existing = self.db.execute("SELECT pubkey FROM m2_devices WHERE id=?", (claim["device_id"],)).fetchone()
        if existing and existing[0] != claim["public_key"]:
            raise GhostError("CONFLICT", "Device ID is already bound to a different key")
        self.db.execute("INSERT INTO m2_devices VALUES (?, ?, 1) ON CONFLICT(id) DO UPDATE SET active=1", (claim["device_id"], claim["public_key"]))
        del self.sessions[pairing_id]
        return {"device_id": claim["device_id"], "fingerprint": fingerprint}

    def key(self, device_id):
        row = self.db.execute("SELECT pubkey FROM m2_devices WHERE id=? AND active=1", (device_id,)).fetchone()
        if not row:
            raise GhostError("UNAUTHORIZED", "Device is not actively paired", 403)
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(row[0]))

    def revoke(self, device_id):
        self.db.execute("UPDATE m2_devices SET active=0 WHERE id=?", (device_id,))

    def devices(self):
        return [{"device_id": row[0], "fingerprint": hashlib.sha256(bytes.fromhex(row[1])).hexdigest(), "active": bool(row[2])}
                for row in self.db.execute("SELECT id,pubkey,active FROM m2_devices ORDER BY id")]

    def consume(self, device, nonce, command):
        try:
            self.db.execute("INSERT INTO m2_nonces VALUES (?, ?, ?, ?)", (device, nonce, command, self.clock()))
        except sqlite3.IntegrityError as exc:
            raise GhostError("REPLAY_DETECTED", "Nonce or command ID was already consumed") from exc
        # Retained without pruning in MVP: never evict a live freshness-window entry.

    def close(self):
        self.db.close()
