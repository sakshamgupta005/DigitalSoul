"""Focused test suite for DigitalSoul's Self-Healing Execution Engine.

Tests:
- Obstacle categorization & safe action discovery
- Strict safeguards (refusal to bypass authentication/payment/security)
- Bounded retry budget enforcement
- Loop prevention via obstacle signatures
- Distinction between attempted, completed, and verified actions
- Event emission for HUD overlay
"""

import asyncio
import json
import pytest

from agent.ghost.desktop.self_healing import (
    ObstacleCategory,
    ObstacleDescriptor,
    RecoveryState,
    RecoveryStatus,
    compute_signature,
    heal_browser_page,
    is_security_boundary,
    scan_browser_obstacles,
)


def test_obstacle_signature_determinism():
    sig1 = compute_signature("cookie_banner", "Cookie Preferences Accept All")
    sig2 = compute_signature("cookie_banner", "cookie preferences accept all")
    sig3 = compute_signature("cookie_banner", "Different Text")
    assert sig1 == sig2
    assert sig1 != sig3
    assert sig1.startswith("cookie_banner:")


def test_strict_security_safeguards_detection():
    # Authentication & credentials
    is_sec, reason = is_security_boundary("Please enter your admin password to continue.")
    assert is_sec is True
    assert "password" in reason.lower()

    # Two-factor authentication
    is_sec, reason = is_security_boundary("Enter 6-digit 2FA OTP code")
    assert is_sec is True

    # Payment confirmation
    is_sec, reason = is_security_boundary("Authorize payment of $450 to proceed with checkout")
    assert is_sec is True
    assert "authorize payment" in reason.lower()

    # CAPTCHA
    is_sec, reason = is_security_boundary("Please solve the Cloudflare turnstile captcha")
    assert is_sec is True

    # Windows UAC / Admin
    is_sec, reason = is_security_boundary("User Account Control: Do you want to allow this app...")
    assert is_sec is True

    # Benign cookie banner is NOT a security boundary
    is_sec, reason = is_security_boundary("We use cookies to improve your browsing experience. Accept all.")
    assert is_sec is False
    assert reason is None


def test_recovery_state_retry_budget_and_loop_prevention():
    state = RecoveryState(max_retries=2)
    sig_cookie = "cookie_banner:abc123"

    # First attempt should be allowed
    can_retry, err = state.can_retry(sig_cookie)
    assert can_retry is True
    assert err is None

    # Record first attempt
    desc = ObstacleDescriptor(
        category=ObstacleCategory.COOKIE_BANNER,
        label="Cookie Banner",
        element_selector="#cookie-consent",
        dismiss_selector="#accept-cookies-btn",
        dismiss_text="Accept All",
        is_safe=True,
        signature=sig_cookie,
    )
    state.record_attempt(desc, RecoveryStatus.RESOLVED)
    assert state.attempt_count == 1
    assert sig_cookie in state.seen_signatures

    # Second encounter of IDENTICAL signature -> loop prevention triggers!
    can_retry, err = state.can_retry(sig_cookie)
    assert can_retry is False
    assert "Loop prevented" in err

    # Different obstacle signature -> allowed
    sig_promo = "newsletter_popup:def456"
    can_retry, err = state.can_retry(sig_promo)
    assert can_retry is True

    # Record second attempt
    desc2 = ObstacleDescriptor(
        category=ObstacleCategory.NEWSLETTER_POPUP,
        label="Newsletter Popup",
        element_selector="#promo-modal",
        dismiss_selector="#close-btn",
        dismiss_text="Dismiss",
        is_safe=True,
        signature=sig_promo,
    )
    state.record_attempt(desc2, RecoveryStatus.RESOLVED)
    assert state.attempt_count == 2

    # Third attempt exceeds budget (max_retries=2)
    sig_third = "modal:ghi789"
    can_retry, err = state.can_retry(sig_third)
    assert can_retry is False
    assert "Retry limit exceeded (2/2)" in err


class MockPage:
    """Mock browser page simulating evaluate calls."""

    def __init__(self, obstacle_responses, dismiss_fn=None):
        self.obstacle_responses = list(obstacle_responses)
        self.dismiss_fn = dismiss_fn
        self.evaluated_scripts = []

    async def evaluate(self, script, *args):
        self.evaluated_scripts.append(script)
        if "btn.click()" in str(script):
            if self.dismiss_fn:
                self.dismiss_fn()
            return True
        if self.obstacle_responses:
            return self.obstacle_responses.pop(0)
        return {"detected": False}


