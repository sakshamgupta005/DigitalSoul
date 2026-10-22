"""Natural language workflow synthesizer for DigitalSoul.

Translates typed or spoken instructions into executable workflows:
- Shopify e-commerce routines (via browser-use)
- Excel spreadsheet workflows
- WhatsApp communications
- Desktop and browser application routines
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from ..browser.shopify import generate_product_fields
from ..integrations.omniroute import OmniRouteClient


def extract_quoted_or_tail(text: str, prefix_words: List[str]) -> str:
    """Extract content in quotes or immediately following given prefix words."""
    quoted = re.findall(r'["\']([^"\']+)["\']', text)
    if quoted:
        return quoted[0].strip()
    norm = text
    for w in prefix_words:
        if w in norm.lower():
            idx = norm.lower().find(w) + len(w)
            tail = norm[idx:].strip().lstrip(":").strip()
            if tail:
                return tail
    return text.strip()


def synthesize_workflow(prompt: str, catalog: Optional[Any] = None, learning: Optional[Any] = None) -> Dict[str, Any]:
    """Synthesizes a reviewed workflow from typed or spoken user input."""
    raw = (prompt or "").strip()
    if not raw:
        raise ValueError("Please provide a workflow description by typing or speaking.")
    if len(raw) > 1024:
        raise ValueError("Workflow prompt must be 1024 characters or less.")

    lowered = raw.lower()

    # 1. Shopify workflow
    if any(k in lowered for k in ("shopify", "product", "e-commerce", "ecommerce", "store listing")):
        # Extract title
        title_candidates = re.findall(r'(?:product|item|listing|title)\s*[:=]?\s*["\']?([^"\'$,\n]+)', raw, re.I)
        title = title_candidates[0].strip() if title_candidates else ""
        if not title:
            # Fallback: remove trigger prefixes
            sub = re.sub(r'^(?:add|create|new|make|automate)\s+(?:a\s+)?(?:shopify\s+)?(?:product\s+)?(?:called|named|for|to|:)?\s*', '', raw, flags=re.I)
            title = re.split(r'(?:for\s+\$|\$|price|sku)', sub, flags=re.I)[0].strip()
        if not title or len(title) < 2:
            title = "DigitalSoul Featured Product"

        # Extract price
        price_match = re.search(r'\$\s*(\d+(?:\.\d{1,2})?)|\bprice\s*[:=]?\s*\$?\s*(\d+(?:\.\d{1,2})?)', raw, re.I)
        price = price_match.group(1) or price_match.group(2) if price_match else "49.99"

        # Extract SKU if mentioned
        sku_match = re.search(r'\bsku\s*[:=]?\s*([A-Za-z0-9_-]+)', raw, re.I)
        sku = sku_match.group(1).upper() if sku_match else None

        partial: Dict[str, Any] = {
            "title": title.strip().title(),
            "price": price,
            "store_url": "https://admin.shopify.com/store/gzn0vr-qn/products",
        }
        if sku:
            partial["sku"] = sku

        # Enhance with AI heuristics / OmniRoute
        product = generate_product_fields(partial)
        return {
            "ok": True,
            "kind": "shopify",
            "name": f"Shopify · {product['title']}",
            "product": product,
            "summary": f"Add '{product['title']}' (${product['price']}) to Shopify store via browser-use",
            "message": f"✨ Synthesized Shopify product '{product['title']}'! Review fields below.",
        }

    # 2. Excel workflow
    if any(k in lowered for k in ("excel", "spreadsheet", "sheet", "cells", "rows", "columns", "budget", "expenses")):
        from .excel import generate_excel_data
        topic_match = re.search(r'(?:for|of|topic|about|named|called)\s+["\']?([^"\'\n.,]+)', raw, re.I)
        topic = topic_match.group(1).strip().title() if topic_match else "Monthly Expenses"
        excel_payload = {
            "topic": topic,
            "prompt": raw,
        }
        excel_data = generate_excel_data(excel_payload)
        excel_data["topic"] = topic
        return {
            "ok": True,
            "kind": "excel",
            "name": f"Excel · {topic}",
            "excel": excel_data,
            "summary": f"Update Excel workbook for '{topic}' with formatted headers and rows",
            "message": f"✨ Synthesized Excel spreadsheet for '{topic}'! Review and run below.",
        }

    # 3. Google Form workflow
    if any(k in lowered for k in ("google form", "google forms", "gform", "fill form", "fill out form", "survey", "enrolment form", "registration form", "docs.google.com/forms")):
        url_match = re.search(r'https?://docs\.google\.com/forms/[^\s]+', raw)
        form_url = url_match.group(0) if url_match else "https://docs.google.com/forms/d/e/1FAIpQLScIxD14Pyc183PbDLHcS9N-nbQRRYSncqY0TP3g7qSsfWgnKQ/viewform"
        return {
            "ok": True,
            "kind": "gform",
            "name": "Google Form · Auto-fill & Memory",
            "form": {
                "url": form_url
            },
            "summary": "Auto-fill Google Form using remembered profile and learned slots",
            "message": "✨ Google Form workflow synthesized! Review remembered answers and new fields below."
        }

    # 4. WhatsApp workflow
    if any(k in lowered for k in ("whatsapp", "whats app", "message", "text to", "chat")):
        # Extract recipient
        to_match = re.search(r'\bto\s+([A-Za-z0-9_]+)', raw, re.I)
        if to_match:
            recipient = to_match.group(1).strip().title()
        else:
            rec_cand = re.search(r'(?:message|text)\s+([A-Za-z0-9_]+)', raw, re.I)
            recipient = rec_cand.group(1).strip().title() if rec_cand else "Contact"

        if recipient.lower() in ("a", "the", "someone", "whatsapp", "message", "someone"):
            recipient = "Alex"

        # Extract message body
        msg_match = re.search(r'(?:saying|that|message|with text|text)\s*[:=]?\s*["\']?([^"\']+)["\']?$', raw, re.I)
        if msg_match:
            message = msg_match.group(1).strip().strip('"').strip("'")
        else:
            # If quotes used anywhere
            quoted = re.findall(r'["\']([^"\']+)["\']', raw)
            message = quoted[0] if quoted else f"Hello {recipient}, sending you a quick message!"

        from .whatsapp import find_whatsapp_executable
        wa_exe = find_whatsapp_executable()
        flow = {
            "format": "digitalsoul.whatsapp-flow.v1",
            "kind": "whatsapp",
            "recipient": recipient,
            "search": recipient,
            "chat_title": recipient,
            "message": message,
            "executable": wa_exe,
            "header": {"control_type": "Text", "name": recipient},
            "actions": [
                {"kind": "search", "target": {"control_type": "Edit", "automation_id": "_r_e_"}},
                {"kind": "select", "target": {"control_type": "ListItem", "name": recipient}},
                {"kind": "compose", "target": {"control_type": "Edit"}},
            ],
            "send": {"kind": "enter"},
        }
        steps = [
            {"kind": "open_app", "app": "WhatsApp.exe", "path": wa_exe, "label": "WhatsApp"},
            {"kind": "set_text", "app": "WhatsApp.exe", "path": wa_exe, "label": "Search", "value": recipient},
            {"kind": "click", "app": "WhatsApp.exe", "path": wa_exe, "label": recipient},
            {"kind": "set_text", "app": "WhatsApp.exe", "path": wa_exe, "label": f"Type a message to {recipient}", "value": message, "field": f"Type a message to {recipient}"},
            {"kind": "press_enter", "app": "WhatsApp.exe", "path": wa_exe, "label": "Send"},
        ]
        wf_dict = {
            "name": f"WhatsApp · Message {recipient}",
            "steps": steps,
            "apps": ["WhatsApp.exe"],
            "flow": flow,
        }
        saved_id = None
        saved_wf = None
        if catalog is not None:
            saved = catalog.save(wf_dict)
            saved_id = saved["id"]
            saved_wf = saved

        return {
            "ok": True,
            "kind": "whatsapp",
            "id": saved_id,
            "name": f"WhatsApp · Message {recipient}",
            "recipient": recipient,
            "message": message,
            "flow": flow,
            "workflow": saved_wf or wf_dict,
            "summary": f"Send WhatsApp message to {recipient}: \"{message}\"",
            "message_text": f"✨ Synthesized WhatsApp message for {recipient}! Review and approve below.",
        }

    # Unfamiliar tasks must be grounded in demonstrations, not fabricated locators.
    from .hermes_planner import propose
    return propose(raw, catalog, learning)
