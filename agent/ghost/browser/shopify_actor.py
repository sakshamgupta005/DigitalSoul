"""Shopify browser-use actor with Edge and Blinky integration.

Uses Microsoft Edge and browser-use to fill and submit product forms on Shopify:
https://admin.shopify.com/store/gzn0vr-qn/products/new
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

try:
    from browser_use import Browser
except ImportError:
    Browser = None  # type: ignore[assignment, misc]
from agent.ghost.drivers.browser import browser_executable
from .edge_session import existing_edge_endpoint, validate_local_cdp
from ..desktop.self_healing import heal_browser_page, RecoveryStatus


FILL_SCRIPT = """(data) => (async () => {
    const report = { steps: [], errors: [], ok: true };
    const pause = () => new Promise(resolve => setTimeout(resolve, 180));
    function queryAll(selector, initial = document) {
        const found = [];
        function visit(root) {
            found.push(...root.querySelectorAll(selector));
            for (const el of root.querySelectorAll('*')) if (el.shadowRoot) visit(el.shadowRoot);
        }
        visit(initial);
        return found;
    }

    function visible(el) {
        return !!el.getClientRects().length || queryAll('button,input,[role="option"],[role="combobox"]', el).some(c => c.getClientRects().length);
    }
    function clickControl(el) {
        const native = queryAll('button,[role="option"],[role="combobox"]', el).find(c => c.getClientRects().length);
        (native || el).click();
    }
    async function setValue(selectorList, val, fieldName, reveal, optional = false) {
        if (val === undefined || val === null || val === '') return false;
        let el = null;
        for (const sel of selectorList) {
            const matches = queryAll(sel).filter(e => e.getClientRects().length);
            if (matches.length > 1) {
                if (optional) return false;
                report.errors.push(`Ambiguous field: ${fieldName}`);
                return false;
            }
            el = matches[0];
            if (el) break;
        }
        if (!el && reveal) {
            const controls = queryAll('button').filter(b => b.getClientRects().length && (b.innerText || '').trim() === reveal);
            if (controls.length === 1) {
                controls[0].click();
                await pause();
                for (const selector of selectorList) {
                    const found = queryAll(selector).filter(e => e.getClientRects().length);
                    if (found.length > 1) {
                        if (optional) return false;
                        report.errors.push(`Ambiguous field: ${fieldName}`);
                        return false;
                    }
                    if (found.length === 1) { el = found[0]; break; }
                }
            }
        }
        if (!el) {
            if (!optional) {
                report.errors.push(`Element not found for field: ${fieldName}`);
            }
            return false;
        }
        if (el.disabled || !el.getClientRects().length) {
            if (!optional) {
                report.errors.push(`Field unavailable: ${fieldName}`);
            }
            return false;
        }
        el.focus();
        if (el.isContentEditable) {
            el.innerHTML = val;
            el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        } else if (el.tagName === 'SELECT') {
            el.value = val;
            el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        } else {
            const proto = el instanceof HTMLInputElement ? window.HTMLInputElement.prototype : window.HTMLTextAreaElement.prototype;
            const set = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (set) {
                set.call(el, val);
            } else {
                el.value = val;
            }
            el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
            el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        }
        el.blur();
        await pause();
        const actual = el.isContentEditable ? el.innerHTML : el.value;
        if (String(actual) !== String(val)) {
            if (!optional) {
                report.errors.push(`Value not accepted: ${fieldName}`);
            }
            return false;
        }
        report.steps.push({ field: fieldName, tag: el.tagName, value: String(val).slice(0, 80) });
        return true;
    }

    // 1. Title
    await setValue(['input[name="title"]', '#product-title', 'input[id*="title"]', 'input[placeholder*="Title"]'], data.title, 'title', null, false);

    // 2. Description (Handles contenteditable div, iframe, or textarea)
    let descSet = false;
    const desc = data.description || '';
    const richEditors = queryAll('.tox-edit-area iframe, iframe.tox-edit-area__iframe, #product-description').filter(e => e.getClientRects().length);
    const richEditor = richEditors.length === 1 ? richEditors[0] : null;
    if (richEditor && desc) {
        if (richEditor.tagName === 'IFRAME') {
            try {
                const doc = richEditor.contentDocument || richEditor.contentWindow.document;
                const editor = window.tinymce?.get(richEditor.id.replace(/_ifr$/, ''));
                doc.body.focus();
                if (editor) {
                    editor.setContent(desc);
                    editor.fire('input');
                    editor.fire('change');
                    editor.save();
                } else {
                    doc.body.innerHTML = desc;
                }
                doc.body.dispatchEvent(new richEditor.contentWindow.Event('input', { bubbles: true, composed: true }));
                doc.body.dispatchEvent(new richEditor.contentWindow.Event('change', { bubbles: true, composed: true }));
                doc.body.blur();
                descSet = true;
                report.steps.push({ field: 'description', tag: 'IFRAME', value: desc.slice(0, 80) });
            } catch(e) {}
        } else if (richEditor.isContentEditable) {
            richEditor.focus();
            richEditor.innerHTML = desc;
            richEditor.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
            descSet = true;
            report.steps.push({ field: 'description', tag: 'CONTENTEDITABLE', value: desc.slice(0, 80) });
        }
    }
    if (!descSet && desc) {
        await setValue(['textarea[name="descriptionHtml"]', 'textarea[name="description"]', '#product-description-text'], desc, 'description', null, true);
    }

    // 3. Status (Active / Draft)
    const statusVal = (data.status || 'Active').toUpperCase();
    const statusRadio = data.status ? document.querySelector(`input[name="status"][value="${statusVal}"]`) : null;
    if (statusRadio) {
        statusRadio.checked = true;
        statusRadio.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        report.steps.push({ field: 'status', tag: 'RADIO', value: statusVal });
    } else if (data.status) {
        const fields = queryAll('s-internal-single-picker-field[label="Status"]');
        if (fields.length === 1) {
            const field = fields[0];
            if (field.textContent.trim().toUpperCase() !== statusVal) {
                clickControl(field);
                await pause();
                const options = queryAll('s-internal-picker-option').filter(e => e.getAttribute('value') === statusVal && visible(e));
                if (options.length === 1) { clickControl(options[0]); await pause(); }
            }
            if (field.textContent.trim().toUpperCase() === statusVal) report.steps.push({field:'status',tag:'PICKER',value:data.status});
            else report.errors.push('Status selection was not accepted');
        } else await setValue(['#product-status', 'select[name="status"]'], data.status, 'status', null, true);
    }

    // 4. Pricing
    await setValue(['input[name="price"]', '#pricing-price', 'input[id*="price"]'], data.price, 'price', null, false);
    await setValue(['input[name="compareAtPrice"]', '#pricing-compare-at', 'input[name*="compare"]'], data.compare_at_price, 'compare_at_price', 'Compare-at', true);
    await setValue(['input[name="cost"]', '#pricing-cost', 'input[name*="cost"]'], data.cost_per_item, 'cost_per_item', 'Cost per item', true);

    // 5. Inventory
    await setValue(['input[name="sku"]', '#inventory-sku', 'input[id*="sku"]'], data.sku, 'sku', 'SKU', true);
    await setValue(['input[name="barcode"]', '#inventory-barcode', 'input[id*="barcode"]'], data.barcode, 'barcode', 'Barcodes', true);
    await setValue(['input[name="quantity"]', 'input[name*="quantity"]', 'input[name="inventoryLevels[0]"]', '#inventory-quantity'], data.inventory_quantity, 'inventory_quantity', null, true);

    // Shopify's current organization controls are searchable custom pickers.
    async function setPicker(label, val, fieldName, selectors, multiple = false) {
        if (val === undefined || val === null || val === '') return;
        const native = selectors.some(s => queryAll(s).some(visible));
        if (native) { await setValue(selectors, val, fieldName, null, true); return; }
        const fields = queryAll('s-internal-single-picker-field,s-internal-multi-picker-field')
            .filter(e => e.getAttribute('label') === label);
        if (fields.length !== 1) return;
        const field = fields[0];
        const values = multiple ? String(val).split(',').map(v => v.trim()).filter(Boolean) : [String(val)];
        for (const value of values) {
            const popupId = field.getAttribute('commandfor');
            const popups = queryAll('s-popover').filter(e => e.id === popupId);
            if (popups.length !== 1) return;
            const popup = popups[0];
            clickControl(field);
            await pause();
            const search = queryAll('input[type="search"]', popup).filter(visible);
            if (search.length !== 1) return;
            const term = label === 'Category' ? value.split('>').at(-1).trim() : value;
            const input = search[0];
            input.focus();
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, term);
            input.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
            let choices = [];
            for (let attempt = 0; attempt < 12; attempt++) {
                await pause();
                choices = queryAll('s-internal-picker-option,s-internal-picker-action', popup).filter(e => {
                    if (!visible(e)) return false;
                    const text = e.textContent.trim();
                    return text === term || e.getAttribute('value') === term ||
                        (label !== 'Category' && [`Add ${term}`, `Add "${term}"`, `Create ${term}`].includes(text));
                });
                if (choices.length) break;
            }
            if (choices.length !== 1) return;
            clickControl(choices[0]);
            await pause();
            if (!field.textContent.toLowerCase().includes(term.toLowerCase())) {
                return;
            }
        }
        report.steps.push({field:fieldName,tag:'PICKER',value:String(val).slice(0,80)});
    }
    await setPicker('Category', data.category, 'category', ['input[name="category"]', '#product-category']);
    await setPicker('Vendor', data.vendor, 'vendor', ['input[name="vendor"]', '#product-vendor']);
    await setPicker('Tags', data.tags, 'tags', ['input[name="tags"]', '#product-tags'], true);

    report.ok = report.errors.length === 0;
    return JSON.stringify(report);
})()"""

CLICK_SAVE_SCRIPT = """() => {
    const candidates = [
        document.querySelector('#product-save-btn'),
        document.querySelector('button[aria-label="Save"]'),
        document.querySelector('button[type="submit"]'),
        ...Array.from(document.querySelectorAll('button')).filter(b => (b.innerText || '').trim().toLowerCase() === 'save')
    ].filter(Boolean);

    const unique = [...new Set(candidates)].filter(b => !b.disabled && b.getClientRects().length);
    if (unique.length >= 1) {
        const btn = unique[0];
        btn.click();
        return JSON.stringify({ ok: true, clicked: btn.innerText || 'Save', tag: btn.tagName });
    }
    return JSON.stringify({ ok: false, error: 'Save button not found' });
}"""


class ShopifyBrowserActor:
    def __init__(self, cdp_url: Optional[str] = None, headless: bool = False, profile_dir: Optional[str] = None):
        self.cdp_url = cdp_url
        self.headless = headless
        self.profile_dir = profile_dir or str(Path(".ghost/edge_shopify").resolve())
        self.browser: Optional[Browser] = None
        self._attached = False

    async def start(self, target_url: str = "") -> ShopifyBrowserActor:
        if self.browser:
            return self
        if Browser is None:
            raise RuntimeError("browser_use is required for ShopifyBrowserActor")

        if self.cdp_url or not self.headless:
            endpoint = validate_local_cdp(self.cdp_url) if self.cdp_url else existing_edge_endpoint()
            self.browser = Browser(cdp_url=endpoint, use_cloud=False, keep_alive=True,
                                   enable_default_extensions=False, auto_download_pdfs=False)
            self._attached = True
            await self.browser.start()
            return self

        # Headless / isolated test mode
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self.browser = Browser(
            user_data_dir=self.profile_dir,
            headless=self.headless,
            is_local=True,
            keep_alive=not self.headless,
            use_cloud=False,
            executable_path=browser_executable(),
            enable_default_extensions=False,
            auto_download_pdfs=False,
        )
        await self.browser.start()
        return self

    async def close(self):
        if self.browser:
            browser, self.browser = self.browser, None
            if self._attached:
                await browser.stop()
            else:
                await browser.kill()

    async def execute_product_creation(self, product_data: Dict[str, Any], click_save: bool = False, event_sink: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Fill literal reviewed values on Shopify or practice harness."""
        target_url = product_data.get("store_url", "")
        parsed = urllib.parse.urlsplit(target_url)
        local_test = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                      and parsed.path == "/api/v1/shopify/test-form" and not parsed.fragment)
        live_form = (parsed.scheme == "https" and parsed.hostname == "admin.shopify.com"
                     and parsed.path.startswith("/store/")
                     and (parsed.path.rstrip("/").endswith("/products/new") or parsed.path.rstrip("/").endswith("/products"))
                     and parsed.port in (None, 443))
        if parsed.username or parsed.password or not (local_test or live_form):
            raise ValueError("Use a Shopify admin products URL or the local practice form")
        if click_save and not local_test:
            raise ValueError("Live Save requires M2 effect-bound approval; use fill-only and review in Shopify")
        if not str(product_data.get("title", "")).strip():
            raise ValueError("A product title is required")

        new_product_url = target_url
        if live_form and target_url.rstrip("/").endswith("/products"):
            new_product_url = target_url.rstrip("/") + "/new"

        await self.start(target_url)
        started = time.monotonic()
        matches = []
        if self._attached:
            for candidate in await self.browser.get_pages():
                cand_url = (await candidate.get_url()).rstrip("/")
                if cand_url in (target_url.rstrip("/"), new_product_url.rstrip("/")):
                    matches.append(candidate)

        if len(matches) > 1:
            exact_new = []
            for m in matches:
                if (await m.get_url()).rstrip("/") == new_product_url.rstrip("/"):
                    exact_new.append(m)
            if len(exact_new) == 1:
                matches = exact_new
            else:
                return {"ok": False, "errors": ["Multiple matching Shopify forms are open; keep one open and retry"], "save": {}}

        page = matches[0] if matches else await self.browser.new_page()
        self.page = page

        dest_url = new_product_url if live_form else target_url
        current_url = (await page.get_url()).rstrip("/")
        if not matches:
            await page.goto(dest_url)
            await asyncio.sleep(2)
        elif current_url != dest_url.rstrip("/"):
            await page.goto(dest_url)
            await asyncio.sleep(3)

        current_url = await page.get_url()
        if current_url.rstrip("/") != dest_url.rstrip("/"):
            return {"ok":False, "errors":["Shopify redirected away from the reviewed product form"], "save":{}}

        # Self-healing obstacle inspection before attempting to fill form
        heal_status, heal_reason = await heal_browser_page(page, event_sink=event_sink)
        if heal_status == RecoveryStatus.BLOCKED_SECURITY:
            return {
                "ok": False,
                "errors": [f"Blocked by security boundary: {heal_reason}. Human authorization required."],
                "save": {},
                "status": "blocked_security",
                "duration_seconds": round(time.monotonic() - started, 2)
            }

        raw = await page.evaluate(FILL_SCRIPT, product_data)
        fill = json.loads(raw) if isinstance(raw, str) else raw

        if fill.get("errors"):
            # Check if an obstacle appeared or obscured fields
            heal_status, _ = await heal_browser_page(page, event_sink=event_sink)
            if heal_status == RecoveryStatus.RESOLVED:
                # Retry filling once after resolving obstacle
                raw = await page.evaluate(FILL_SCRIPT, product_data)
                fill = json.loads(raw) if isinstance(raw, str) else raw

        errors = list(fill.get("errors", []))
        supported = {"title", "description", "price", "compare_at_price", "cost_per_item",
                     "sku", "barcode", "inventory_quantity", "category", "vendor", "tags", "status"}
        requested = {key for key in supported if product_data.get(key) is not None and product_data.get(key) != ""}
        completed = {step.get("field") for step in fill.get("steps", [])}
        for field in sorted(requested - completed):
            errors.append(f"Requested field was not filled: {field}")
        save = {}
        if not errors and click_save:
            raw = await page.evaluate(CLICK_SAVE_SCRIPT)
            save = json.loads(raw) if isinstance(raw, str) else raw
            if local_test and isinstance(save, dict) and save.get("ok"):
                raw = await page.evaluate("() => document.body.getAttribute('data-saved-product')")
                saved = json.loads(raw) if raw and raw != "null" else None
                verified = bool(saved and all(str(saved.get(k)) == str(product_data.get(k))
                                             for k in ("title", "price", "sku", "status")))
                save = {"ok": verified, "attempted": True, "verified": verified, "product": saved}
            if not isinstance(save, dict) or not save.get("ok"):
                errors.append("Save was not verified; inspect the page before retrying")
        return {"ok": not errors, "engine":"browser-use", "target_url": target_url, "current_url": current_url,
                "filled_steps": fill.get("steps", []), "errors": errors, "save": save,
                "status": "saved" if save.get("verified") else "filled" if not errors else "blocked",
                "duration_seconds": round(time.monotonic() - started, 2)}
