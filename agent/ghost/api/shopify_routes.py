"""Shopify automation API routes and test harness.

Allows the Notch desktop client and background workers to generate product fields
using AI and execute them through browser-use.
"""

from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent.ghost.browser.shopify import generate_product_fields
from agent.ghost.browser.shopify_actor import ShopifyBrowserActor
from agent.ghost.browser.shopify_session import ShopifySession


@asynccontextmanager
async def shopify_lifespan(app):
    app.state.shopify_sessions = {}
    try:
        yield
    finally:
        for session in app.state.shopify_sessions.values():
            await asyncio.to_thread(session.close)


router = APIRouter(prefix="/api/v1/shopify", tags=["shopify"], lifespan=shopify_lifespan)


class GenerateRequest(BaseModel):
    title: Optional[str] = ""
    prompt: Optional[str] = ""
    price: Optional[str] = "29.99"
    category: Optional[str] = None
    store_url: Optional[str] = "https://admin.shopify.com/store/gzn0vr-qn/products/new"


class RunRequest(BaseModel):
    product: Dict[str, Any]
    click_save: bool = False
    headless: bool = True
    cdp_url: Optional[str] = None


@router.post("/generate")
def generate_fields(req: GenerateRequest):
    """Generates complete Shopify product fields using AI / rule heuristics."""
    return generate_product_fields(req.model_dump())


@router.post("/run")
async def run_shopify(req: RunRequest, request: Request):
    """Executes browser-use product automation against the specified store URL."""
    sessions = request.app.state.shopify_sessions
    key = (req.cdp_url, req.headless)
    if key not in sessions:
        sessions[key] = ShopifySession(actor_factory=lambda headless: ShopifyBrowserActor(
            cdp_url=req.cdp_url, headless=headless))
    session = sessions[key]
    try:
        return await asyncio.wait_for(asyncio.wrap_future(session.submit(
            req.product, click_save=req.click_save, headless=req.headless)), timeout=170)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Shopify automation error: {exc}")


