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
        self.page = None
        self.page_url = None
        self.approval = None
        self.submission_attempted = False
        self.profile_dir = str(Path(".ghost/gform_browser").resolve())

    @classmethod
    def inspect_url(cls, url: str, memory: Optional[GFormMemory] = None) -> Dict[str, Any]:
        """Fetch and analyze Google Form URL structure, matching against remembered profile."""
        cls.validate_url(url)
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

        # Try attaching to existing Edge CDP if available and responsive
        endpoint = None
        if self.cdp_url:
            endpoint = validate_local_cdp(self.cdp_url)
        else:
            try:
                candidate = existing_edge_endpoint()
                parsed = urllib.parse.urlsplit(candidate)
                if parsed.port:
                    req = urllib.request.Request(f"http://127.0.0.1:{parsed.port}/json/version")
                    with urllib.request.urlopen(req, timeout=0.8) as resp:
                        if resp.status == 200:
                            endpoint = candidate
            except Exception:
                endpoint = None

        if endpoint:
            try:
                self.browser = Browser(cdp_url=endpoint, use_cloud=False, keep_alive=True,
                                       enable_default_extensions=False, auto_download_pdfs=False)
                self._attached = True
                await self.browser.start()
                return self
            except Exception:
                await self.close()

        # Fall back to isolated browser session launched with executable
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
        self.page = None
        self.page_url = None
        self.approval = None
        if self.browser:
            b, self.browser = self.browser, None
            try:
                if self._attached:
                    await b.stop()
                else:
                    await b.kill()
            except Exception:
                pass

    @staticmethod
    def validate_url(url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "docs.google.com" or
                parsed.username or parsed.password or parsed.port not in (None, 443) or
                not re.fullmatch(r"/forms/(?:u/\d+/)?d/(?:e/)?[\w-]+/viewform", parsed.path)):
            raise ValueError("Use a Google Forms responder URL ending in /viewform")
        return url

    async def _page(self, url):
        self.validate_url(url)
        try:
            await self.start()
            if self.page is None or self.page_url != url:
                self.page = await self.browser.new_page()
                await self.page.goto(url)
                self.page_url = url
                self.approval = None
                self.submission_attempted = False
                await asyncio.sleep(1)
            return self.page
        except Exception:
            await self.close()
            raise

    async def _snapshot(self):
        from .gform_fields import operate
        state = await operate(self.page, "inspect")
        # A login redirect or another document must never receive reviewed answers.
        current = urllib.parse.urlsplit(state["url"])
        expected = urllib.parse.urlsplit(self.page_url)
        if current.scheme != "https" or current.netloc != expected.netloc or current.path != expected.path:
            raise ValueError("Form navigated away; complete sign-in and inspect again")
        return state

    def _review(self, state):
        known, missing = [], []
        for question in state["questions"]:
            key, value, confidence = self.memory.match_question(question["title"], question.get("options") or None)
            item = dict(question, matched_key=key, suggested_value=value if value is not None else "", confidence=confidence)
            (known if value is not None and confidence >= .7 else missing).append(item)
        return dict(title=state["title"], url=self.page_url, total_questions=len(state["questions"]),
                    known_fields=known, new_fields=missing, current_section_only=True)

    async def inspect(self, url):
        await self._page(url)
        self.approval = None
        state = await self._snapshot()
        if not state["questions"]:
            raise ValueError("No visible questions. Check sign-in, form access, or whether responses are closed")
        return self._review(state)

    async def fill_and_review(self, url, items, click_submit=False, save_to_memory=True):
        from .gform_fields import operate
        import secrets
        if click_submit:
            raise ValueError("Fill first, then approve the separate submission preview")
        if self.page_url != url or self.page is None:
            raise ValueError("Inspect this form in the browser before filling")
        await self._page(url)
        if self.submission_attempted:
            raise ValueError("Submission already attempted; inspect its outcome instead of retrying")
        self.approval = None
        state = await self._snapshot()
        # Require answers for visible required questions; never fill another section by fuzzy title.
        for q in state["questions"]:
            answers = [i for i in items if i.get("id") == q["id"]]
            if len(answers) > 1:
                raise ValueError("Duplicate answers for a question")
            if q["required"] and (not answers or answers[0].get("value") in (None, "", [])):
                raise ValueError("Answer required: " + q["title"])
        chosen = [i for i in items if i.get("value") not in (None, "", [])]
        result = await operate(self.page, "fill", signature=state["signature"], items=chosen)
        fresh = await self._snapshot()
        if fresh["signature"] != result["signature"] or fresh["values"] != result["values"] or fresh["errors"]:
            raise ValueError("Form changed or validation failed after filling; inspect the browser")
        updates = {i["key"]: i["value"] for i in result["steps"] if i.get("key")}
        if save_to_memory and updates:
            self.memory.save(updates)
        response = dict(ok=True, engine="browser-use", filled_steps=result["steps"], errors=[], submitted=False,
                        saved_memory_count=len(updates) if save_to_memory else 0)
        if fresh["next"]:
            await operate(self.page, "next", signature=fresh["signature"])
            for _ in range(30):
                await asyncio.sleep(.2)
                following = await self._snapshot()
                if following["signature"] != fresh["signature"]:
                    response.update(review=self._review(following), status="review_next_section")
                    return response
                if following["errors"]:
                    raise ValueError("Next section blocked by form validation")
            raise ValueError("Next section did not appear; no further click attempted")
        if not fresh["submit"]:
            raise ValueError("Final Submit control is unavailable")
        token = secrets.token_urlsafe(32)
        self.approval = (token, time.monotonic()+60, fresh)
        response.update(status="ready_for_submission", approval_token=token)
        return response

    async def submit(self, token):
        from .gform_fields import operate
        approval, self.approval = self.approval, None
        if not approval or approval[0] != token or time.monotonic() > approval[1] or self.submission_attempted:
            raise ValueError("Submission approval expired or already used; review the form again")
        fresh = await self._snapshot()
        old = approval[2]
        if any(fresh[k] != old[k] for k in ("signature", "values", "url")) or fresh["errors"]:
            raise ValueError("Answers changed after review; fill and review again")
        import hashlib
        journal = Path(".ghost/gform_submissions")
        journal.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(self.page_url.encode()).hexdigest()
        try:
            with (journal / (identity + ".attempted")).open("x", encoding="utf-8") as marker:
                marker.write("Submission attempted; inspect browser outcome before explicitly resetting this record.")
                marker.flush()
                os.fsync(marker.fileno())
        except FileExistsError:
            raise ValueError("This form has a previous submission attempt. Inspect its outcome before another submission")
        self.submission_attempted = True  # never automatically retry an uncertain submission
        await operate(self.page, "submit", signature=fresh["signature"])
        for _ in range(30):
            await asyncio.sleep(.2)
            state = await operate(self.page, "inspect")
            parsed = urllib.parse.urlsplit(state["url"])
            expected = urllib.parse.urlsplit(self.page_url)
            if parsed.netloc == expected.netloc and parsed.path == expected.path.replace("/viewform", "/formResponse") and state["confirmed"]:
                return dict(ok=True, submitted=True, status="confirmed")
        return dict(ok=False, submitted=False, status="uncertain", errors=["Submit was attempted; confirmation was not verified. Inspect the browser; do not resubmit."])
