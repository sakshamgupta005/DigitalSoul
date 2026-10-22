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


def synthesize_workflow(prompt: str, catalog: Optional[Any] = None) -> Dict[str, Any]:
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

    # 3. WhatsApp workflow
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

        flow = {
            "format": "digitalsoul.whatsapp-flow.v1",
            "kind": "whatsapp",
            "recipient": recipient,
            "message": message,
            "send": {"kind": "Enter"},
        }
        return {
            "ok": True,
            "kind": "whatsapp",
            "name": f"WhatsApp · Message {recipient}",
            "recipient": recipient,
            "message": message,
            "flow": flow,
            "summary": f"Send WhatsApp message to {recipient}: \"{message}\"",
            "message_text": f"✨ Synthesized WhatsApp message for {recipient}! Review and approve below.",
        }

    # 4. Web search or Browser routine
    if any(k in lowered for k in ("edge", "chrome", "brave", "search", "google", "bing", "youtube", "browse", "web", "lookup")):
        browser_app = "chrome.exe" if "chrome" in lowered else "brave.exe" if "brave" in lowered else "msedge.exe"
        query_match = re.search(r'(?:search for|search|lookup|find|browse)\s+["\']?([^"\'\n]+)', raw, re.I)
        query = query_match.group(1).strip() if query_match else ""
        if not query and "youtube" in lowered:
            url = "https://www.youtube.com"
            title = "Open YouTube"
        elif not query and "github" in lowered:
            url = "https://github.com"
            title = "Open GitHub"
        elif query:
            url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
            title = f"Search '{query}' in Browser"
        else:
            url = "https://www.bing.com"
            title = "Open Web Browser"

        steps = [
            {"kind": "open_app", "app": browser_app, "path": browser_app, "label": browser_app.replace(".exe", "").capitalize()},
            {"kind": "open_url", "url": url, "app": browser_app, "label": title},
        ]
        wf_dict = {
            "name": title,
            "steps": steps,
            "apps": [browser_app],
        }
        if catalog is not None:
            saved = catalog.save(wf_dict)
            return {
                "ok": True,
                "kind": "workflow_created",
                "id": saved["id"],
                "name": saved["name"],
                "steps": saved["steps"],
                "workflow": saved,
                "summary": f"Created routine '{saved['name']}' ({len(steps)} steps)",
                "message": f"✨ Created routine '{saved['name']}'! Ready to execute.",
            }
        return {
            "ok": True,
            "kind": "workflow_preview",
            "name": title,
            "steps": steps,
            "apps": [browser_app],
            "summary": f"Routine '{title}' with {len(steps)} steps",
            "message": f"✨ Synthesized routine '{title}'! Review and save.",
        }

    # 5. Generic Desktop Routine / Application Launch
    app_target = "notepad.exe"
    app_label = "Notepad"
    if "calc" in lowered:
        app_target = "calculatorapp.exe"
        app_label = "Calculator"
    elif "spotify" in lowered:
        app_target = "Spotify.exe"
        app_label = "Spotify"
    elif "word" in lowered:
        app_target = "WINWORD.EXE"
        app_label = "Word"
    elif "terminal" in lowered or "powershell" in lowered:
        app_target = "powershell.exe"
        app_label = "PowerShell"

    title = raw.strip().capitalize()
    if len(title) > 60:
        title = title[:57] + "…"
    steps = [
        {"kind": "open_app", "app": app_target, "path": app_target, "label": app_label},
    ]
    if "type" in lowered or "write" in lowered:
        text_to_type = extract_quoted_or_tail(raw, ["type", "write", "saying"])
        if text_to_type and text_to_type != raw:
            steps.append({"kind": "set_text", "value": text_to_type, "app": app_target, "label": f"Type '{text_to_type[:24]}'"})

    wf_dict = {
        "name": title,
        "steps": steps,
        "apps": [app_target],
    }
    if catalog is not None:
        saved = catalog.save(wf_dict)
        return {
            "ok": True,
            "kind": "workflow_created",
            "id": saved["id"],
            "name": saved["name"],
            "steps": saved["steps"],
            "workflow": saved,
            "summary": f"Created routine '{saved['name']}' ({len(steps)} steps)",
            "message": f"✨ Created routine '{saved['name']}'! Ready to execute.",
        }

    return {
        "ok": True,
        "kind": "workflow_preview",
        "name": title,
        "steps": steps,
        "apps": [app_target],
        "summary": f"Routine '{title}' with {len(steps)} steps",
        "message": f"✨ Synthesized routine '{title}'! Review and save.",
    }