SHOPIFY_POLARIS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Products / Add product · Shopify Admin</title>
  <style>
    :root {
      --p-surface: #ffffff;
      --p-background: #f1f2f4;
      --p-text: #202223;
      --p-text-subdued: #6d7175;
      --p-border: #d2d5d8;
      --p-interactive: #008060;
      --p-interactive-hover: #006e52;
      --p-radius: 8px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "San Francisco", "Segoe UI", Roboto, sans-serif;
      background: var(--p-background);
      color: var(--p-text);
      line-height: 1.5;
      padding-bottom: 80px;
    }
    header {
      background: #1a1a1a;
      color: #fff;
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .badge { background: #303030; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
    .container {
      max-width: 980px;
      margin: 24px auto;
      padding: 0 16px;
    }
    .top-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }
    .top-bar h1 { font-size: 20px; font-weight: 600; }
    .card {
      background: var(--p-surface);
      border-radius: var(--p-radius);
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
      padding: 20px;
      margin-bottom: 20px;
    }
    .card h2 { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
    label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; color: var(--p-text); }
    input[type="text"], input[type="number"], select, textarea {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--p-border);
      border-radius: 6px;
      font-size: 14px;
      margin-bottom: 14px;
      outline: none;
    }
    input:focus, textarea:focus { border-color: var(--p-interactive); box-shadow: 0 0 0 1px var(--p-interactive); }
    .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .editor {
      min-height: 140px;
      border: 1px solid var(--p-border);
      border-radius: 6px;
      padding: 12px;
      font-size: 14px;
      background: #fafbfc;
      outline: none;
      margin-bottom: 14px;
    }
    .editor:focus { border-color: var(--p-interactive); background: #fff; }
    .btn-save {
      background: var(--p-interactive);
      color: #fff;
      border: none;
      padding: 10px 20px;
      border-radius: 6px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
    }
    .btn-save:hover { background: var(--p-interactive-hover); }
    .alert-success {
      background: #e3f1df;
      color: #1b5e20;
      border: 1px solid #c8e6c9;
      padding: 14px;
      border-radius: 6px;
      margin-bottom: 16px;
      display: none;
    }
  </style>
</head>
<body>
  <header>
    <div><strong>Shopify Admin</strong> · store: <code>gzn0vr-qn</code></div>
    <div class="badge">Products / New</div>
  </header>
  <div class="container">
    <div id="success-banner" class="alert-success">
      <strong>Product created successfully!</strong>
      <div id="saved-summary" style="font-size: 12px; margin-top: 4px;"></div>
    </div>
    <form id="product-form">
      <div class="top-bar">
        <h1>Add product</h1>
        <button id="product-save-btn" type="submit" class="btn-save">Save</button>
      </div>

      <div class="card">
        <h2>Title & Description</h2>
        <label for="product-title">Title</label>
        <input type="text" id="product-title" name="title" placeholder="Short sleeve t-shirt" required>

        <label for="product-description">Description</label>
        <div id="product-description" class="editor" contenteditable="true" placeholder="Product description, features, materials..."></div>
      </div>

      <div class="card">
        <h2>Pricing</h2>
        <div class="grid-3">
          <div>
            <label for="pricing-price">Price ($)</label>
            <input type="text" id="pricing-price" name="price" placeholder="0.00">
          </div>
          <div>
            <label for="pricing-compare-at">Compare-at price ($)</label>
            <input type="text" id="pricing-compare-at" name="compareAtPrice" placeholder="0.00">
          </div>
          <div>
            <label for="pricing-cost">Cost per item ($)</label>
            <input type="text" id="pricing-cost" name="cost" placeholder="0.00">
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Inventory</h2>
        <div class="grid-2">
          <div>
            <label for="inventory-sku">SKU (Stock Keeping Unit)</label>
            <input type="text" id="inventory-sku" name="sku" placeholder="PROD-001">
          </div>
          <div>
            <label for="inventory-quantity">Available quantity</label>
            <input type="number" id="inventory-quantity" name="quantity" placeholder="0">
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Product organization</h2>
        <div class="grid-2">
          <div>
            <label for="product-category">Category</label>
            <input type="text" id="product-category" name="category" placeholder="Apparel & Accessories">
          </div>
          <div>
            <label for="product-vendor">Vendor</label>
            <input type="text" id="product-vendor" name="vendor" placeholder="Vendor">
          </div>
          <div>
            <label for="product-tags">Tags (comma separated)</label>
            <input type="text" id="product-tags" name="tags" placeholder="vintage, summer, cotton">
          </div>
        </div>
        <div>
          <label for="product-status">Status</label>
          <select id="product-status" name="status">
            <option value="Active">Active</option>
            <option value="Draft">Draft</option>
          </select>
        </div>
      </div>
    </form>
  </div>

  <script>
    document.getElementById('product-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const title = document.getElementById('product-title').value;
      const desc = document.getElementById('product-description').innerHTML;
      const price = document.getElementById('pricing-price').value;
      const sku = document.getElementById('inventory-sku').value;
      const status = document.getElementById('product-status').value;

      const data = { title, desc, price, sku, status, timestamp: new Date().toISOString() };
      document.body.setAttribute('data-saved-product', JSON.stringify(data));

      const banner = document.getElementById('success-banner');
      banner.style.display = 'block';
      document.getElementById('saved-summary').textContent = `Saved: "${title}" - $${price} (SKU: ${sku}, Status: ${status})`;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  </script>
</body>
</html>
"""


COOKIE_OVERLAY_HTML = """
  <div id="cookie-consent-overlay" class="obstacle-overlay" style="position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 99999; display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);">
    <div style="background: #fff; border-radius: 8px; padding: 24px; max-width: 440px; text-align: center; box-shadow: 0 12px 32px rgba(0,0,0,0.3); font-family: -apple-system, sans-serif;">
      <div style="font-size: 28px; margin-bottom: 8px;">🍪</div>
      <h3 style="margin: 0 0 8px; color: #202223; font-size: 17px;">Cookie &amp; Privacy Preferences</h3>
      <p style="font-size: 13px; color: #6d7175; line-height: 1.4; margin-bottom: 18px;">We use essential and analytics cookies to enhance store staging automation. Accept cookies to continue.</p>
      <div style="display: flex; gap: 10px; justify-content: center;">
        <button id="accept-cookies-btn" type="button" onclick="document.getElementById('cookie-consent-overlay').remove();" style="background: #008060; color: #fff; border: none; border-radius: 4px; padding: 9px 18px; font-weight: 600; cursor: pointer;">Accept All Cookies</button>
        <button id="decline-cookies-btn" type="button" onclick="document.getElementById('cookie-consent-overlay').remove();" style="background: #f6f6f7; color: #202223; border: 1px solid #c9cccf; border-radius: 4px; padding: 9px 16px; cursor: pointer;">Decline Optional</button>
      </div>
    </div>
  </div>
"""

NEWSLETTER_OVERLAY_HTML = """
  <div id="newsletter-modal-overlay" class="obstacle-overlay" style="position: fixed; inset: 0; background: rgba(0,0,0,0.55); z-index: 99999; display: flex; align-items: center; justify-content: center;">
    <div style="background: #fff; border-radius: 8px; padding: 24px; max-width: 420px; text-align: center; box-shadow: 0 12px 32px rgba(0,0,0,0.3); position: relative; font-family: -apple-system, sans-serif;">
      <button id="dismiss-newsletter-btn" aria-label="Close" onclick="document.getElementById('newsletter-modal-overlay').remove();" style="position: absolute; top: 12px; right: 14px; border: none; background: transparent; font-size: 18px; cursor: pointer; color: #6d7175;">×</button>
      <h3 style="margin: 0 0 8px; color: #202223; font-size: 16px;">Supplier Drop Insights</h3>
      <p style="font-size: 13px; color: #6d7175; margin-bottom: 16px;">Get real-time wholesale updates directly in your inbox.</p>
      <button type="button" onclick="document.getElementById('newsletter-modal-overlay').remove();" style="background: #f1f2f3; border: 1px solid #c9cccf; border-radius: 4px; padding: 7px 16px; font-size: 13px; cursor: pointer;">Maybe Later</button>
    </div>
  </div>
"""

SECURITY_OVERLAY_HTML = """
  <div id="security-auth-overlay" class="obstacle-overlay" style="position: fixed; inset: 0; background: rgba(30,10,10,0.75); z-index: 99999; display: flex; align-items: center; justify-content: center;">
    <div style="background: #fff; border-radius: 8px; padding: 24px; max-width: 440px; text-align: center; border: 2px solid #d72c0d; box-shadow: 0 14px 36px rgba(0,0,0,0.4); font-family: -apple-system, sans-serif;">
      <div style="font-size: 26px; margin-bottom: 6px;">🔒</div>
      <h3 style="margin: 0 0 8px; color: #d72c0d; font-size: 17px;">Authorize Payment &amp; Admin Password</h3>
      <p style="font-size: 13px; color: #202223; margin-bottom: 14px;">High-risk operation: Confirm $450 subscription and enter admin password to unlock.</p>
      <input type="password" placeholder="Admin password" style="width: 80%; padding: 8px; border: 1px solid #c9cccf; border-radius: 4px; margin-bottom: 14px;">
      <div>
        <button id="authorize-payment-btn" type="button" style="background: #d72c0d; color: #fff; border: none; border-radius: 4px; padding: 9px 18px; font-weight: 600;">Authorize Payment</button>
      </div>
    </div>
  </div>
"""


@router.get("/test-form", response_class=HTMLResponse)
def shopify_test_form(simulate_popup: Optional[str] = None):
    """Renders the local Shopify Polaris Add Product test form with optional simulated obstacle."""
    html = SHOPIFY_POLARIS_HTML
    if simulate_popup == "cookie":
        html = html.replace("</body>", f"{COOKIE_OVERLAY_HTML}</body>")
    elif simulate_popup == "newsletter":
        html = html.replace("</body>", f"{NEWSLETTER_OVERLAY_HTML}</body>")
    elif simulate_popup == "security":
        html = html.replace("</body>", f"{SECURITY_OVERLAY_HTML}</body>")
    return html
