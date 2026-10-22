"""Local encrypted recordings. Raw desktop evidence never grants execution trust."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..safety.credentials import protect


class Catalog:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self.db.commit()

    def save(self, recording):
        value = dict(recording)
        value.setdefault("id", str(uuid.uuid4()))
        value.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        if not isinstance(value.get("name"), str) or not 1 <= len(value["name"].strip()) <= 80:
            raise ValueError("Use a recording name of 1–80 characters")
        if not isinstance(value.get("steps"), list) or len(value["steps"]) > 500:
            raise ValueError("Recording exceeds the 500-step limit")
        value["name"] = value["name"].strip()
        data = protect(json.dumps(value, ensure_ascii=False).encode())
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO recordings VALUES (?,?)", (value["id"], data))
        return value

    def get(self, recording_id):
        row = self.db.execute("SELECT payload FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if not row:
            raise ValueError("Recording not found")
        return json.loads(protect(row[0], decrypt=True))

    def list(self):
        result = []
        for row in self.db.execute("SELECT id FROM recordings"):
            value = self.get(row[0])
            result.append({key: value.get(key) for key in
                           ("id", "name", "created_at", "apps", "duration", "replay_error")}
                          | {"step_count": len(value["steps"]), "can_run": bool(value.get("flow"))})
        return sorted(result, key=lambda r: r["created_at"], reverse=True)

    def delete(self, recording_id):
        with self.db:
            self.db.execute("DELETE FROM recordings WHERE id=?", (recording_id,))

    def close(self):
        self.db.close()
