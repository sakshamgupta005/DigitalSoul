"""Google Forms browser automation actor using browser-use and persistent memory."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()

try:
    from browser_use import Browser
except ImportError:
    Browser = None  # type: ignore[assignment, misc]

from agent.ghost.drivers.browser import browser_executable
from .edge_session import existing_edge_endpoint, validate_local_cdp
from .gform_memory import GFormMemory


GFORM_FILL_SCRIPT = """(data) => (async () => {
    const report = { steps: [], errors: [], ok: true, submitted: false };
    const pause = (ms = 160) => new Promise(r => setTimeout(r, ms));
    const items = (data.items || []).map((item, index) => ({ ...item, index, done: false }));
    const visible = (node) => !!node && node.getClientRects().length > 0 && !node.disabled;
    const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const blocks = () => Array.from(document.querySelectorAll('.geS5n, [role="listitem"], .Qr7Oae')).filter(visible);
    const blockTitle = (block) => {
        const titleEl = block.querySelector('[role="heading"], .M7eMe, .Fvvcwh, .freebirdFormviewerViewItemsItemItemTitle');
        return norm(titleEl ? titleEl.innerText : block.innerText || '');
    };
    const fireValue = (input, value) => {
        const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) setter.call(input, value); else input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
    };
    const findBlock = (item, currentBlocks) => {
        if (item.entry_id === 'emailAddress' || norm(item.title || item.key) === 'email') {
            const emailInput = document.querySelector('input[name="emailAddress"], input[type="email"], input[autocomplete="email"]');
            if (emailInput && visible(emailInput)) {
                return emailInput.closest('.geS5n, [role="listitem"], .Qr7Oae') || emailInput.parentElement;
            }
        }
        const entry = item.entry_id ? `entry.${item.entry_id}` : '';
        if (entry) {
            const control = document.querySelector(`[name="${entry}"]`);
            if (control) return control.closest('.geS5n, [role="listitem"], .Qr7Oae') || control.parentElement;
        }
        const wanted = norm(item.title || item.key);
        return currentBlocks.find(block => {
            const title = blockTitle(block);
            return title === wanted || (wanted && (title.includes(wanted) || wanted.includes(title.slice(0, 45))));
        });
    };
    const fillBlock = async (block, item) => {
        const targetVal = String(item.value || '').trim();
        if (!targetVal) return false;
        const targetLow = norm(targetVal);

        const input = block.querySelector('input[name="emailAddress"], input[type="email"], input[type="text"], input[type="url"], input:not([type]), textarea')
            || (block.tagName === 'INPUT' ? block : null);
        if (input && !input.disabled) {
            input.focus();
            fireValue(input, targetVal);
            return { type: 'text' };
        }

        const radios = Array.from(block.querySelectorAll('[role="radio"]')).filter(visible);
        for (const radio of radios) {
            const label = norm(radio.getAttribute('data-value') || radio.getAttribute('aria-label') || radio.innerText);
            if (label === targetLow || label.includes(targetLow) || targetLow.includes(label)) {
                radio.click();
                await pause();
                return { type: 'radio' };
            }
        }

        const checkboxes = Array.from(block.querySelectorAll('[role="checkbox"]')).filter(visible);
        for (const checkbox of checkboxes) {
            const label = norm(checkbox.getAttribute('aria-label') || checkbox.innerText);
            if (label === targetLow || label.includes(targetLow) || targetLow.includes(label)) {
                if (checkbox.getAttribute('aria-checked') !== 'true') checkbox.click();
                await pause();
                return { type: 'checkbox' };
            }
        }

        const dropdown = block.querySelector('[role="listbox"], [role="combobox"]');
        if (dropdown && visible(dropdown)) {
            dropdown.click();
            await pause(100);
            const option = Array.from(document.querySelectorAll('[role="option"]')).find((candidate) => {
                const label = norm(candidate.innerText || candidate.getAttribute('aria-label'));
                return label === targetLow || label.includes(targetLow) || targetLow.includes(label);
            });
            if (option) {
                option.click();
                await pause();
                return { type: 'dropdown' };
            }
        }
        return false;
    };

    // Google Forms is paginated. Fill the visible page, advance, and repeat so
    // a reviewed answer set can follow the form's own branching flow.
    for (let page = 0; page < 20; page += 1) {
        await pause();
        const currentBlocks = blocks();
        for (const item of items.filter(candidate => !candidate.done && String(candidate.value || '').trim())) {
            const block = findBlock(item, currentBlocks);
            if (!block) continue;
            const filled = await fillBlock(block, item);
            if (filled) {
                item.done = true;
                report.steps.push({ question: item.title, key: item.key || '', value: String(item.value).trim(), ...filled });
            }
        }

        // Ensure any visible built-in email collection input is filled before advancing
        const emailInput = document.querySelector('input[name="emailAddress"], input[type="email"], input[autocomplete="email"]');
        if (emailInput && visible(emailInput) && !emailInput.value.trim()) {
            const emailItem = items.find(it => (it.entry_id === 'emailAddress' || norm(it.title || it.key).includes('email')) && it.value);
            if (emailItem) {
                emailInput.focus();
                fireValue(emailInput, String(emailItem.value).trim());
                emailItem.done = true;
                report.steps.push({ question: emailItem.title || 'Email', value: String(emailItem.value).trim(), type: 'email' });
            }
        }

        const next = Array.from(document.querySelectorAll('[role="button"], button')).find(button => {
            const label = norm(button.innerText || button.getAttribute('aria-label'));
            return visible(button) && /^(next|continue)$/.test(label);
        });
        if (!next) break;
        next.click();
        await pause(500);
    }

    for (const item of items) {
        if (!item.done && String(item.value || '').trim()) report.errors.push(`Could not fill: "${item.title}"`);
    }

    if (data.click_submit) {
        const submitBtn = Array.from(document.querySelectorAll('[role="button"], button')).find(button => norm(button.innerText || button.getAttribute('aria-label')) === 'submit' && visible(button));
        if (submitBtn) {
            submitBtn.click();
            report.submitted = true;
        }
    }

    report.ok = report.steps.length > 0 && report.errors.length === 0;
    return JSON.stringify(report);
})()"""


def extract_form_structure(html: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse FB_PUBLIC_LOAD_DATA_ from Google Form HTML to extract title and questions."""
    title = "Google Form"
    questions = []
    
    # Try finding page title
    title_match = re.search(r'<meta property="og:title" content="(.*?)"', html) or re.search(r'<title>(.*?)</title>', html)
    if title_match:
        title = title_match.group(1).replace(" - Google Forms", "").strip()

    match = re.search(r'FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>', html, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if len(data) > 1 and len(data[1]) > 8 and data[1][8]:
                title = data[1][8]
            
            raw_questions = data[1][1] if len(data) > 1 and len(data[1]) > 1 else []
            for q in raw_questions:
                if q and len(q) > 1 and q[1]:
                    q_title = q[1].strip()
                    q_type = q[3] if len(q) > 3 else 0
                    entry_id = q[4][0][0] if q[4] and len(q[4]) > 0 and q[4][0] else None
                    options = None
                    if q[4] and len(q[4]) > 0 and q[4][0] and len(q[4][0]) > 1 and q[4][0][1]:
                        options = [opt[0] for opt in q[4][0][1] if opt and len(opt) > 0 and opt[0]]

                    # Only include actual input questions (skip section headers / info notes)
                    if entry_id is not None or options is not None or q_type in (0, 1, 2, 3, 4, 9):
                        questions.append({
                            "title": q_title,
                            "entry_id": entry_id,
                            "type": q_type,
                            "type_name": {0: "text", 1: "paragraph", 2: "radio", 3: "dropdown", 4: "checkbox", 9: "date"}.get(q_type, "input"),
                            "options": options
                        })

            # Detect built-in Google Forms email collection (e.g. input name="emailAddress")
            has_email = False
            if len(data) > 1 and len(data[1]) > 10 and data[1][10] and isinstance(data[1][10], list) and len(data[1][10]) > 0 and data[1][10][0] == 1:
                has_email = True
            elif 'name="emailAddress"' in html or 'autocomplete="email"' in html:
                has_email = True

            if has_email and not any(q.get("entry_id") == "emailAddress" or q["title"].strip().lower() == "email" for q in questions[:3]):
                questions.insert(0, {
                    "title": "Email",
                    "entry_id": "emailAddress",
                    "type": 0,
                    "type_name": "text",
                    "options": None
                })
        except Exception:
            pass

    return title, questions


class GFormActor:
    """Automates Google Forms through browser-use and smart profile memory."""

    def __init__(self, cdp_url: Optional[str] = None, headless: bool = False, memory: Optional[GFormMemory] = None):
        self.cdp_url = cdp_url
        self.headless = headless
        self.memory = memory or GFormMemory()
        self.browser: Optional[Browser] = None
        self._attached = False
        self.profile_dir = str(Path(".ghost/gform_browser").resolve())

    @classmethod
    def inspect_url(cls, url: str, memory: Optional[GFormMemory] = None) -> Dict[str, Any]:
        """Fetch and analyze Google Form URL structure, matching against remembered profile."""
        mem = memory or GFormMemory()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        title, questions = extract_form_structure(html)
        
        known_fields = []
        new_fields = []

        for q in questions:
            key, matched_val, conf = mem.match_question(q["title"], q.get("options"))
            item = {
                "title": q["title"],
                "entry_id": q.get("entry_id"),
                "type": q.get("type_name", "text"),
                "options": q.get("options"),
                "matched_key": key,
                "suggested_value": matched_val or "",
                "confidence": conf
            }
            if matched_val and conf >= 0.7:
                known_fields.append(item)
            else:
                new_fields.append(item)

        return {
            "title": title,
            "url": url,
            "total_questions": len(questions),
            "known_fields": known_fields,
            "new_fields": new_fields,
            "remembered_profile": mem.profile
        }

    async def start(self) -> GFormActor:
        if self.browser:
            return self
        if Browser is None:
            raise RuntimeError("browser_use is required for Google Forms automation")

        # Try attaching to existing Edge CDP if available
        if self.cdp_url:
            endpoint = validate_local_cdp(self.cdp_url)
            self.browser = Browser(cdp_url=endpoint, use_cloud=False, keep_alive=True,
                                   enable_default_extensions=False, auto_download_pdfs=False)
            self._attached = True
            await self.browser.start()
            return self

        try:
            endpoint = existing_edge_endpoint()
            self.browser = Browser(cdp_url=endpoint, use_cloud=False, keep_alive=True,
                                   enable_default_extensions=False, auto_download_pdfs=False)
            self._attached = True
            await self.browser.start()
            return self
        except Exception:
            # Fall back to isolated browser session
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
            self._attached = False
            await self.browser.start()
            return self

    async def close(self):
        if self.browser:
            b, self.browser = self.browser, None
            if self._attached:
                await b.stop()
            else:
                await b.kill()

    async def fill_and_review(self, url: str, items: List[Dict[str, Any]], click_submit: bool = False, save_to_memory: bool = True) -> Dict[str, Any]:
        """Navigate to Google Form, fill all items, and optionally submit."""
        await self.start()
        started = time.monotonic()

        page = await self.browser.new_page()
        try:
            await page.goto(url)
            await asyncio.sleep(2.5)

            raw = await page.evaluate(GFORM_FILL_SCRIPT, {"items": items, "click_submit": click_submit})
            report = json.loads(raw) if isinstance(raw, str) else raw

            # Persist only values that were actually filled. Unreachable branch
            # questions and blank placeholders must not become memory facts.
            updates = {}
            if save_to_memory:
                for it in report.get("steps", []):
                    val = it.get("value")
                    title = it.get("question", "")
                    if val and str(val).strip():
                        clean_key = it.get("key") or re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_").lower()[:64]
                        if clean_key:
                            updates[clean_key] = str(val).strip()
                if updates:
                    self.memory.save(updates)

            return {
                "ok": report.get("ok", True),
                "engine": "browser-use",
                "url": url,
                "filled_steps": report.get("steps", []),
                "errors": report.get("errors", []),
                "submitted": report.get("submitted", False),
                "saved_memory_count": len(updates) if save_to_memory else 0,
                "duration_seconds": round(time.monotonic() - started, 2)
            }
        finally:
            if self.headless:
                await page.close()
