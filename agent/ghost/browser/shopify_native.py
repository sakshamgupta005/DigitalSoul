"""Narrow Shopify native fallback using Blinky's fresh, window-scoped pattern."""
import re
import threading
from urllib.parse import urlsplit

from ..drivers.cua import CuaClient


LABELS = {
    "title": ("Title",), "price": ("Price",), "sku": ("SKU",),
    "compare_at_price": ("Compare-at price",), "cost_per_item": ("Cost per item",),
    "barcode": ("Barcode",), "inventory_quantity": ("Shop location quantity available",),
    "description": ("Rich Text Area", "Rich Text Area. Press ALT-0 for help."),
}


def canonical(value):
    value = str(value or "").strip().rstrip("/")
    if value.startswith("admin.shopify.com/"):
        value = "https://" + value
    parsed = urlsplit(value)
    # Map myshopify.com admin URLs: https://<store>.myshopify.com/admin/products[/new]
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".myshopify.com"):
        store = parsed.hostname.split(".")[0]
        if parsed.path.rstrip("/").endswith("/products") or parsed.path.rstrip("/").endswith("/products/new"):
            return f"https://admin.shopify.com/store/{store}/products/new"
    # Auto-normalize /products -> /products/new and strip query/fragment
    if parsed.scheme == "https" and parsed.hostname == "admin.shopify.com":
        path = parsed.path.rstrip("/")
        if path.endswith("/products"):
            path = path + "/new"
        return f"https://admin.shopify.com{path}"
    return value


def field_label(value):
    # Edge appends the store's currency symbol to the accessible Price label.
    return re.sub(r"[\s\u20b9$\u20ac\u00a3]+$", "", str(value or "")).strip()


class ShopifyNativeActor:
    def __init__(self, client=None):
        self.client = client or CuaClient()
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def prepare(self):
        self._cancelled.clear()

    def _snapshot(self, window, url):
        data = self.client.call("get_window_state", {"pid":int(window["pid"]),
            "window_id":int(window["window_id"]), "include_screenshot":False,
            "max_elements":2000, "max_depth":30})
        if data.get("degraded"):
            raise RuntimeError("Edge accessibility data is unavailable")
        elements = data.get("elements", [])
        address = [e for e in elements if canonical(e.get("value")) == url and
                   any(word in str(e.get("label", "")).lower() for word in ("address", "url", "search bar"))]
        if len(address) != 1:
            raise RuntimeError("The visible Edge tab does not uniquely match the reviewed Shopify URL")
        return elements

    def execute(self, product, click_save=False):
        url = canonical(product.get("store_url"))
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "admin.shopify.com" or
            not re.fullmatch(r"/store/[^/]+/products/new", parsed.path) or parsed.query or parsed.fragment or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("Native Shopify execution requires the exact new-product URL")
        if click_save:
            raise ValueError("Native Shopify only fills reviewed values; Save requires its separate approval path")
        windows = self.client.call("list_windows").get("windows", [])
        candidates = [w for w in windows if "edge" in str(w.get("app_name", w.get("process", ""))).lower()
                      and "shopify" in str(w.get("title", "")).lower() and not w.get("minimized")]
        matches = []
        for window in candidates:
            try:
                self._snapshot(window, url)
                matches.append(window)
            except RuntimeError:
                continue
        if len(matches) != 1:
            raise RuntimeError("Keep the reviewed Shopify new-product tab visible in one Edge window")
        window = matches[0]
        steps, errors = [], []
        for field, value in product.items():
            if self._cancelled.is_set():
                raise RuntimeError("Native Shopify run was stopped; filled values remain unsaved")
            if field == "store_url" or value is None or value == "":
                continue
            if field not in LABELS:
                errors.append(f"Native control needs browser-use: {field}")
                continue
            if field == "description" and "<" in str(value):
                errors.append("HTML description requires the browser-use editor adapter")
                continue
            controls = [e for e in self._snapshot(window, url) if field_label(e.get("label")) in LABELS[field]
                        and str(e.get("role", "")).lower() in {"edit", "textfield", "textbox", "document", "spinner"}
                        and e.get("enabled", True) and e.get("element_token")]
            if len(controls) != 1:
                errors.append(f"Native field is missing or ambiguous: {field}")
                continue
            self.client.call("set_value", {"pid":int(window["pid"]),
                "element_token":controls[0]["element_token"], "value":str(value)})
            fresh = [e for e in self._snapshot(window, url) if field_label(e.get("label")) in LABELS[field]
                     and str(e.get("value", "")) == str(value)]
            if len(fresh) != 1:
                errors.append(f"Native field write was not verified: {field}")
            else:
                steps.append({"field":field, "tag":"UIA", "verified":True})
        return {"ok":not errors, "engine":"cua-driver", "filled_steps":steps, "errors":errors,
                "save":{}, "status":"filled" if not errors else "blocked", "current_url":url}

    def close(self):
        self.client.close()
