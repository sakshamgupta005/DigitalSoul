"""Acme Portal: synthetic overdue-account report and team notes for the demo workflow.

Usage (from the repository root):
    python demo-apps/acme-portal/server.py serve      # http://127.0.0.1:7701/
    python demo-apps/acme-portal/server.py reset      # wipe and reseed demo data
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_common import DemoApp, HttpError, esc, json_response, main, parse_limit, utc_now  # noqa: E402

NOTE_MAX_CHARS = 2000
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class AcmePortal(DemoApp):
    name = "acme-portal"
    title = "Acme Internal Portal"
    hostname = "portal.acme.local"
    default_port = 7701
    nav = (("/reports/overdue", "Reports"), ("/team/notes", "Team Notes"))
    static_files = {"portal.js": Path(__file__).resolve().parent / "static" / "portal.js"}
    tables = ("overdue_accounts", "team_notes")
    schema = (
        "DROP TABLE IF EXISTS overdue_accounts",
        "DROP TABLE IF EXISTS team_notes",
        """CREATE TABLE overdue_accounts (
            position INTEGER PRIMARY KEY,
            account_id TEXT NOT NULL UNIQUE,
            customer_name TEXT NOT NULL,
            amount_due_cents INTEGER NOT NULL CHECK (amount_due_cents >= 0),
            days_overdue INTEGER NOT NULL CHECK (days_overdue >= 0))""",
        """CREATE TABLE team_notes (
            note_id INTEGER PRIMARY KEY,
            body TEXT NOT NULL,
            author TEXT NOT NULL,
            created_at TEXT NOT NULL,
            idempotency_key TEXT UNIQUE)""",
    )

    def load_seed(self, conn):
        for position, row in enumerate(self.seed["overdue_accounts"], start=1):
            conn.execute(
                "INSERT INTO overdue_accounts VALUES (?, ?, ?, ?, ?)",
                (position, row["account_id"], row["customer_name"], row["amount_due_cents"], row["days_overdue"]),
            )
        for note in self.seed["team_notes"]:
            conn.execute(
                "INSERT INTO team_notes (body, author, created_at) VALUES (?, ?, ?)",
                (note["body"], note["author"], note["created_at"]),
            )

    def _register_routes(self):
        self.route("GET", r"/", self.home)
        self.route("GET", r"/reports/overdue", self.overdue_page)
        self.route("GET", r"/team/notes", self.notes_page)
        self.route("GET", r"/api/reports/overdue", self.overdue_api)
        self.route("GET", r"/api/team/notes", self.list_notes)
        self.route("POST", r"/api/team/notes", self.create_note)
        self.route("GET", r"/api/team/notes/(?P<note_id>[0-9]{1,12})", self.get_note)

    # Data --------------------------------------------------------------------

    def _accounts(self):
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT account_id, customer_name, amount_due_cents, days_overdue FROM overdue_accounts ORDER BY position"
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def _notes(self, contains=None, limit=50):
        where, args = "", []
        if contains is not None:
            where, args = "WHERE instr(body, ?) > 0", [contains]
        conn = self.connect()
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM team_notes {where}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT note_id, body, author, created_at FROM team_notes {where} ORDER BY note_id DESC LIMIT ?",
                [*args, limit],
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows], count

    # Pages -------------------------------------------------------------------

    def home(self, request):
        return self.page(
            self.title,
            "<h1>Acme Internal Portal</h1>"
            '<p>Use <a href="/reports/overdue">Reports</a> for overdue invoices '
            'or <a href="/team/notes">Team Notes</a> for weekly summaries.</p>',
        )

    def overdue_page(self, request):
        items = "\n".join(
            f'<li data-testid="overdue-account" data-record-id="{esc(a["account_id"])}">'
            f'<span data-testid="account-id">{esc(a["account_id"])}</span> '
            f'<span class="muted">{esc(a["customer_name"])}, '
            f'${a["amount_due_cents"] // 100:,}.{a["amount_due_cents"] % 100:02d} due, '
            f'{a["days_overdue"]} days overdue</span></li>'
            for a in self._accounts()
        )
        return self.page(
            f"{self.title} - Overdue Invoices",
            "<h1>Overdue Invoices</h1>"
            f'<ul data-testid="overdue-account-list" aria-label="Overdue accounts">\n{items}\n</ul>',
        )

    def notes_page(self, request):
        notes, _ = self._notes(limit=200)
        items = "\n".join(
            f'<li data-testid="team-note" data-note-id="{n["note_id"]}">'
            f'<p class="note-body">{esc(n["body"])}</p>'
            f'<p class="muted">{esc(n["author"])}, {esc(n["created_at"])}</p></li>'
            for n in notes
        )
        return self.page(
            f"{self.title} - Team Notes",
            "<h1>Team Notes</h1>"
            '<form id="team-note-form">'
            '<label for="team-note-body">Weekly summary</label>'
            f'<textarea id="team-note-body" name="body" data-testid="team-note-body" maxlength="{NOTE_MAX_CHARS}" rows="4"></textarea>'
            '<button type="submit" data-testid="post-team-note-btn">Post Weekly Summary</button>'
            '<p data-testid="post-status" role="status" aria-live="polite"></p>'
            "</form>"
            f'<ol data-testid="team-note-list" aria-label="Team notes">\n{items}\n</ol>',
            script="portal.js",
        )

    # API ---------------------------------------------------------------------

    def overdue_api(self, request):
        accounts = self._accounts()
        return json_response(
            200,
            {"accounts": accounts, "account_ids": [a["account_id"] for a in accounts], "synthetic_demo_data": True},
        )

    def list_notes(self, request):
        contains = request.param("contains")
        if contains is not None and not 1 <= len(contains) <= NOTE_MAX_CHARS:
            raise HttpError(422, "VALIDATION_ERROR", f"contains must be 1 to {NOTE_MAX_CHARS} characters.")
        notes, count = self._notes(contains, parse_limit(request))
        return json_response(200, {"notes": notes, "count": count})

    def get_note(self, request, note_id):
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT note_id, body, author, created_at FROM team_notes WHERE note_id = ?", (int(note_id),)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HttpError(404, "NOT_FOUND", f"No team note {note_id}.")
        return json_response(200, {"note": dict(row)})

    def create_note(self, request):
        payload = request.json()
        if set(payload) != {"body"}:
            raise HttpError(422, "VALIDATION_ERROR", "Body must contain exactly one field: body.")
        body = payload["body"]
        if not isinstance(body, str) or not body.strip() or len(body) > NOTE_MAX_CHARS:
            raise HttpError(422, "VALIDATION_ERROR", f"body must be a non-blank string of at most {NOTE_MAX_CHARS} characters.")
        key = request.headers.get("Idempotency-Key")
        if key is not None and not IDEMPOTENCY_KEY.fullmatch(key):
            raise HttpError(422, "VALIDATION_ERROR", "Idempotency-Key must be 1-128 characters of [A-Za-z0-9._:-].")

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if key is not None:
                existing = conn.execute(
                    "SELECT note_id, body, author, created_at FROM team_notes WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if existing is not None:
                    conn.execute("COMMIT")
                    if existing["body"] != body:
                        raise HttpError(409, "CONFLICT", "Idempotency-Key was already used with a different body.")
                    return json_response(200, {"note": dict(existing)}, {"Idempotent-Replayed": "true"})
            cursor = conn.execute(
                "INSERT INTO team_notes (body, author, created_at, idempotency_key) VALUES (?, ?, ?, ?)",
                (body, "demo-user (synthetic)", utc_now(), key),
            )
            note = conn.execute(
                "SELECT note_id, body, author, created_at FROM team_notes WHERE note_id = ?", (cursor.lastrowid,)
            ).fetchone()
            conn.execute("COMMIT")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return json_response(201, {"note": dict(note)}, {"Location": f"/api/team/notes/{note['note_id']}"})


if __name__ == "__main__":
    sys.exit(main(AcmePortal))
