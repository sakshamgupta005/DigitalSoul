"""Resolve WhatsApp's WebView2 content HWND, with verified process ancestry."""
import os
from ..drivers.cua import CuaClient
from .whatsapp import executable, webview_host, is_whatsapp


def content_window(path, client=None):
    owned = client is None
    client = client or CuaClient()
    try:
        windows = client.call("list_windows").get("windows", [])
        matches = []
        for window in windows:
            if str(window.get("app_name", "")).lower() != "msedgewebview2.exe":
                continue
            pid = int(window["pid"])
            try:
                owner = executable(webview_host(pid))
            except (OSError, ValueError):
                continue
            if not is_whatsapp(owner) or (path and os.path.normcase(owner) != os.path.normcase(str(path))):
                continue
            if window.get("minimized") or not window.get("is_on_screen", True):
                continue
            matches.append(window)
        if len(matches) > 1:
            raise ValueError("Multiple WhatsApp content windows; close extras before replay")
        return matches[0] if matches else None
    finally:
        if owned:
            client.close()
