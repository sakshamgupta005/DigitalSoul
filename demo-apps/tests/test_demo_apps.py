"""Tests for the synthetic Acme demo apps.

Run from the repository root:
    python -m unittest discover -s demo-apps/tests -v

Each test runs real HTTP servers on ephemeral loopback ports with a temporary SQLite
database. Expected selectors and tag values are read from the authoritative
contracts/fixtures/workflow.valid.json, which these tests never modify.
"""

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
REPO = DEMO.parent
sys.path.insert(0, str(DEMO))

import manage  # noqa: E402
from demo_common import RESET_CONFIRMATION, RESET_HEADER, RESET_PATH, create_server, serve_in_thread  # noqa: E402

AcmePortal = manage.load_app_class("acme-portal", "AcmePortal")
AcmeCrm = manage.load_app_class("acme-crm", "AcmeCrm")
WORKFLOW = json.loads((REPO / "contracts" / "fixtures" / "workflow.valid.json").read_text(encoding="utf-8-sig"))
RULE = next(r for r in WORKFLOW["decision_rules"] if r["id"] == "segment")
OVERDUE_IDS = ["1042", "1043", "1044", "1045", "1046", "1047"]


def workflow_steps(steps=None):
    for step in WORKFLOW["steps"] if steps is None else steps:
        yield step
        yield from workflow_steps(step.get("body", []))


def expected_segment(customer):
    # Hand-written mirror of the fixture rule "plan == 'Pro' and last_login_days > 30" (no eval).
    return RULE["then"] if customer["plan"] == "Pro" and customer["last_login_days"] > 30 else RULE["else"]


class Element:
    def __init__(self, tag, attrs, parents):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parents = parents
        self.text = ""


class PageParser(HTMLParser):
    VOID = {"meta", "link", "input", "br", "img"}

    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs, list(self.stack))
        self.elements.append(element)
        if tag not in self.VOID:
            self.stack.append(element)

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop().tag == tag:
                break

    def handle_data(self, data):
        for element in self.stack:
            element.text += data

    def find(self, **attrs):
        return [e for e in self.elements if all(e.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items())]

    def one(self, testcase, **attrs):
        found = self.find(**attrs)
        testcase.assertEqual(len(found), 1, f"expected exactly one element with {attrs}, found {len(found)}")
        return found[0]


