import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent.ghost.execution.canonical import canonical
from agent.ghost.execution.errors import GhostError
from agent.ghost.remote.pairing import RemoteStore
from agent.ghost.remote.service import RemoteService
from agent.ghost.remote.signing import COMMAND_DOMAIN, RESPONSE_DOMAIN, CommandVerifier
from .helpers import runtime


def paired(tmp_path, clock=lambda: 1792645200.0):
    store = RemoteStore(tmp_path / "remote.sqlite3", clock=clock)
    phone = Ed25519PrivateKey.generate()
    pubkey = phone.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    session = store.begin("desktop", "00" * 32)
    claim = {"pairing_id": session["pairing_id"], "capability": session["capability"], "device_id": "phone", "public_key": pubkey}
    sig = phone.sign(b"digitalghost.pair.v1\n" + canonical(claim)).hex()
    result = store.claim(**claim, signature=sig)
    store.confirm(session["pairing_id"], result["fingerprint"])
    return store, phone


def command(store, phone, kind="get_status", **extra):
    envelope = {"protocol_version": 1, "msg_id": uuid4().hex, "from_device": "phone", "to_device": "desktop", "kind": "command",
                "payload": {"command_id": uuid4().hex, "device_id": "phone", "type": kind,
                            "issued_at": datetime.fromtimestamp(store.clock(), timezone.utc).isoformat().replace("+00:00", "Z"),
                            "nonce": uuid4().hex, **extra}}
    envelope["sig"] = phone.sign(COMMAND_DOMAIN + canonical(envelope)).hex()
    return envelope


def test_signatures_bind_routing_and_reject_replay_after_restart(tmp_path):
    store, phone = paired(tmp_path)
    raw = json.dumps(command(store, phone))
    verifier = CommandVerifier("desktop", store)
    assert verifier.verify(raw, lambda envelope: None)["from_device"] == "phone"
    clock = store.clock
    store.close()
    store = RemoteStore(tmp_path / "remote.sqlite3", clock=clock)
    verifier = CommandVerifier("desktop", store)
    with pytest.raises(GhostError) as exc:
        verifier.verify(raw, lambda envelope: None)
    assert exc.value.code == "REPLAY_DETECTED"
    tampered = command(store, phone)
    tampered["payload"]["command_id"] = "different"
    with pytest.raises(GhostError) as exc:
        verifier.verify(json.dumps(tampered), lambda envelope: None)
    assert exc.value.code == "SIGNATURE_INVALID"
    tampered = command(store, phone)
    tampered["to_device"] = "another-desktop"
    with pytest.raises(GhostError) as exc:
        verifier.verify(json.dumps(tampered), lambda envelope: None)
    assert exc.value.code == "UNAUTHORIZED"
    store.close()


def test_expiry_revocation_and_duplicate_json(tmp_path):
    store, phone = paired(tmp_path)
    verifier = CommandVerifier("desktop", store)
    envelope = command(store, phone)
    store.clock = lambda: 1792645300.0
    with pytest.raises(GhostError) as exc:
        verifier.verify(json.dumps(envelope), lambda envelope: None)
    assert exc.value.code == "COMMAND_EXPIRED"
    valid = json.dumps(command(store, phone))
    duplicated = valid.replace('"kind": "command"', '"kind":"command","kind":"command"')
    with pytest.raises(GhostError) as exc:
        verifier.verify(duplicated, lambda envelope: None)
    assert exc.value.code == "VALIDATION_ERROR"
    store.revoke("phone")
    with pytest.raises(GhostError) as exc:
        verifier.verify(valid, lambda envelope: None)
    assert exc.value.code == "UNAUTHORIZED"
    store.close()


def test_nonce_consumption_is_atomic_under_duplicate_delivery(tmp_path):
    store, phone = paired(tmp_path)
    raw = json.dumps(command(store, phone))
    clock = store.clock
    store.close()

    def verify():
        local = RemoteStore(tmp_path / "remote.sqlite3", clock=clock)
        try:
            CommandVerifier("desktop", local).verify(raw, lambda envelope: None)
            return "accepted"
        except GhostError as exc:
            return exc.code
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: verify(), range(2)))
    assert sorted(results) == ["REPLAY_DETECTED", "accepted"]


def test_pairing_cannot_replace_existing_device_key(tmp_path):
    store, phone = paired(tmp_path)
    other = Ed25519PrivateKey.generate()
    session = store.begin("desktop", "00" * 32)
    claim = {"pairing_id": session["pairing_id"], "capability": session["capability"], "device_id": "phone",
             "public_key": other.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()}
    result = store.claim(**claim, signature=other.sign(b"digitalghost.pair.v1\n" + canonical(claim)).hex())
    with pytest.raises(GhostError) as exc:
        store.confirm(session["pairing_id"], result["fingerprint"])
    assert exc.value.code == "CONFLICT"
    store.close()


def test_remote_status_is_signed_and_contains_no_field_values(tmp_path):
    async def check():
        value = runtime(tmp_path)
        store, phone = paired(tmp_path)
        desktop_key = Ed25519PrivateKey.generate()
        service = RemoteService("desktop", desktop_key, store, value)
        response = await service.handle(json.dumps(command(store, phone)))
        signature = bytes.fromhex(response.pop("sig"))
        desktop_key.public_key().verify(signature, RESPONSE_DOMAIN + canonical(response))
        assert response["payload"] == {"ok": True, "data": {"runs": []}}
        assert "parameters" not in json.dumps(response)
        with pytest.raises(InvalidSignature):
            desktop_key.public_key().verify(signature, COMMAND_DOMAIN + canonical(response))
        await value.close()
        value.store.close()
        store.close()
    asyncio.run(check())


def test_canonical_numbers_and_utf16_key_order():
    assert canonical({"b": 1.0, "a": 1e-6, "z": -0.0}) == b'{"a":0.000001,"b":1,"z":0}'
    assert canonical({"\ufffd": 1, "😀": 2}).decode() == '{"😀":2,"�":1}'
    with pytest.raises(GhostError):
        canonical({"bad": float("nan")})
