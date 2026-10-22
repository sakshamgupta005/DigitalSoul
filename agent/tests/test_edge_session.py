"""Existing Edge session selection must never silently launch a fresh profile."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent.ghost.browser.edge_session import existing_edge_endpoint, validate_local_cdp
from agent.ghost.browser import shopify_actor


TARGET = "https://admin.shopify.com/store/gzn0vr-qn/products/new"


@pytest.fixture(autouse=True)
def no_configured_endpoint(monkeypatch):
    monkeypatch.delenv("DIGITALSOUL_EDGE_CDP_URL", raising=False)
    monkeypatch.delenv("DIGITALSOUL_EDGE_USER_DATA_DIR", raising=False)


def test_discovers_permission_enabled_websocket_without_http_probe(tmp_path):
    (tmp_path / "DevToolsActivePort").write_text("52341\n/devtools/browser/test-session\n")
    assert existing_edge_endpoint(tmp_path) == "ws://127.0.0.1:52341/devtools/browser/test-session"


def test_missing_endpoint_explains_required_edge_setting(tmp_path):
    with pytest.raises(RuntimeError, match="edge://inspect/#remote-debugging"):
        existing_edge_endpoint(tmp_path)


@pytest.mark.parametrize("content", ["0\n/devtools/browser/test", "65536\n/devtools/browser/test",
                                       "9222\nhttps://example.org", "9222\n/devtools/browser/test?x=y"])
def test_rejects_malformed_discovery(tmp_path, content):
    (tmp_path / "DevToolsActivePort").write_text(content)
    with pytest.raises((RuntimeError, ValueError)):
        existing_edge_endpoint(tmp_path)


@pytest.mark.parametrize("endpoint", ["ws://external.example:9222/devtools/browser/test",
                                        "ws://user:pass@localhost:9222/test", "http://localhost"])
def test_rejects_nonlocal_or_credentialed_connections(endpoint):
    with pytest.raises(ValueError):
        validate_local_cdp(endpoint)


def test_reuses_open_product_page_and_disconnects_without_closing_edge(monkeypatch):
    async def check():
        page = AsyncMock()
        page.get_url.return_value = TARGET
        page.evaluate.return_value = json.dumps({"steps": [{"field": "title"}], "errors": []})
        browser = AsyncMock()
        browser.get_pages.return_value = [page]
        options = []
        def connect(**kwargs):
            options.append(kwargs)
            return browser
        monkeypatch.setattr(shopify_actor, "Browser", connect)
        monkeypatch.setattr(shopify_actor, "existing_edge_endpoint", lambda: "ws://127.0.0.1:1234/devtools/browser/test")
        actor = shopify_actor.ShopifyBrowserActor(headless=False)
        try:
            result = await actor.execute_product_creation({"store_url": TARGET, "title": "TEST - unsaved"})
            assert result["ok"] and result["status"] == "filled" and result["save"] == {}
            assert options[0]["cdp_url"].startswith("ws://127.0.0.1:1234/")
            assert options[0]["keep_alive"] is True
            assert "user_data_dir" not in options[0]
            browser.new_page.assert_not_awaited()
            page.goto.assert_not_awaited()
            assert page.evaluate.await_count == 1
        finally:
            await actor.close()
        browser.stop.assert_awaited_once()
        browser.kill.assert_not_awaited()
    asyncio.run(check())


def test_missing_discovery_never_creates_isolated_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("DIGITALSOUL_EDGE_USER_DATA_DIR", str(tmp_path))
    browser = AsyncMock()
    monkeypatch.setattr(shopify_actor, "Browser", browser)
    with pytest.raises(RuntimeError, match="regular Edge"):
        asyncio.run(shopify_actor.ShopifyBrowserActor().start())
    browser.assert_not_called()
