"""Shopify browser-use actor.

Uses browser-use Actor/Browser session to fill and submit product forms on Shopify:
https://admin.shopify.com/store/gzn0vr-qn/products/new
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from browser_use import Browser
from agent.ghost.drivers.browser import browser_executable
from .shopify import ShopifyProduct


FILL_SCRIPT = """(data) => {
    const report = { steps: [], errors: [], ok: true };

    function setValue(selectorList, val, fieldName) {
        if (val === undefined || val === null || val === '') return;
        let el = null;
        for (const sel of selectorList) {
            el = document.querySelector(sel);
            if (el) break;
        }
        if (!el) {
            report.errors.push(`Element not found for field: ${fieldName}`);
            return false;
        }
        el.focus();
        if (el.isContentEditable) {
            el.innerHTML = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
        } else if (el.tagName === 'SELECT') {
            el.value = val;
            el.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            const proto = el instanceof HTMLInputElement ? window.HTMLInputElement.prototype : window.HTMLTextAreaElement.prototype;
            const set = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (set) {
                set.call(el, val);
            } else {
                el.value = val;
            }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        report.steps.push({ field: fieldName, tag: el.tagName, value: String(val).slice(0, 80) });
        return true;
    }

    // 1. Title
    setValue(['input[name="title"]', '#product-title', 'input[id*="title"]', 'input[placeholder*="Title"]'], data.title, 'title');

    // 2. Description (Handles contenteditable div, iframe, or textarea)
    let descSet = false;
    const richEditor = document.querySelector('[contenteditable="true"], .tox-edit-area iframe, #product-description');
    if (richEditor) {
        if (richEditor.tagName === 'IFRAME') {
            try {
                const doc = richEditor.contentDocument || richEditor.contentWindow.document;
                doc.body.innerHTML = data.description;
                descSet = true;
                report.steps.push({ field: 'description', tag: 'IFRAME', value: data.description.slice(0, 80) });
            } catch(e) {}
        } else if (richEditor.isContentEditable) {
            richEditor.focus();
            richEditor.innerHTML = data.description;
            richEditor.dispatchEvent(new Event('input', { bubbles: true }));
            descSet = true;
            report.steps.push({ field: 'description', tag: 'CONTENTEDITABLE', value: data.description.slice(0, 80) });
        }
    }
    if (!descSet) {
        setValue(['textarea[name="descriptionHtml"]', 'textarea[name="description"]', '#product-description-text', 'textarea'], data.description, 'description');
    }

    // 3. Status (Active / Draft)
    const statusVal = (data.status || 'Active').toUpperCase();
    const statusRadio = document.querySelector(`input[name="status"][value="${statusVal}"], input[value="${statusVal}"]`);
    if (statusRadio) {
        statusRadio.checked = true;
        statusRadio.dispatchEvent(new Event('change', { bubbles: true }));
        report.steps.push({ field: 'status', tag: 'RADIO', value: statusVal });
    } else {
        setValue(['#product-status', 'select[name="status"]'], data.status, 'status');
    }

    // 4. Pricing
    setValue(['input[name="price"]', '#pricing-price', 'input[id*="price"]'], data.price, 'price');
    setValue(['input[name="compareAtPrice"]', '#pricing-compare-at', 'input[name*="compare"]'], data.compare_at_price, 'compare_at_price');
    setValue(['input[name="cost"]', '#pricing-cost', 'input[name*="cost"]'], data.cost_per_item, 'cost_per_item');

    // 5. Inventory
    setValue(['input[name="sku"]', '#inventory-sku', 'input[id*="sku"]'], data.sku, 'sku');
    setValue(['input[name="barcode"]', '#inventory-barcode', 'input[id*="barcode"]'], data.barcode, 'barcode');
    setValue(['input[name="quantity"]', 'input[name*="quantity"]', '#inventory-quantity'], data.inventory_quantity, 'inventory_quantity');

    // 6. Organization & Tags
    setValue(['input[name="category"]', '#product-category', 'input[id*="category"]'], data.category, 'category');
    setValue(['input[name="vendor"]', '#product-vendor', 'input[id*="vendor"]'], data.vendor, 'vendor');
    setValue(['input[name="tags"]', '#product-tags', 'input[id*="tags"]'], data.tags, 'tags');

    return JSON.stringify(report);
}"""

CLICK_SAVE_SCRIPT = """() => {
    const candidates = [
        document.querySelector('#product-save-btn'),
        document.querySelector('button[aria-label="Save"]'),
        document.querySelector('button[type="submit"]'),
        ...Array.from(document.querySelectorAll('button')).filter(b => (b.innerText || '').trim().toLowerCase() === 'save')
    ].filter(Boolean);

    if (candidates.length > 0) {
        const btn = candidates[0];
        btn.click();
        return JSON.stringify({ ok: true, clicked: btn.innerText || 'Save', tag: btn.tagName });
    }
    return JSON.stringify({ ok: false, error: 'Save button not found' });
}"""


class ShopifyBrowserActor:
    def __init__(self, cdp_url: Optional[str] = None, headless: bool = True, profile_dir: Optional[str] = None):
        self.cdp_url = cdp_url
        self.headless = headless
        self.profile_dir = profile_dir or str(Path(".ghost/chrome_shopify").resolve())
        self.browser: Optional[Browser] = None
        self._owned = True

    async def start(self) -> ShopifyBrowserActor:
        if self.browser:
            return self
        
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        if self.cdp_url:
            self.browser = Browser(cdp_url=self.cdp_url)
        else:
            self.browser = Browser(
                user_data_dir=self.profile_dir,
                headless=self.headless,
                is_local=True,
                use_cloud=False,
                executable_path=browser_executable(),
                enable_default_extensions=False,
                auto_download_pdfs=False,
            )
        await self.browser.start()
        return self

    async def close(self):
        if self.browser:
            try:
                await self.browser.kill()
            except Exception:
                pass
            self.browser = None

    async def execute_product_creation(self, product_data: Dict[str, Any], click_save: bool = True) -> Dict[str, Any]:
        """Navigates to Shopify new product URL and uses browser-use to fill fields and submit."""
        await self.start()
        start_time = time.monotonic()
        target_url = product_data.get("store_url") or "https://admin.shopify.com/store/gzn0vr-qn/products/new"
        
        page = await self.browser.new_page()
        try:
            # Navigate to target
            await page.goto(target_url)
            await asyncio.sleep(2.5)  # Allow initial framework scripts to mount

            current_url = await page.get_url()
            page_title = await page.get_title()

            # Execute filling script in page context
            fill_result_raw = await page.evaluate(FILL_SCRIPT, product_data)
            fill_report = json.loads(fill_result_raw) if isinstance(fill_result_raw, str) else fill_result_raw

            save_report = {}
            if click_save:
                save_result_raw = await page.evaluate(CLICK_SAVE_SCRIPT)
                save_report = json.loads(save_result_raw) if isinstance(save_result_raw, str) else save_result_raw
                await asyncio.sleep(1.0)

            duration = round(time.monotonic() - start_time, 2)
            return {
                "ok": True,
                "target_url": target_url,
                "current_url": current_url,
                "page_title": page_title,
                "filled_steps": fill_report.get("steps", []),
                "errors": fill_report.get("errors", []),
                "save": save_report,
                "duration_seconds": duration,
            }
        finally:
            # Leave page open or cleanup depending on session
            pass
