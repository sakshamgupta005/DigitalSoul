"""Shopify automation API routes and test harness.

Allows the Notch desktop client and background workers to generate product fields
using AI and execute them through browser-use.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent.ghost.browser.shopify import generate_product_fields
from agent.ghost.browser.shopify_actor import ShopifyBrowserActor

router = APIRouter(prefix="/api/v1/shopify", tags=["shopify"])


class GenerateRequest(BaseModel):
    title: Optional[str] = ""
    prompt: Optional[str] = ""
    price: Optional[str] = "29.99"
    category: Optional[str] = None
    store_url: Optional[str] = "https://admin.shopify.com/store/gzn0vr-qn/products/new"


class RunRequest(BaseModel):
    product: Dict[str, Any]
    click_save: bool = True
    headless: bool = True
    cdp_url: Optional[str] = None


@router.post("/generate")
def generate_fields(req: GenerateRequest):
    """Generates complete Shopify product fields using AI / rule heuristics."""
    return generate_product_fields(req.model_dump())


@router.post("/run")
async def run_shopify(req: RunRequest):
    """Executes browser-use product automation against the specified store URL."""
    actor = ShopifyBrowserActor(cdp_url=req.cdp_url, headless=req.headless)
    try:
        result = await actor.execute_product_creation(req.product, click_save=req.click_save)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Shopify automation error: {exc}")
    finally:
        await actor.close()


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


@router.get("/test-form", response_class=HTMLResponse)
def shopify_test_form():
    """Renders the local Shopify Polaris Add Product test form."""
    return SHOPIFY_POLARIS_HTML