@pytest.mark.anyio
async def test_heal_browser_page_recovers_cookie_banner():
    events = []

    # Initial scan finds cookie banner; post-dismissal scan confirms cleared
    initial_scan = {
        "detected": True,
        "category": "cookie_banner",
        "label": "Cookie & Privacy Consent",
        "selector": "#cookie-consent-overlay",
        "dismiss_selector": "#accept-cookies-btn",
        "dismiss_text": "Accept All Cookies",
        "safe": True,
        "rect": {"left": 200, "top": 150, "width": 400, "height": 200},
        "raw_text": "We use cookies. Accept all cookies.",
    }
    post_scan = {"detected": False}

    page = MockPage([initial_scan, post_scan])
    state = RecoveryState(max_retries=2)

    status, msg = await heal_browser_page(page, event_sink=events.append, state=state)

    assert status == RecoveryStatus.RESOLVED
    assert "Successfully dismissed" in msg
    assert state.attempt_count == 1

    # Verify event emission sequence
    event_names = [e["event"] for e in events]
    assert event_names == [
        "hud_obstacle",
        "hud_recovering",
        "hud_retrying_verification",
        "hud_acting",
    ]
    # Check that rect was passed for HUD reticle
    assert events[0]["rect"] == {"left": 200, "top": 150, "width": 400, "height": 200}
    assert events[0]["recoverable"] is True
    assert "Dismissing Cookie & Privacy Consent" in events[1]["status"]


@pytest.mark.anyio
async def test_heal_browser_page_refuses_security_boundary():
    events = []

    # Page contains an authorization / payment password overlay
    security_scan = {
        "detected": True,
        "category": "security_boundary",
        "label": "Payment Authorization",
        "selector": "#security-auth-overlay",
        "dismiss_selector": "#authorize-payment-btn",
        "dismiss_text": "Authorize Payment",
        "safe": False,
        "rect": {"left": 300, "top": 200, "width": 450, "height": 300},
        "raw_text": "Authorize payment of $450 and enter password to confirm",
    }

    page = MockPage([security_scan])
    state = RecoveryState(max_retries=2)

    status, reason = await heal_browser_page(page, event_sink=events.append, state=state)

    assert status == RecoveryStatus.BLOCKED_SECURITY
    assert "Security boundary" in reason
    # Click script must NOT have been called
    assert not any("btn.click()" in s for s in page.evaluated_scripts)

    # Verify HUD events reflect blocked security state
    event_names = [e["event"] for e in events]
    assert event_names == ["hud_obstacle", "hud_paused_for_help"]
    assert "security boundary" in events[1]["reason"].lower()


@pytest.mark.anyio
async def test_heal_browser_page_detects_persistent_obstacle():
    events = []

    # Obstacle scan continues to report identical obstacle after attempted click
    persistent_scan = {
        "detected": True,
        "category": "cookie_banner",
        "label": "Persistent Banner",
        "selector": "#broken-banner",
        "dismiss_selector": "#broken-btn",
        "dismiss_text": "Accept",
        "safe": True,
        "raw_text": "Accept cookies",
    }

    page = MockPage([persistent_scan, persistent_scan])
    state = RecoveryState(max_retries=2)

    status, reason = await heal_browser_page(page, event_sink=events.append, state=state)

    assert status == RecoveryStatus.UNRECOVERABLE
    assert "remained after attempted dismissal" in reason
    assert events[-1]["event"] == "hud_paused_for_help"


import threading
import time
import uvicorn
from agent.ghost.app import app
from agent.ghost.browser.shopify import ShopifyProduct
from agent.ghost.browser.shopify_actor import ShopifyBrowserActor


