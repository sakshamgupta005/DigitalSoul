"""browser-use Actor adapter tests, plus an opt-in real local-browser smoke test."""
import asyncio
import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.ghost.drivers.browser import BrowserDriver
from agent.ghost.drivers.browser_network import BrowserNetwork
from agent.ghost.execution.errors import GhostError
from agent.ghost.execution.recovery import RunStore
from agent.ghost.execution.runtime import Runtime
from agent.ghost.safety.policy import acme_policy
from .helpers import finish, workflow


class ActorElement:
    def __init__(self, node, testid, tag="SPAN", name="", text="", value=None, attrs=None, clicked=None):
        self.node, self.attrs, self.clicked = node, attrs or {}, clicked
        self.data = {"testid": testid, "tag": tag, "name": name, "text": text, "value": value,
                     "visible": True, "disabled": False, "type": None}

    async def evaluate(self, function, *args):
        return json.dumps(self.data)

    async def get_basic_info(self):
        return {"backendNodeId": self.node}

    async def get_attribute(self, name):
        return self.attrs.get(name)

    async def fill(self, text):
        self.data["value"] = text

    async def select_option(self, text):
        self.data["value"] = text

    async def click(self):
        self.clicked()


class ActorPage:
    def __init__(self, server, identity):
        self.server, self._target_id = server, identity
        self.url, self.elements = "about:blank", []

    async def goto(self, url):
        self.url = url
        if "/reports/overdue" in url:
            self.elements = [ActorElement(1, "overdue-account-list", attrs={"data-account-ids": '["ACC-1","ACC-2"]'})]
        elif "/customers/" in url:
            account = url.rsplit("/", 1)[1]
            record = self.server.records[account]
            tag = ActorElement(4, "tag-select", "SELECT", name="Customer Tag", value=record["tag"])
            self.elements = [ActorElement(1, "customer-record-id", text=account),
                             ActorElement(2, "customer-plan", text=record["plan"]),
                             ActorElement(3, "last-login-days", text=str(record["days"])), tag,
                             ActorElement(5, "save-btn", "BUTTON", name="Save", clicked=lambda: record.update(tag=tag.data["value"]))]
        elif "/team/notes" in url:
            draft = ActorElement(1, "team-note-body", "TEXTAREA", value="")
            self.elements = [draft, ActorElement(2, "post-team-note-btn", "BUTTON", name="Post Weekly Summary",
                             clicked=lambda: self.server.notes.append({"body": draft.data["value"]}))]

    async def get_url(self):
        return self.url

    async def evaluate(self, function, *args):
        return "complete" if "readyState" in function else "2"

    async def get_elements_by_css_selector(self, selector):
        if selector.startswith("[data-testid="):
            tid = json.loads(selector[len("[data-testid="):-1])
            return [e for e in self.elements if e.data["testid"] == tid]
        if selector == "select#tag":
            return [e for e in self.elements if e.data["testid"] == "tag-select"]
        if selector.startswith("button,"):
            return [e for e in self.elements if e.data["tag"] == "BUTTON"]
        if selector.startswith("select,"):
            return [e for e in self.elements if e.data["tag"] == "SELECT"]
        return self.elements


class ActorSession:
    def __init__(self):
        self.pages = []
        self.records = {"ACC-1": {"plan": "Pro", "days": 45, "tag": ""},
                        "ACC-2": {"plan": "Basic", "days": 50, "tag": ""}}
        self.notes = []

    async def start(self):
        pass

    async def new_page(self):
        page = ActorPage(self, str(len(self.pages)))
        self.pages.append(page)
        return page

    async def read_json(self, url):
        return copy.deepcopy(self.records[url.rsplit("/", 1)[1]] if "/api/customers/" in url else self.notes)


class ActorMonitor:
    async def start(self):
        pass

    async def attach(self, page):
        pass

    async def mutation(self, page, url, click):
        await click()
        return True

    async def close(self):
        pass


def test_actor_driver_executes_workflow_without_unrestricted_agent(tmp_path):
    async def check():
        session = ActorSession()
        policy = acme_policy()
        driver = BrowserDriver(policy, session=session, monitor=ActorMonitor(), read_json=session.read_json)
        runtime = Runtime(driver, policy, RunStore(tmp_path / "actor.sqlite3"))
        runtime.register(workflow())
        initial = await runtime.start("wf_at_risk_seg", 4, {}, "supervised", "actor")
        result = await finish(runtime, initial["run_id"])
        assert result["state"] == "succeeded", result.get("error")
        assert session.records["ACC-1"]["tag"] == "At-risk – High"
        assert session.records["ACC-2"]["tag"] == "At-risk – Watch"
        assert len(session.notes) == 1
        await runtime.close()
        runtime.store.close()
    asyncio.run(check())


def test_actor_resolver_rejects_inconsistent_alternatives():
    async def check():
        session = ActorSession()
        page = await session.new_page()
        await page.goto("http://crm.acme.local/customers/ACC-1")
        driver = BrowserDriver(acme_policy(), session=session, monitor=ActorMonitor())
        with pytest.raises(GhostError) as exc:
            await driver.resolve(page, [{"by": "testid", "value": "save-btn"}, {"by": "testid", "value": "tag-select"}])
        assert exc.value.code == "LOCATOR_AMBIGUOUS"
    asyncio.run(check())


def test_cdp_monitor_requires_fresh_matching_mutation_request():
    async def check():
        monitor = BrowserNetwork(None, acme_policy())
        monitor.sessions, monitor.targets = {"tab": "sid"}, {"sid": "tab"}
        monitor.attach = AsyncMock(return_value="sid")
        page = SimpleNamespace(_target_id="tab")
        url = "http://crm.acme.local/api/customers/ACC-1"

        async def click():
            monitor.request_started({"requestId": "get", "request": {"url": url, "method": "GET"}}, "sid")
            monitor.response_received({"requestId": "get", "response": {"url": url, "status": 200}}, "sid")
            assert not monitor.pending["tab"]["future"].done()
            monitor.request_started({"requestId": "write", "request": {"url": url, "method": "PATCH"}}, "sid")
            monitor.response_received({"requestId": "write", "response": {"url": url, "status": 200}}, "sid")

        assert await monitor.mutation(page, url, click)
        assert not monitor.pending
        monitor.client = SimpleNamespace(send=SimpleNamespace(Fetch=SimpleNamespace(failRequest=AsyncMock(), continueRequest=AsyncMock())))
        await monitor.filter_request({"requestId": "evil", "request": {"url": "https://evil.example/"}}, "sid")
        monitor.client.send.Fetch.failRequest.assert_awaited_once()
        monitor.client.send.Fetch.continueRequest.assert_not_awaited()
    asyncio.run(check())


@pytest.mark.skipif(os.environ.get("DIGITALSOUL_TEST_NO_IO") == "1", reason="Requires OS async IO and a local Chrome/Edge installation")
def test_real_browser_use_actor_connection(tmp_path):
    async def check():
        from browser_use import Browser
        from agent.ghost.drivers.browser import browser_executable
        browser = Browser(user_data_dir=str(tmp_path / "profile"), headless=True, is_local=True,
                          use_cloud=False, executable_path=browser_executable(),
                          enable_default_extensions=False, auto_download_pdfs=False,
                          downloads_path=str(tmp_path / "downloads"))
        try:
            await browser.start()
            page = await browser.new_page()
            assert await page.evaluate("() => 1 + 1") == "2"
        finally:
            await browser.kill()
    asyncio.run(check())
