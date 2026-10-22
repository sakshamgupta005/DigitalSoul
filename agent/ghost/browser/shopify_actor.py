"""Shopify browser-use actor with Edge and Blinky integration.

Uses Microsoft Edge and browser-use to fill and submit product forms on Shopify:
https://admin.shopify.com/store/gzn0vr-qn/products/new
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

try:
    from browser_use import Browser
except ImportError:
    Browser = None  # type: ignore[assignment, misc]
from agent.ghost.drivers.browser import browser_executable
from .shopify import ShopifyProduct


def get_browser_use_llm():
    """Resolve and instantiate primary and fallback LLMs for browser-use (from Blinky)."""
    from browser_use import ChatGoogle, ChatOpenAI, ChatGroq

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if gemini_key:
        try:
            return ChatGoogle(model="gemini-2.0-flash", api_key=gemini_key)
        except Exception:
            pass

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            return ChatGroq(model="llama-3.3-70b-versatile", api_key=groq_key)
        except Exception:
            pass

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        try:
            return ChatOpenAI(model="gpt-4o-mini", api_key=openai_key)
        except Exception:
            pass

    return None


def ensure_edge_running_with_cdp(target_url: str = "") -> bool:
    """Checks if Microsoft Edge is listening on CDP port 9222; if not, launches Edge with CDP enabled."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as resp:
            return True
    except Exception:
        pass

    edge_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    edge_exe = next((p for p in edge_paths if os.path.exists(p)), "msedge.exe")
    cmd = [
        edge_exe,
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
    ]
    if target_url:
        cmd.append(target_url)

    try:
        subprocess.Popen(cmd)
        for _ in range(16):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as resp:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def open_tab_in_edge(url: str) -> Optional[str]:
    """Opens or activates a tab in Edge via CDP HTTP endpoints."""
    try:
        encoded = urllib.parse.quote(url, safe=":/?&=#")
        req = urllib.request.Request(f"http://127.0.0.1:9222/json/new?{encoded}", method="PUT")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tab = json.loads(resp.read().decode())
            tab_id = tab.get("id")
            if tab_id:
                try:
                    act_req = urllib.request.Request(f"http://127.0.0.1:9222/json/activate/{tab_id}", method="POST")
                    urllib.request.urlopen(act_req, timeout=2)
                except Exception:
                    pass
            return tab_id
    except Exception:
        pass
    return None


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
    const desc = data.description || '';
    const richEditor = document.querySelector('[contenteditable="true"], .tox-edit-area iframe, #product-description');
    if (richEditor && desc) {
        if (richEditor.tagName === 'IFRAME') {
            try {
                const doc = richEditor.contentDocument || richEditor.contentWindow.document;
                doc.body.innerHTML = desc;
                descSet = true;
                report.steps.push({ field: 'description', tag: 'IFRAME', value: desc.slice(0, 80) });
            } catch(e) {}
        } else if (richEditor.isContentEditable) {
            richEditor.focus();
            richEditor.innerHTML = desc;
            richEditor.dispatchEvent(new Event('input', { bubbles: true }));
            descSet = true;
            report.steps.push({ field: 'description', tag: 'CONTENTEDITABLE', value: desc.slice(0, 80) });
        }
    }
    if (!descSet && desc) {
        setValue(['textarea[name="descriptionHtml"]', 'textarea[name="description"]', '#product-description-text', 'textarea'], desc, 'description');
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
    def __init__(self, cdp_url: Optional[str] = None, headless: bool = False, profile_dir: Optional[str] = None):
        self.cdp_url = cdp_url
        self.headless = headless
        self.profile_dir = profile_dir or str(Path(".ghost/edge_shopify").resolve())
        self.browser: Optional[Browser] = None

    async def start(self, target_url: str = "") -> ShopifyBrowserActor:
        if self.browser:
            return self
        if Browser is None:
            raise RuntimeError("browser_use is required for ShopifyBrowserActor")

        if not self.headless:
            # Interactive mode: ensure Edge is running on 9222 and open tab
            open_tab_in_edge(target_url)
            edge_running = ensure_edge_running_with_cdp(target_url)
            if edge_running:
                self.browser = Browser(cdp_url="http://127.0.0.1:9222")
                await self.browser.start()
                return self

        # Headless / isolated test mode
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
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
        # Do not kill the user's Microsoft Edge process if connected via CDP
        if self.browser:
            try:
                if not self.headless and "9222" in str(getattr(self.browser, "cdp_url", "")):
                    pass
                else:
                    await self.browser.kill()
            except Exception:
                pass
            self.browser = None

    async def execute_product_creation(self, product_data: Dict[str, Any], click_save: bool = True) -> Dict[str, Any]:
        """Opens Microsoft Edge to the Shopify new product URL and uses browser-use to fill fields and submit."""
        target_url = product_data.get("store_url") or "https://admin.shopify.com/store/gzn0vr-qn/products/new"
        
        await self.start(target_url)
        start_time = time.monotonic()
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

            # Check if LLM agent should assist for vision or verification (Blinky pattern)
            llm = get_browser_use_llm()
            if llm and not save_report.get("ok"):
                try:
                    from browser_use import Agent
                    task = (
                        f"On this Shopify page, find the Title input and set it to '{product_data.get('title')}'. "
                        f"Find the Price input and set it to '{product_data.get('price')}'. Then click Save."
                    )
                    agent = Agent(task=task, llm=llm, browser=self.browser, use_vision=True)
                    history = await agent.run(max_steps=5)
                    save_report = {"ok": True, "agent_result": history.final_result()}
                except Exception as agent_err:
                    save_report["agent_error"] = str(agent_err)

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
            # Leave Edge page open so user can inspect their product!
            pass
