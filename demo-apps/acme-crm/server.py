"""Acme CRM: synthetic customer records and tags for the demo workflow.

Usage (from the repository root):
    python demo-apps/acme-crm/server.py serve      # http://127.0.0.1:7702/
    python demo-apps/acme-crm/server.py reset      # wipe and reseed demo data
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_common import DemoApp, HttpError, esc, json_response, main  # noqa: E402

CUSTOMER_ID = r"[A-Za-z0-9_-]{1,32}"
MAX_IDS = 200


class AcmeCrm(DemoApp):
    name = "acme-crm"
    title = "Acme CRM"
    hostname = "crm.acme.local"
    default_port = 7702
    nav = (("/", "Customers"),)
    static_files = {"crm.js": Path(__file__).resolve().parent / "static" / "crm.js"}
    tables = ("customers", "tickets")
    schema = (
        "DROP TABLE IF EXISTS tickets",
        "DROP TABLE IF EXISTS customers",
        """CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            position INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            plan TEXT NOT NULL,
            last_login_days INTEGER NOT NULL CHECK (last_login_days >= 0),
            tag TEXT NOT NULL,
            tag_version INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE tickets (
            ticket_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers (customer_id),
            subject TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
            opened_days_ago INTEGER NOT NULL CHECK (opened_days_ago >= 0))""",
    )

    @property
    def tags(self):
        return list(self.seed["tags"])

    def load_seed(self, conn):
        for position, c in enumerate(self.seed["customers"], start=1):
            if c["tag"] not in self.tags:
                raise ValueError(f"Seed tag {c['tag']!r} is not an allowed tag")
            conn.execute(
                "INSERT INTO customers (customer_id, position, name, email, plan, last_login_days, tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c["customer_id"], position, c["name"], c["email"], c["plan"], c["last_login_days"], c["tag"]),
            )
        for t in self.seed["tickets"]:
            conn.execute(
                "INSERT INTO tickets VALUES (?, ?, ?, ?, ?)",
                (t["ticket_id"], t["customer_id"], t["subject"], t["status"], t["opened_days_ago"]),
            )

    def _register_routes(self):
        self.route("GET", r"/", self.home)
        self.route("GET", rf"/customers/(?P<customer_id>{CUSTOMER_ID})", self.customer_page)
        self.route("GET", r"/api/tags", self.list_tags)
        self.route("GET", r"/api/customers", self.list_customers)
        self.route("GET", rf"/api/customers/(?P<customer_id>{CUSTOMER_ID})", self.get_customer)
        self.route("PATCH", rf"/api/customers/(?P<customer_id>{CUSTOMER_ID})", self.update_customer)

    # Data --------------------------------------------------------------------

    def _customers(self, ids=None):
        conn = self.connect()
        try:
            if ids is None:
                rows = conn.execute("SELECT * FROM customers ORDER BY position").fetchall()
            else:
                marks = ",".join("?" * len(ids))
                rows = conn.execute(f"SELECT * FROM customers WHERE customer_id IN ({marks}) ORDER BY position", ids).fetchall()
            tickets = conn.execute(
                "SELECT ticket_id, customer_id, subject, opened_days_ago FROM tickets "
                "WHERE status = 'open' ORDER BY opened_days_ago, ticket_id"
            ).fetchall()
        finally:
            conn.close()
        open_by_customer = {}
        for t in tickets:
            open_by_customer.setdefault(t["customer_id"], []).append(
                {"ticket_id": t["ticket_id"], "subject": t["subject"], "opened_days_ago": t["opened_days_ago"]}
            )
        result = []
        for row in rows:
            record = {k: row[k] for k in ("customer_id", "name", "email", "plan", "last_login_days", "tag", "tag_version")}
            record["open_tickets"] = open_by_customer.get(row["customer_id"], [])
            record["open_ticket_count"] = len(record["open_tickets"])
            result.append(record)
        return result

    def _customer(self, customer_id):
        found = self._customers([customer_id])
        if not found:
            raise HttpError(404, "NOT_FOUND", f"No customer {customer_id}.")
        return found[0]

    # Pages -------------------------------------------------------------------

    def home(self, request):
        rows = "\n".join(
            f'<li data-testid="customer-link" data-record-id="{esc(c["customer_id"])}">'
            f'<a href="/customers/{esc(c["customer_id"])}">{esc(c["customer_id"])} {esc(c["name"])}</a></li>'
            for c in self._customers()
        )
        return self.page(f"{self.title} - Customers", f'<h1>Customers</h1>\n<ul data-testid="customer-list">\n{rows}\n</ul>')

    def customer_page(self, request, customer_id):
        try:
            c = self._customer(customer_id)
        except HttpError:
            return self.html_error(404, f"No customer {customer_id}.")
        options = "".join(
            f'<option value="{esc(tag)}"{" selected" if tag == c["tag"] else ""}>{esc(tag or "(no tag)")}</option>'
            for tag in self.tags
        )
        tickets = "".join(
            f'<li data-testid="open-ticket" data-ticket-id="{esc(t["ticket_id"])}">'
            f'{esc(t["ticket_id"])}: {esc(t["subject"])} (opened {t["opened_days_ago"]} days ago)</li>'
            for t in c["open_tickets"]
        )
        record_id = esc(c["customer_id"])
        return self.page(
            f"{self.title} - Customer {c['customer_id']}",
            f'<h1>Customer <span data-testid="customer-id">{record_id}</span></h1>'
            "<dl>"
            f'<dt>Name</dt><dd data-testid="customer-name">{esc(c["name"])}</dd>'
            f'<dt>Plan</dt><dd data-testid="customer-plan">{esc(c["plan"])}</dd>'
            f'<dt>Days since last login</dt><dd data-testid="last-login-days">{c["last_login_days"]}</dd>'
            f'<dt>Open tickets</dt><dd data-testid="open-ticket-count">{c["open_ticket_count"]}</dd>'
            f'<dt>Current tag</dt><dd data-testid="current-tag">{esc(c["tag"] or "(no tag)")}</dd>'
            "</dl>"
            f'<ul data-testid="open-ticket-list" aria-label="Open tickets">{tickets}</ul>'
            '<form id="cust">'
            '<div class="field-tag"><label for="tag">Tag</label>'
            f'<select id="tag" name="tag" data-testid="tag-select" aria-label="Customer Tag">{options}</select></div>'
            '<button type="submit" data-testid="save-btn">Save</button>'
            '<p data-testid="save-status" role="status" aria-live="polite"></p>'
            "</form>",
            main_attrs=f' data-testid="customer-record" data-record-id="{record_id}"',
            script="crm.js",
        )

    # API ---------------------------------------------------------------------

    def list_tags(self, request):
        return json_response(200, {"tags": self.tags})

    def list_customers(self, request):
        raw = request.param("ids")
        if raw is None:
            return json_response(200, {"customers": self._customers(), "missing": []})
        ids = raw.split(",")
        if not 1 <= len(ids) <= MAX_IDS or len(set(ids)) != len(ids):
            raise HttpError(422, "VALIDATION_ERROR", f"ids must list 1 to {MAX_IDS} distinct customer IDs.")
        if not all(re.fullmatch(CUSTOMER_ID, i) for i in ids):
            raise HttpError(422, "VALIDATION_ERROR", "ids contains an invalid customer ID.")
        customers = self._customers(ids)
        found = {c["customer_id"] for c in customers}
        return json_response(200, {"customers": customers, "missing": [i for i in ids if i not in found]})

    def get_customer(self, request, customer_id):
        return json_response(200, {"customer": self._customer(customer_id)})

    def update_customer(self, request, customer_id):
        payload = request.json()
        if set(payload) != {"tag"}:
            raise HttpError(422, "VALIDATION_ERROR", "Only the tag field can be changed; send exactly {\"tag\": ...}.")
        tag = payload["tag"]
        if not isinstance(tag, str) or tag not in self.tags:
            raise HttpError(422, "VALIDATION_ERROR", f"tag must be one of {self.tags}.")
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT tag FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise HttpError(404, "NOT_FOUND", f"No customer {customer_id}.")
            changed = row["tag"] != tag
            if changed:
                conn.execute(
                    "UPDATE customers SET tag = ?, tag_version = tag_version + 1 WHERE customer_id = ?",
                    (tag, customer_id),
                )
            conn.execute("COMMIT")
        finally:
            conn.close()
        return json_response(200, {"customer": self._customer(customer_id), "changed": changed})


if __name__ == "__main__":
    sys.exit(main(AcmeCrm))