class DemoServerTest(unittest.TestCase):
    enable_reset_endpoint = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.portal = AcmePortal(Path(self.tmp.name) / "portal.sqlite3")
        self.crm = AcmeCrm(Path(self.tmp.name) / "crm.sqlite3")
        self.servers = {}
        for key, app in (("portal", self.portal), ("crm", self.crm)):
            server = create_server(app, port=0, enable_reset_endpoint=self.enable_reset_endpoint, quiet=True)
            serve_in_thread(server)
            self.servers[key] = server

    def tearDown(self):
        for server in self.servers.values():
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def url(self, app, path):
        return f"http://127.0.0.1:{self.servers[app].server_port}{path}"

    def call(self, app, method, path, payload=None, headers=None, raw=None, content_type="application/json"):
        body = raw if raw is not None else (None if payload is None else json.dumps(payload).encode("utf-8"))
        request = urllib.request.Request(self.url(app, path), data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read().decode("utf-8")

    def get_json(self, app, path):
        status, _, text = self.call(app, "GET", path)
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def page(self, app, path):
        status, headers, text = self.call(app, "GET", path)
        self.assertEqual(status, 200, text)
        self.assertIn("text/html", headers["Content-Type"])
        return PageParser(text)


class SeedDataTests(DemoServerTest):
    def test_overdue_report_lists_seeded_account_ids_in_order(self):
        data = self.get_json("portal", "/api/reports/overdue")
        self.assertEqual(data["account_ids"], OVERDUE_IDS)
        self.assertTrue(data["synthetic_demo_data"])

    def test_every_overdue_account_exists_in_crm(self):
        ids = ",".join(OVERDUE_IDS)
        data = self.get_json("crm", f"/api/customers?ids={ids}")
        self.assertEqual(data["missing"], [])
        self.assertEqual([c["customer_id"] for c in data["customers"]], OVERDUE_IDS)

    def test_crm_profile_fields_used_by_segmentation(self):
        c = self.get_json("crm", "/api/customers/1042")["customer"]
        self.assertEqual((c["plan"], c["last_login_days"], c["open_ticket_count"], c["tag"]), ("Pro", 45, 2, ""))
        self.assertEqual([t["ticket_id"] for t in c["open_tickets"]], ["T-9002", "T-9001"])
        self.assertEqual(self.get_json("crm", "/api/customers/1046")["customer"]["open_ticket_count"], 2)

    def test_seed_covers_both_decision_outcomes_and_boundaries(self):
        customers = self.get_json("crm", f"/api/customers?ids={','.join(OVERDUE_IDS)}")["customers"]
        expected = {c["customer_id"]: expected_segment(c) for c in customers}
        self.assertEqual(expected["1042"], RULE["then"])
        self.assertEqual(expected["1044"], RULE["else"], "Pro at exactly 30 days is not > 30")
        self.assertEqual(expected["1045"], RULE["then"], "Pro at 31 days is > 30")
        self.assertEqual(expected["1046"], RULE["else"], "non-Pro plan")
        self.assertEqual(set(expected.values()), {RULE["then"], RULE["else"]})

    def test_seed_is_marked_synthetic(self):
        for app in ("portal", "crm"):
            parser = self.page(app, "/")
            self.assertIn("SYNTHETIC DEMO DATA", parser.one(self, data_testid="demo-banner").text)
            self.assertTrue(self.get_json(app, "/api/health")["synthetic_demo_data"])
        emails = [c["email"] for c in self.get_json("crm", "/api/customers")["customers"]]
        self.assertTrue(all(e.endswith("@example.invalid") for e in emails))


class ResetTests(DemoServerTest):
    def test_reset_restores_identical_state_repeatedly(self):
        baseline = (self.portal.snapshot(), self.crm.snapshot())
        self.call("crm", "PATCH", "/api/customers/1042", {"tag": RULE["then"]})
        self.call("portal", "POST", "/api/team/notes", {"body": "temporary note"})
        self.assertNotEqual((self.portal.snapshot(), self.crm.snapshot()), baseline)
        for _ in range(2):
            self.portal.reset()
            self.crm.reset()
            self.assertEqual((self.portal.snapshot(), self.crm.snapshot()), baseline)

    def test_reset_endpoint_is_disabled_by_default(self):
        status, _, _ = self.call("crm", "POST", RESET_PATH, headers={RESET_HEADER: RESET_CONFIRMATION})
        self.assertEqual(status, 404)

    def test_cli_reset_reseeds_database(self):
        db = Path(self.tmp.name) / "cli.sqlite3"
        module = sys.modules[AcmeCrm.__module__]
        self.assertEqual(module.main(AcmeCrm, ["--db", str(db), "reset"]), 0)
        self.assertEqual(AcmeCrm(db).snapshot(), self.crm.snapshot())


class ResetEndpointTests(DemoServerTest):
    enable_reset_endpoint = True

    def test_reset_endpoint_requires_confirmation_header(self):
        self.call("crm", "PATCH", "/api/customers/1042", {"tag": RULE["then"]})
        status, _, _ = self.call("crm", "POST", RESET_PATH)
        self.assertEqual(status, 403)
        self.assertEqual(self.get_json("crm", "/api/customers/1042")["customer"]["tag"], RULE["then"])
        status, _, text = self.call("crm", "POST", RESET_PATH, headers={RESET_HEADER: RESET_CONFIRMATION})
        self.assertEqual(status, 200, text)
        self.assertEqual(self.get_json("crm", "/api/customers/1042")["customer"]["tag"], "")


class CustomerTagTests(DemoServerTest):
    def test_saving_tag_persists_and_is_independently_readable(self):
        status, _, text = self.call("crm", "PATCH", "/api/customers/1042", {"tag": RULE["then"]})
        self.assertEqual(status, 200, text)
        self.assertTrue(json.loads(text)["changed"])
        self.assertEqual(self.get_json("crm", "/api/customers/1042")["customer"]["tag"], RULE["then"])
        listed = self.get_json("crm", "/api/customers?ids=1042,1043")["customers"]
        self.assertEqual({c["customer_id"]: c["tag"] for c in listed}, {"1042": RULE["then"], "1043": ""})
        selected = [e for e in self.page("crm", "/customers/1042").elements if e.tag == "option" and "selected" in e.attrs]
        self.assertEqual([e.attrs["value"] for e in selected], [RULE["then"]])

    def test_tag_survives_server_restart(self):
        self.call("crm", "PATCH", "/api/customers/1043", {"tag": RULE["else"]})
        self.servers["crm"].shutdown()
        self.servers["crm"].server_close()
        server = create_server(AcmeCrm(self.crm.db_path), port=0, quiet=True)
        serve_in_thread(server)
        self.servers["crm"] = server
        self.assertEqual(self.get_json("crm", "/api/customers/1043")["customer"]["tag"], RULE["else"])

    def test_repeated_save_is_idempotent_in_state(self):
        self.call("crm", "PATCH", "/api/customers/1045", {"tag": RULE["then"]})
        status, _, text = self.call("crm", "PATCH", "/api/customers/1045", {"tag": RULE["then"]})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(text)["changed"])
        self.assertEqual(json.loads(text)["customer"]["tag_version"], 1)

    def test_tag_validation(self):
        cases = [
            ({"tag": "Delete everything"}, 422),
            ({"tag": RULE["then"], "plan": "Free"}, 422),
            ({"plan": "Free"}, 422),
            ({"tag": 5}, 422),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                status, _, _ = self.call("crm", "PATCH", "/api/customers/1042", payload)
                self.assertEqual(status, expected)
        self.assertEqual(self.call("crm", "PATCH", "/api/customers/9999", {"tag": RULE["else"]})[0], 404)
        self.assertEqual(self.call("crm", "PATCH", "/api/customers/1042", raw=b"tag=x", content_type="application/x-www-form-urlencoded")[0], 415)
        self.assertEqual(self.call("crm", "PATCH", "/api/customers/1042", raw=b"{not json")[0], 400)
        self.assertEqual(self.get_json("crm", "/api/customers/1042")["customer"]["plan"], "Pro")

    def test_no_delete_or_arbitrary_query_surface(self):
        status, headers, _ = self.call("crm", "DELETE", "/api/customers/1042")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, PATCH")
        self.assertEqual(self.call("crm", "GET", "/api/customers?ids=1042;DROP%20TABLE%20customers")[0], 422)
        self.assertEqual(self.call("crm", "GET", "/api/customers?ids=1042&ids=1043")[0], 422)
        self.assertEqual(self.get_json("crm", "/api/customers/1042")["customer"]["customer_id"], "1042")


class TeamNoteTests(DemoServerTest):
    SUMMARY = "Weekly at-risk segmentation complete; run=run_test_123; processed=6"

    def test_posting_note_creates_readable_note(self):
        status, headers, text = self.call("portal", "POST", "/api/team/notes", {"body": self.SUMMARY})
        self.assertEqual(status, 201, text)
        note = json.loads(text)["note"]
        self.assertEqual(note["body"], self.SUMMARY)
        self.assertEqual(headers["Location"], f"/api/team/notes/{note['note_id']}")
        self.assertEqual(self.get_json("portal", headers["Location"])["note"], note)
        found = self.get_json("portal", "/api/team/notes?contains=run%3Drun_test_123")
        self.assertEqual((found["count"], found["notes"][0]["body"]), (1, self.SUMMARY))
        bodies = [e.text.strip() for e in self.page("portal", "/team/notes").find(data_testid="team-note")]
        self.assertTrue(bodies[0].startswith(self.SUMMARY))

    def test_seeded_previous_note_does_not_match_new_run(self):
        self.assertEqual(self.get_json("portal", "/api/team/notes")["count"], 1)
        self.assertEqual(self.get_json("portal", "/api/team/notes?contains=run%3Drun_test_123")["count"], 0)

    def test_idempotency_key_prevents_duplicate_notes(self):
        key = {"Idempotency-Key": "run_test_123:s6_post_summary"}
        first = self.call("portal", "POST", "/api/team/notes", {"body": self.SUMMARY}, key)
        second = self.call("portal", "POST", "/api/team/notes", {"body": self.SUMMARY}, key)
        self.assertEqual((first[0], second[0]), (201, 200))
        self.assertEqual(second[1]["Idempotent-Replayed"], "true")
        self.assertEqual(json.loads(first[2])["note"], json.loads(second[2])["note"])
        self.assertEqual(self.get_json("portal", "/api/team/notes?contains=run_test_123")["count"], 1)
        conflict = self.call("portal", "POST", "/api/team/notes", {"body": "different"}, key)
        self.assertEqual(conflict[0], 409)

    def test_note_validation_and_html_escaping(self):
        for payload in ({"body": ""}, {"body": "   "}, {"body": "x" * 2001}, {"body": "ok", "author": "x"}, {"text": "x"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.call("portal", "POST", "/api/team/notes", payload)[0], 422)
        self.call("portal", "POST", "/api/team/notes", {"body": "<script>alert(1)</script>"})
        status, _, text = self.call("portal", "GET", "/team/notes")
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)


class WorkflowSelectorTests(DemoServerTest):
    """Every route, testid, role/name and css locator in the workflow fixture resolves uniquely."""

    def test_navigation_and_url_postconditions(self):
        portal_home = self.page("portal", "/")
        reports = [e for e in portal_home.elements if e.tag == "a" and e.text.strip() == "Reports"]
        self.assertEqual({e.attrs["href"] for e in reports}, {"/reports/overdue"})
        for step in workflow_steps():
            if step.get("verb") != "navigate":
                continue
            app = "portal" if step["app"] == "acme-portal" else "crm"
            path = "/" + step["input"].split("/", 3)[3].replace("{{item}}", "1042")
            with self.subTest(step=step["step_id"], path=path):
                self.page(app, path)

    def test_overdue_list_exposes_record_ids(self):
        parser = self.page("portal", "/reports/overdue")
        container = parser.one(self, data_testid="overdue-account-list")
        items = [e for e in parser.find(data_testid="overdue-account") if container in e.parents]
        self.assertEqual([e.attrs["data-record-id"] for e in items], OVERDUE_IDS)

    def test_every_fixture_locator_resolves_uniquely(self):
        pages = {
            "acme-portal": [self.page("portal", "/reports/overdue"), self.page("portal", "/team/notes")],
            "acme-crm": [self.page("crm", "/customers/1042")],
        }
        roles = {"button": {"button"}, "combobox": {"select"}}
        checked = 0
        for step in workflow_steps():
            for locator in step["locators"]:
                counts = []  # matches per page: must be unique wherever the target appears
                for parser in pages[step["app"]]:
                    if locator["by"] == "testid":
                        matches = parser.find(data_testid=locator["value"])
                    elif locator["by"] == "role":
                        matches = [
                            e for e in parser.elements
                            if e.tag in roles[locator["role"]]
                            and (e.attrs.get("aria-label") or e.text.strip()) == locator["name"]
                        ]
                    elif locator["by"] == "css":
                        tag, _, element_id = locator["value"].partition("#")
                        matches = [e for e in parser.elements if e.tag == tag and e.attrs.get("id") == element_id]
                    else:
                        self.assertEqual(locator["by"], "text")
                        matches = [e for e in parser.elements if e.tag == "a" and e.text.strip() == locator["value"]]
                    counts.append(len(matches))
                with self.subTest(step=step["step_id"], locator=locator):
                    self.assertGreaterEqual(sum(counts), 1)
                    self.assertLessEqual(max(counts), 1)
                checked += 1
        self.assertGreaterEqual(checked, 9)

    def test_crm_record_identity_and_read_fields(self):
        parser = self.page("crm", "/customers/1045")
        self.assertEqual(parser.one(self, data_testid="customer-record").attrs["data-record-id"], "1045")
        self.assertEqual(parser.one(self, data_testid="customer-plan").text.strip(), "Pro")
        self.assertEqual(int(parser.one(self, data_testid="last-login-days").text), 31)
        self.assertEqual(int(parser.one(self, data_testid="open-ticket-count").text), 1)
        select = parser.one(self, data_testid="tag-select")
        self.assertEqual(select.attrs["id"], "tag")
        self.assertEqual([p.tag for p in select.parents[-2:]], ["form", "div"])
        self.assertEqual((select.parents[-2].attrs["id"], select.parents[-1].attrs["class"]), ("cust", "field-tag"))
        options = [e.attrs["value"] for e in parser.elements if e.tag == "option"]
        self.assertIn(RULE["then"], options)
        self.assertIn(RULE["else"], options)

    def test_network_postconditions_match_api_routes(self):
        posts = {s["step_id"]: s["post"][0]["value"] for s in workflow_steps() if any(p["type"] == "network_2xx" for p in s["post"])}
        self.assertEqual(posts, {"s5_save_cust": "/api/customers/{{item}}", "s6_post_summary": "/api/team/notes"})
        crm_js = self.call("crm", "GET", "/static/crm.js")[2]
        portal_js = self.call("portal", "GET", "/static/portal.js")[2]
        self.assertIn('method: "PATCH"', crm_js)
        self.assertIn("/api/customers/${encodeURIComponent(recordId)}", crm_js)
        self.assertIn('fetch("/api/team/notes"', portal_js)
        self.assertEqual(self.call("crm", "PATCH", "/api/customers/1042", {"tag": RULE["else"]})[0], 200)
        self.assertEqual(self.call("portal", "POST", "/api/team/notes", {"body": "x"})[0], 201)

    def test_unknown_record_is_404(self):
        self.assertEqual(self.call("crm", "GET", "/customers/9999")[0], 404)
        self.assertEqual(self.call("crm", "GET", "/api/customers/9999")[0], 404)


class LocalOnlyTests(DemoServerTest):
    def test_refuses_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            create_server(AcmeCrm(Path(self.tmp.name) / "x.sqlite3"), host="0.0.0.0", port=0)

    def test_rejects_foreign_host_header(self):
        status, _, _ = self.call("crm", "GET", "/api/customers/1042", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)
        status, _, _ = self.call("crm", "GET", "/api/customers/1042", headers={"Host": "crm.acme.local"})
        self.assertEqual(status, 200)

    def test_security_headers_and_body_limit(self):
        status, headers, _ = self.call("portal", "GET", "/team/notes")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, _, _ = self.call("portal", "POST", "/api/team/notes", raw=b"x" * (16 * 1024 + 1))
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()
