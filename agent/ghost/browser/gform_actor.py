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
    const pause = (ms = 100) => new Promise(r => setTimeout(r, ms));

    const blocks = Array.from(document.querySelectorAll('.geS5n, [role="listitem"], .Qr7Oae'));
    for (const item of (data.items || [])) {
        const questionKey = (item.title || item.key || '').trim().toLowerCase();
        const targetVal = String(item.value || '').trim();
        if (!targetVal) continue;

        let matched = false;
        for (const block of blocks) {
            const titleEl = block.querySelector('[role="heading"], .M7eMe, .Fvvcwh, .freebirdFormviewerViewItemsItemItemTitle');
            const blockText = (titleEl ? titleEl.innerText : block.innerText || '').trim().toLowerCase();

            // Check if this block corresponds to the target question
            if (blockText.includes(questionKey) || questionKey.includes(blockText.slice(0, 35))) {
                // 1. Text input / textarea
                const input = block.querySelector('input[type="text"], input[type="email"], input[type="url"], input:not([type]), textarea');
                if (input && !input.disabled) {
                    input.focus();
                    input.value = targetVal;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new Event('blur', { bubbles: true }));
                    report.steps.push({ question: item.title, value: targetVal, type: 'text' });
                    matched = true;
                    break;
                }

                // 2. Radio buttons
                const radios = Array.from(block.querySelectorAll('[role="radio"]'));
                for (const radio of radios) {
                    const rText = (radio.getAttribute('data-value') || radio.getAttribute('aria-label') || radio.innerText || '').trim().toLowerCase();
                    const targetLow = targetVal.toLowerCase();
                    if (rText.includes(targetLow) || targetLow.includes(rText) || (targetLow.startsWith('yes') && rText.startsWith('yes'))) {
                        radio.click();
                        await pause();
                        report.steps.push({ question: item.title, value: targetVal, type: 'radio' });
                        matched = true;
                        break;
                    }
                }
                if (matched) break;

                // 3. Checkboxes
                const checkboxes = Array.from(block.querySelectorAll('[role="checkbox"]'));
                for (const cb of checkboxes) {
                    const cbText = (cb.getAttribute('aria-label') || cb.innerText || '').trim().toLowerCase();
                    const targetLow = targetVal.toLowerCase();
                    if (cbText.includes(targetLow) || targetLow.includes(cbText) || (targetLow.startsWith('i verify') && cbText.includes('verify'))) {
                        if (cb.getAttribute('aria-checked') !== 'true') {
                            cb.click();
                            await pause();
                        }
                        report.steps.push({ question: item.title, value: targetVal, type: 'checkbox' });
                        matched = true;
                        break;
                    }
                }
                if (matched) break;
            }
        }
        if (!matched) {
            report.errors.push(`Could not find interactive element for: "${item.title}"`);
        }
    }

    if (data.click_submit) {
        const submitBtn = Array.from(document.querySelectorAll('[role="button"], button'))
            .find(b => (b.innerText || '').trim().toLowerCase() === 'submit');
        if (submitBtn) {
            submitBtn.click();
            report.submitted = true;
        }
    }

    report.ok = report.steps.length > 0;
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

            # If user provided new fields, persist them into profile memory
            if save_to_memory:
                updates = {}
                for it in items:
                    val = it.get("value")
                    title = it.get("title", "")
                    if val and str(val).strip():
                        # Extract a clean snake_case slug from title
                        clean_key = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_").lower()[:32]
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
                "saved_memory_count": len(items),
                "duration_seconds": round(time.monotonic() - started, 2)
            }
        finally:
            if self.headless:
                await page.close()