@pytest.fixture(scope="module")
def local_server():
    """Spawns background test server for Shopify test form."""
    config = uvicorn.Config(app, host="127.0.0.1", port=7796, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "Local practice server failed to start"
    yield "http://127.0.0.1:7796"
    server.should_exit = True
    thread.join(timeout=5)


def test_self_healing_browser_use_cookie_recovery(local_server, tmp_path):
    """End-to-end test of self-healing resolving simulated cookie banner in browser-use."""
    pytest.importorskip("browser_use")
    events = []

    async def run():
        product = ShopifyProduct(
            title="Self-Healing Tested Jacket",
            description="<p>Automated verification with obstacle recovery.</p>",
            price="129.99",
            sku="HEAL-JKT-001",
            status="Active",
            store_url=f"{local_server}/api/v1/shopify/test-form?simulate_popup=cookie",
        )
        actor = ShopifyBrowserActor(headless=True, profile_dir=str(tmp_path / "heal_profile"))
        try:
            report = await actor.execute_product_creation(product.to_dict(), click_save=True, event_sink=events.append)
            assert report["ok"] is True
            assert report["save"].get("verified") is True
            assert report["save"]["product"]["title"] == product.title

            # Confirm obstacle was detected and healed via HUD events
            event_types = [e["event"] for e in events]
            assert "hud_obstacle" in event_types
            assert "hud_recovering" in event_types
            assert "hud_retrying_verification" in event_types
        finally:
            await actor.close()

    asyncio.run(run())


def test_self_healing_browser_use_refuses_security_modal(local_server, tmp_path):
    """Verifies that security / payment confirmation popups are NEVER automatically dismissed."""
    pytest.importorskip("browser_use")
    events = []

    async def run():
        product = ShopifyProduct(
            title="Unauthorized Security Product",
            price="49.99",
            sku="SEC-PROD-001",
            status="Active",
            store_url=f"{local_server}/api/v1/shopify/test-form?simulate_popup=security",
        )
        actor = ShopifyBrowserActor(headless=True, profile_dir=str(tmp_path / "sec_profile"))
        try:
            report = await actor.execute_product_creation(product.to_dict(), click_save=True, event_sink=events.append)
            # Must fail / block
            assert report["ok"] is False
            assert report["status"] == "blocked_security"
            assert any("security boundary" in err.lower() for err in report["errors"])

            # Verify HUD received paused for help
            event_types = [e["event"] for e in events]
            assert "hud_obstacle" in event_types
            assert "hud_paused_for_help" in event_types
        finally:
            await actor.close()

    asyncio.run(run())


class MockActorPage:
    def __init__(self, obstacle_info):
        self.obstacle_info = obstacle_info
        self.healed = False
        self.url = "http://127.0.0.1:7777/api/v1/shopify/test-form"

    async def get_url(self):
        return self.url

    async def goto(self, url):
        self.url = url

    async def evaluate(self, script, *args):
        # 1. Obstacle scan JS
        if "candidateDialogs" in str(script) or "BROWSER_OBSTACLE_SCAN_JS" in str(script):
            if not self.healed:
                return self.obstacle_info
            return {"detected": False}
        # 2. Saved product check
        if "data-saved-product" in str(script):
            return json.dumps({"title": "Healing Test", "price": "99.00", "sku": "SKU-HEAL-1", "status": "Active"})
        # 3. CLICK_SAVE_SCRIPT
        if "product-save-btn" in str(script) or "CLICK_SAVE_SCRIPT" in str(script):
            return json.dumps({"ok": True, "clicked": "Save"})
        # 4. Dismiss button click
        if "btn.click()" in str(script):
            self.healed = True
            return True
        # 5. FILL_SCRIPT
        if "const report = { steps:" in str(script):
            return json.dumps({
                "ok": True,
                "errors": [],
                "steps": [
                    {"field": "title", "value": "Healing Test"},
                    {"field": "price", "value": "99.00"},
                    {"field": "sku", "value": "SKU-HEAL-1"},
                    {"field": "status", "value": "ACTIVE"}
                ]
            })
        return None


class MockBrowser:
    def __init__(self, page):
        self._page = page

    async def get_pages(self):
        return [self._page]

    async def new_page(self):
        return self._page

    async def start(self):
        pass

    async def stop(self):
        pass

    async def kill(self):
        pass


@pytest.mark.anyio
async def test_actor_execute_recovers_cookie_banner():
    events = []
    cookie_info = {
        "detected": True,
        "category": "cookie_banner",
        "label": "Cookie Consent Banner",
        "selector": "#cookie-consent-overlay",
        "dismiss_selector": "#accept-cookies-btn",
        "dismiss_text": "Accept All",
        "safe": True,
        "rect": {"left": 250, "top": 180, "width": 420, "height": 220},
        "raw_text": "Accept cookies"
    }
    mock_page = MockActorPage(cookie_info)
    actor = ShopifyBrowserActor(headless=True)
    actor.browser = MockBrowser(mock_page)

    product_data = {
        "title": "Healing Test",
        "price": "99.00",
        "sku": "SKU-HEAL-1",
        "status": "Active",
        "store_url": "http://127.0.0.1:7777/api/v1/shopify/test-form"
    }

    report = await actor.execute_product_creation(product_data, click_save=True, event_sink=events.append)
    assert report["ok"] is True
    assert report["save"].get("verified") is True
    assert mock_page.healed is True

    # Check that HUD events were dispatched
    event_names = [e["event"] for e in events]
    assert "hud_obstacle" in event_names
    assert "hud_recovering" in event_names
    assert "hud_retrying_verification" in event_names
    assert "hud_acting" in event_names


@pytest.mark.anyio
async def test_actor_execute_blocks_security_modal():
    events = []
    sec_info = {
        "detected": True,
        "category": "security_boundary",
        "label": "Admin Verification Required",
        "selector": "#security-auth-overlay",
        "dismiss_selector": "#authorize-payment-btn",
        "dismiss_text": "Authorize Payment",
        "safe": False,
        "rect": {"left": 250, "top": 180, "width": 420, "height": 220},
        "raw_text": "Enter password to authorize payment of $450"
    }
    mock_page = MockActorPage(sec_info)
    actor = ShopifyBrowserActor(headless=True)
    actor.browser = MockBrowser(mock_page)

    product_data = {
        "title": "Healing Test",
        "price": "99.00",
        "sku": "SKU-HEAL-1",
        "status": "Active",
        "store_url": "http://127.0.0.1:7777/api/v1/shopify/test-form"
    }

    report = await actor.execute_product_creation(product_data, click_save=True, event_sink=events.append)
    assert report["ok"] is False
    assert report["status"] == "blocked_security"
    assert "security boundary" in report["errors"][0].lower()
    assert mock_page.healed is False

    event_names = [e["event"] for e in events]
    assert "hud_obstacle" in event_names
    assert "hud_paused_for_help" in event_names


