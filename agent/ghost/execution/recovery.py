"""M2-private durable store; replace through this interface with M4 persistence."""
import json
import sqlite3
from pathlib import Path

from .errors import GhostError


class RunStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS m2_runs (id TEXT PRIMARY KEY, idem TEXT UNIQUE, fingerprint TEXT, body TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS m2_sequence (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO m2_sequence VALUES (1, 0)")

    def next_sequence(self):
        return self.db.execute("UPDATE m2_sequence SET value=value+1 WHERE id=1 RETURNING value").fetchone()[0]

    def create(self, run, key, fingerprint):
        try:
            self.db.execute("INSERT INTO m2_runs VALUES (?, ?, ?, ?)",
                            (run["run_id"], key, fingerprint, json.dumps(run, allow_nan=False)))
        except sqlite3.IntegrityError as exc:
            raise GhostError("CONFLICT", "Run idempotency key already exists") from exc

    def by_key(self, key):
        row = self.db.execute("SELECT fingerprint, body FROM m2_runs WHERE idem=?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save(self, run):
        self.db.execute("UPDATE m2_runs SET body=? WHERE id=?", (json.dumps(run, allow_nan=False), run["run_id"]))

    def get(self, run_id):
        row = self.db.execute("SELECT body FROM m2_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise GhostError("NOT_FOUND", "Run not found", 404)
        return json.loads(row[0])

    def list(self, limit=50):
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM m2_runs ORDER BY rowid DESC LIMIT ?", (limit,))]

    def all(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM m2_runs ORDER BY rowid")]

    def close(self):
        self.db.close()
