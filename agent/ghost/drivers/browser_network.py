"""Separate CDP observer: do not replace browser-use's single-listener handlers."""
import asyncio
from urllib.parse import urlsplit

import httpx

from ..execution.errors import GhostError


class BrowserNetwork:
    def __init__(self, browser, policy):
        self.browser, self.policy = browser, policy
        self.client = None
        self.sessions, self.targets, self.pending = {}, {}, {}
        self.tasks = set()
        self.failure = None

    async def start(self):
        from cdp_use import CDPClient
        root = self.browser.cdp_client
        self.client = CDPClient(root.url, additional_headers=getattr(root, "additional_headers", None))
        await self.client.start()
        self.client.register.Fetch.requestPaused(self.request_paused)
        self.client.register.Network.requestWillBeSent(self.request_started)
        self.client.register.Network.responseReceived(self.response_received)

    async def attach(self, page):
        # Compatibility seam pinned to browser-use 0.13.11's Actor Page.
        target = page._target_id
        if target not in self.sessions:
            result = await self.client.send.Target.attachToTarget(params={"targetId": target, "flatten": True})
            sid = result["sessionId"]
            self.sessions[target], self.targets[sid] = sid, target
            await self.client.send.Network.enable(session_id=sid)
            await self.client.send.Fetch.enable(params={"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}, session_id=sid)
        return self.sessions[target]

    def request_paused(self, event, session_id=None):
        # Do not await a CDP command inside its own dispatch reader.
        task = asyncio.create_task(self.filter_request(event, session_id))
        self.tasks.add(task)

        def done(value):
            self.tasks.discard(value)
            if not value.cancelled() and value.exception():
                self.failure = "Network interception failed"

        task.add_done_callback(done)

    async def filter_request(self, event, sid):
        parsed = urlsplit(event.get("request", {}).get("url", ""))
        allowed = {app.origin for app in self.policy.apps.values()}
        if sid not in self.targets or f"{parsed.scheme}://{parsed.netloc}" not in allowed:
            await self.client.send.Fetch.failRequest(params={"requestId": event["requestId"], "errorReason": "BlockedByClient"}, session_id=sid)
        else:
            await self.client.send.Fetch.continueRequest(params={"requestId": event["requestId"]}, session_id=sid)

    def request_started(self, event, session_id=None):
        pending = self.pending.get(self.targets.get(session_id))
        request = event.get("request", {})
        if (pending and request.get("url") == pending["url"] and request.get("method") in ("POST", "PUT", "PATCH")
                and "redirectResponse" not in event):
            pending["ids"].add(event["requestId"])

    def response_received(self, event, session_id=None):
        pending = self.pending.get(self.targets.get(session_id))
        response = event.get("response", {})
        if (pending and not pending["future"].done() and event.get("requestId") in pending["ids"]
                and response.get("url") == pending["url"]):
            pending["future"].set_result(200 <= response.get("status", 0) < 300)

    async def mutation(self, page, url, click):
        if self.failure:
            raise GhostError("GUARD_TRIGGERED", self.failure)
        await self.attach(page)
        target = page._target_id
        if target in self.pending:
            raise GhostError("CONFLICT", "A mutation is already pending for this tab")
        future = asyncio.get_running_loop().create_future()
        self.pending[target] = {"url": url, "future": future, "ids": set()}
        try:
            await click()
            return await asyncio.wait_for(future, 10)
        finally:
            self.pending.pop(target, None)
            if not future.done():
                future.cancel()

    async def cookies(self, page, url):
        sid = await self.attach(page)
        result = await self.client.send.Network.getCookies(params={"urls": [url]}, session_id=sid)
        jar = httpx.Cookies()
        for cookie in result.get("cookies", []):
            jar.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie.get("path", "/"))
        return jar

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.client:
            await self.client.stop()
