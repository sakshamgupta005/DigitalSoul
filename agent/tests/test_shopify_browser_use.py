"""Tests for Shopify product automation with browser-use."""

import asyncio
import json
import pytest
import uvicorn
import threading
import time
from pathlib import Path

from agent.ghost.browser.shopify import generate_product_fields, ShopifyProduct
from agent.ghost.browser.shopify_actor import ShopifyBrowserActor
from agent.ghost.app import app


@pytest.fixture(scope="module")
def local_server():
    """Spawns background test server for Shopify test form."""
    config = uvicorn.Config(app, host="127.0.0.1", port=7799, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "Local practice server failed to start"
    yield "http://127.0.0.1:7799"
    server.should_exit = True
    thread.join(timeout=5)


def test_shopify_ai_field_generation():
    """Tests that AI/heuristic generator fills all necessary fields from minimal input."""
    minimal_input = {
        "title": "Minimalist Wireless Mechanical Keyboard",
        "price": "89.00",
    }
    result = generate_product_fields(minimal_input)

    assert result["title"] == "Minimalist Wireless Mechanical Keyboard"
    assert "Keyboard" in result["sku"] or "MINI" in result["sku"]
    assert float(result["price"]) == 89.00
    assert float(result["compare_at_price"]) > 89.00
    assert float(result["cost_per_item"]) < 89.00
    assert "Electronics" in result["category"]
    assert "Features" in result["description"] or "<ul>" in result["description"]
    assert len(result["tags"]) > 0
    assert result["status"] == "Active"


def test_browser_use_shopify_product_automation(local_server, tmp_path):
    """Tests browser-use navigating, filling, and submitting the Shopify product form."""
    pytest.importorskip("browser_use")
    async def run_automation():
        product = ShopifyProduct(
            title="Bamboo Ergonomic Laptop Stand",
            description="<p>Crafted from 100% natural organic bamboo for posture relief.</p>",
            price="49.99",
            compare_at_price="69.99",
            cost_per_item="18.50",
            sku="BAMB-LAP-001",
            barcode="789012345678",
            inventory_quantity=80,
            category="Office Supplies > Desk Accessories",
            vendor="GZN Store",
            tags="bamboo, ergonomic, laptop-stand, eco-friendly",
            status="Active",
            store_url=f"{local_server}/api/v1/shopify/test-form",
        )

        actor = ShopifyBrowserActor(headless=True, profile_dir=str(tmp_path / "browser_profile"))
        try:
            report = await actor.execute_product_creation(product.to_dict(), click_save=True)
            assert report["ok"] is True
            assert len(report["filled_steps"]) >= 7
            assert report["save"].get("ok") is True

            saved = report["save"]["product"]
            assert report["save"]["verified"] is True
            assert saved["title"] == product.title
            assert saved["price"] == product.price
            assert saved["sku"] == product.sku
            assert saved["status"] == product.status
            raw = await actor.page.evaluate("() => document.body.getAttribute('data-saved-product')")
            assert json.loads(raw) == saved
        finally:
            await actor.close()

    asyncio.run(run_automation())
