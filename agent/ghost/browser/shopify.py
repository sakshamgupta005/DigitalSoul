"""Shopify New Product Automation & AI Field Generator.

Integrates with browser-use for deterministic or assisted DOM execution on:
https://admin.shopify.com/store/gzn0vr-qn/products/new
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.ghost.drivers.browser import browser_executable
from agent.ghost.integrations.omniroute import OmniRouteClient


@dataclass
class ShopifyProduct:
    title: str = ""
    description: str = ""
    status: str = "Active"  # "Active" or "Draft"
    price: str = "29.99"
    compare_at_price: str = "39.99"
    cost_per_item: str = "12.00"
    sku: str = ""
    barcode: str = ""
    track_quantity: bool = True
    inventory_quantity: int = 50
    category: str = "Apparel & Accessories"
    product_type: str = "Standard"
    vendor: str = "GZN Store"
    tags: str = "featured, new-arrival"
    store_url: str = "https://admin.shopify.com/store/gzn0vr-qn/products/new"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_sku(title: str, category: str = "") -> str:
    words = re.findall(r"[A-Za-z0-9]+", title.upper())
    prefix = words[0][:4] if words else "PRD"
    suffix = words[1][:3] if len(words) > 1 else "001"
    num = abs(hash(title)) % 900 + 100
    return f"{prefix}-{suffix}-{num}"


def heuristic_generate(partial: Dict[str, Any]) -> ShopifyProduct:
    """Deterministic, high-quality rule-based generator for offline or fast completions."""
    title = partial.get("title", "").strip() or partial.get("prompt", "").strip() or "Premium Quality Product"
    price_val = float(partial.get("price") or 29.99)
    compare_at = f"{price_val * 1.3:.2f}"
    cost = f"{price_val * 0.4:.2f}"

    category = partial.get("category") or "Apparel & Accessories > Clothing"
    if any(w in title.lower() for w in ["phone", "keyboard", "cable", "mouse", "headphone", "audio", "tech"]):
        category = "Electronics > Audio & Video"
    elif any(w in title.lower() for w in ["coffee", "tea", "cup", "kitchen", "lamp", "desk", "home"]):
        category = "Home & Garden > Kitchen & Dining"

    sku = partial.get("sku") or generate_sku(title, category)

    tags_list = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", title)[:5]]
    tags_list.extend(["new-arrival", "bestseller", "featured"])
    tags = ", ".join(dict.fromkeys(tags_list))

    description = partial.get("description")
    if not description:
        description = (
            f"<p>Experience the superior craftsmanship and modern elegance of the <strong>{title}</strong>. "
            f"Carefully crafted to deliver outstanding durability, functionality, and timeless aesthetics.</p>\n"
            f"<h3>Key Features</h3>\n"
            f"<ul>\n"
            f"  <li><strong>Premium Materials:</strong> Engineered with sustainable, high-grade components built to last.</li>\n"
            f"  <li><strong>Ergonomic Design:</strong> Optimized for everyday comfort, seamless utility, and effortless use.</li>\n"
            f"  <li><strong>Satisfaction Guaranteed:</strong> Comes with standard 30-day return policy and 1-year warranty.</li>\n"
            f"</ul>\n"
            f"<p><em>Order today and elevate your daily experience with fast nationwide shipping.</em></p>"
        )

    return ShopifyProduct(
        title=title,
        description=description,
        status=partial.get("status", "Active"),
        price=f"{price_val:.2f}",
        compare_at_price=partial.get("compare_at_price") or compare_at,
        cost_per_item=partial.get("cost_per_item") or cost,
        sku=sku,
        barcode=partial.get("barcode", ""),
        track_quantity=bool(partial.get("track_quantity", True)),
        inventory_quantity=int(partial.get("inventory_quantity") or 50),
        category=category,
        product_type=partial.get("product_type", "Standard"),
        vendor=partial.get("vendor", "GZN Store"),
        tags=partial.get("tags") or tags,
        store_url=partial.get("store_url", "https://admin.shopify.com/store/gzn0vr-qn/products/new"),
    )


def generate_product_fields(partial: Dict[str, Any]) -> Dict[str, Any]:
    """Generates complete Shopify product fields using OmniRoute/LLM if reachable, or deterministic heuristics."""
    base = heuristic_generate(partial)

    # Attempt OmniRoute / LLM enhancement if configured
    client = OmniRouteClient()
    probe = client.probe(inference=False)
    if probe.get("reachable") and client.settings.model:
        prompt = (
            f"You are a Shopify e-commerce merchandising specialist. "
            f"Given this partial product info: Title: '{base.title}', Category: '{base.category}', Base Price: '{base.price}'. "
            f"Generate a JSON object with: title (clean marketable name), description (compelling HTML formatted with <h3> and <ul>), "
            f"price (string), compare_at_price (string), cost_per_item (string), sku (clean string), "
            f"category (string), tags (comma-separated string), inventory_quantity (integer), status ('Active'). "
            f"Return ONLY valid JSON."
        )
        try:
            with client._client(timeout=8) as http:
                resp = http.post(
                    client.settings.base_url.rstrip("/") + "/chat/completions",
                    json={
                        "model": client.settings.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 800,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code == 200:
                    text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    s = text.find("{")
                    e = text.rfind("}") + 1
                    if s != -1 and e > s:
                        data = json.loads(text[s:e])
                        base.title = data.get("title", base.title)
                        base.description = data.get("description", base.description)
                        base.price = str(data.get("price", base.price))
                        base.compare_at_price = str(data.get("compare_at_price", base.compare_at_price))
                        base.cost_per_item = str(data.get("cost_per_item", base.cost_per_item))
                        base.sku = data.get("sku", base.sku)
                        base.category = data.get("category", base.category)
                        base.tags = data.get("tags", base.tags)
                        base.status = data.get("status", base.status)
        except Exception:
            pass

    return base.to_dict()
