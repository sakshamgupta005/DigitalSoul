"""Durable one-shot desktop jobs. A possibly dispatched message is never retried."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..safety.credentials import protect
from .flow import FORMAT as FLOW_FORMAT, retarget
from .whatsapp_send import FORMAT as SEND_FORMAT
from .whatsapp import validate_recipe


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Include a UTC offset, for example +05:30")
    return result.astimezone(timezone.utc)


class Jobs:
    def __init__(self, path, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS desktop_jobs (
            id TEXT PRIMARY KEY, due REAL NOT NULL, expires REAL NOT NULL,
            state TEXT NOT NULL, payload BLOB NOT NULL, detail TEXT NOT NULL DEFAULT '')""")
        self.db.execute("CREATE TABLE IF NOT EXISTS desktop_control (id INTEGER PRIMARY KEY, stopped INTEGER NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO desktop_control VALUES (1, 0)")
        self.db.commit()

    def close(self):
        self.db.close()

    def schedule(self, recipe, message, at, approved_recipient, now=None):
        validate_recipe(recipe)
        now = now or self.clock()
        due = instant(at)
        if due <= now:
            raise ValueError("Choose a future time")
        if not isinstance(approved_recipient, str) or not approved_recipient.strip():
            raise ValueError("Approve who the message goes to")
        if recipe.get("format") == FLOW_FORMAT:
            # A recorded routine can be sent to someone else; the approval names that person.
            recipe = retarget(recipe, approved_recipient)
        elif recipe.get("format") == SEND_FORMAT:
            if approved_recipient.strip().lower() != recipe["recipient"].strip().lower():
                recipe = dict(recipe, recipient=approved_recipient.strip(), search=approved_recipient.strip())
            validate_recipe(dict(recipe, message=message))
        elif approved_recipient != recipe["recipient"]:
            raise ValueError("Approve the exact recipient recorded in the recipe")
        if not isinstance(message, str) or not message.strip() or len(message) > 4096:
            raise ValueError("Message must contain 1–4096 characters")
        # Multiline/newline input is not allowed in this first native adapter.
        if any(ord(c) < 32 for c in message):
            raise ValueError("Use a single-line message without control characters")
        payload = protect(json.dumps({"recipe": recipe, "message": message}, ensure_ascii=False).encode())
        job_id = str(uuid.uuid4())
        with self.db:
            inserted = self.db.execute("""INSERT INTO desktop_jobs
                SELECT ?, ?, ?, 'pending', ?, ''
                WHERE (SELECT stopped FROM desktop_control WHERE id=1)=0""",
                (job_id, due.timestamp(), due.timestamp() + 60, payload)).rowcount
            if not inserted:
                raise ValueError("Desktop stop is latched; explicitly resume before scheduling")
        return job_id

    def stop(self):
        with self.db:
            self.db.execute("UPDATE desktop_control SET stopped=1 WHERE id=1")
            self.db.execute("UPDATE desktop_jobs SET state='cancelled' WHERE state IN ('pending','preparing')")

    def resume(self):
        with self.db:
            self.db.execute("UPDATE desktop_control SET stopped=0 WHERE id=1")

    def list(self):
        return [dict(row) for row in self.db.execute(
            "SELECT id, due, expires, state, detail FROM desktop_jobs ORDER BY due")]

    def cancel(self, job_id):
        with self.db:
            return self.db.execute("UPDATE desktop_jobs SET state='cancelled' WHERE id=? AND state IN ('pending','preparing')",
                                   (job_id,)).rowcount == 1

    def _transition(self, job_id, before, after, detail=""):
        with self.db:
            guard = " AND (SELECT stopped FROM desktop_control WHERE id=1)=0" if after in ("preparing", "dispatching") else ""
            return self.db.execute("UPDATE desktop_jobs SET state=?,detail=? WHERE id=? AND state=?" + guard,
                                   (after, detail, job_id, before)).rowcount == 1

    def tick(self, adapter, now=None, job_id=None):
        timestamp = (now or self.clock()).timestamp()
        with self.db:
            self.db.execute("UPDATE desktop_jobs SET state='missed' WHERE state='pending' AND expires < ?", (timestamp,))
            extra = " AND id=?" if job_id else ""
            arguments = (timestamp, job_id) if job_id else (timestamp,)
            row = self.db.execute("SELECT * FROM desktop_jobs WHERE state='pending' AND due <= ?" + extra + " ORDER BY due LIMIT 1",
                                  arguments).fetchone()
        if row is None or not self._transition(row["id"], "pending", "preparing"):
            return None
        job_id = row["id"]
        try:
            if hasattr(adapter, "set_guard"):
                adapter.set_guard(lambda: bool(self.db.execute("""SELECT 1 FROM desktop_jobs
                    WHERE id=? AND state IN ('preparing','dispatching')
                    AND (SELECT stopped FROM desktop_control WHERE id=1)=0""", (job_id,)).fetchone()))
            payload = json.loads(protect(row["payload"], decrypt=True))
            adapter.prepare(payload["recipe"], payload["message"])
        except Exception:
            # Never persist exception text: a UIA provider may include private UI text.
            self._transition(job_id, "preparing", "blocked", "Desktop precondition failed; inspect locally")
            return job_id
        if self.clock().timestamp() > row["expires"]:
            self._transition(job_id, "preparing", "missed", "Preparation exceeded the send window; draft may remain")
            return job_id
        # Durable boundary BEFORE the external effect. Cancellation wins until here.
        if not self._transition(job_id, "preparing", "dispatching"):
            return job_id
        try:
            adapter.send(payload["recipe"], payload["message"])
        except Exception:
            self._transition(job_id, "dispatching", "uncertain", "Inspect WhatsApp manually; do not automatically retry")
        else:
            # UIA invocation is NOT proof of WhatsApp delivery or even a server acknowledgement.
            self._transition(job_id, "dispatching", "attempted_unverified", "Send invoked; delivery not verified")
        return job_id
