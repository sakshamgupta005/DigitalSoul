import asyncio
import copy
import json
from pathlib import Path

from agent.ghost.execution.recovery import RunStore
from agent.ghost.execution.runtime import Runtime
from agent.ghost.safety.policy import acme_policy


def workflow():
    return json.loads((Path(__file__).resolve().parents[2] / "contracts/fixtures/workflow.valid.json").read_text(encoding="utf-8"))


class DemoDriver:
    """Deterministic app boundary for runtime tests, not a production driver."""
    def __init__(self):
        self.ids = ["ACC-1", "ACC-2"]
        self.records = {"ACC-1": {"plan": "Pro", "days": 45, "tag": ""},
                        "ACC-2": {"plan": "Basic", "days": 50, "tag": ""}}
        self.urls = {}
        self.record = None
        self.draft = ""
        self.tag = ""
        self.notes = []
        self.calls = []
        self.responses = set()
        self.fail_save = False
        self.block = None

    async def prepare(self, step):
        return {"url": self.urls.get(step["app"]), "record": self.record,
                "tag": self.tag, "draft": self.draft,
                "target": step["locators"][:1]}

    async def check(self, app, condition, locators):
        kind, value = condition["type"], condition.get("value")
        if kind == "url_matches":
            from urllib.parse import urlsplit
            return urlsplit(self.urls.get(app, "")).path == value
        if kind == "record_id_matches":
            return self.record == value
        if kind == "element_visible":
            return True
        if kind == "field_equals":
            tid = locators[condition["locator_ref"]]["value"]
            return (self.tag if tid == "tag-select" else self.draft) == value
        return value in self.responses

    async def perform(self, step, target):
        self.calls.append(copy.deepcopy(step))
        if self.block:
            await self.block.wait()
        verb, sid = step.get("verb"), step["step_id"]
        if verb == "navigate":
            self.urls[step["app"]] = step["input"]
            if "/customers/" in step["input"]:
                self.record = step["input"].rsplit("/", 1)[1]
                self.tag = self.records[self.record]["tag"]
        elif verb == "read_field":
            return {"account_ids": self.ids, "plan": self.records.get(self.record, {}).get("plan"),
                    "last_login_days": self.records.get(self.record, {}).get("days")}[step["output_name"]]
        elif verb in ("set_field", "select_option"):
            if step["app"] == "acme-crm":
                self.tag = step["input"]
            else:
                self.draft = step["input"]
        elif sid == "s5_save_cust":
            self.records[self.record]["tag"] = self.tag
            if self.fail_save:
                raise TimeoutError("Response lost after write")
            self.responses.add("/api/customers/" + self.record)
        elif sid == "s6_post_summary":
            self.notes.append(self.draft)
            self.responses.add("/api/team/notes")

    async def outcome(self, check):
        if check["check"] == "tag_equals_for_all":
            return all(self.records[k]["tag"] == v for k, v in check["args"]["expected_by_id"].items())
        return self.notes.count(check["args"]["containing"]) == 1

    async def close(self):
        pass

    async def reconcile(self, intent):
        if intent["step_id"] == "s5_save_cust":
            return self.records[intent["target"]["record"]]["tag"] == intent["target"]["tag"]
        if intent["step_id"] == "s6_post_summary":
            return self.notes.count(intent["target"]["draft"]) == 1
        return False

    async def restore_navigation(self, navigation):
        self.urls.update(navigation)
        if "acme-crm" in navigation:
            self.record = navigation["acme-crm"].rsplit("/", 1)[1]
            self.tag = self.records[self.record]["tag"]


def runtime(tmp_path, driver=None):
    driver = driver or DemoDriver()
    value = Runtime(driver, acme_policy(), RunStore(tmp_path / "runs.sqlite3"))
    value.register(workflow())
    return value


async def settle(value, rid):
    await value.tasks[rid]
    return value.store.get(rid)


async def finish(value, rid):
    for _ in range(30):
        run = await settle(value, rid)
        if run["state"] == "awaiting_approval":
            binding = {key: run["pending_approval"][key] for key in ("approval_id", "step_id", "workflow_version", "effect_hash")}
            await value.control(rid, "approve", binding)
        else:
            return run
    raise AssertionError("Workflow did not terminate")
