"""Desktop Window Tracker and Collector (Developer 1 / M1).

Monitors foreground window switches and UI focus changes using Windows Win32 / ctypes.
Enforces pause-on-lock and app allowlist restrictions.
"""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from contracts import RawEvent, App, Target, Value, Page, Kind, Source, Shape, AppType


user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def is_workstation_locked() -> bool:
    """Check if the Windows workstation is currently locked."""
    if not user32:
        return False
    # If OpenInputDesktop returns 0 / NULL, workstation is locked or running on secure desktop
    desk = user32.OpenInputDesktop(0, False, 0x0001)
    if desk == 0:
        return True
    user32.CloseDesktop(desk)
    return False


def get_foreground_window_info() -> Dict[str, Optional[str]]:
    """Retrieve title and process name for the currently active foreground window."""
    if not user32:
        return {"title": "Mock Window", "process": "mock.exe"}

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": None, "process": None}

    # Window title
    length = user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    title = buff.value

    # Process ID
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    # Basic process name fallback
    process_name = "unknown.exe"
    return {"title": title, "process": process_name, "pid": str(pid.value)}


class DesktopCollector:
    """Consented desktop collector observing window switches and application context."""

    def __init__(self, allowlisted_apps: Optional[Set[str]] = None):
        self.allowlisted_apps = allowlisted_apps or {"acme-portal", "acme-crm", "chrome.exe"}
        self.paused = False
        self.last_window_title: Optional[str] = None
        self.session_id = str(uuid.uuid4())

    def poll_window_switch(self) -> Optional[RawEvent]:
        """Poll foreground state and emit a RawEvent if window context has switched."""
        if self.paused or is_workstation_locked():
            return None

        info = get_foreground_window_info()
        title = info.get("title")
        if not title or title == self.last_window_title:
            return None

        self.last_window_title = title

        # Check if window belongs to an allowlisted application
        matched_app_id = "desktop-app"
        if "Acme Portal" in title or "portal" in title.lower():
            matched_app_id = "acme-portal"
        elif "Acme CRM" in title or "crm" in title.lower():
            matched_app_id = "acme-crm"
        elif "chrome" in (info.get("process") or "").lower():
            matched_app_id = "browser"

        if self.allowlisted_apps and matched_app_id not in self.allowlisted_apps and info.get("process") not in self.allowlisted_apps:
            # Excluded / non-allowlisted window, skip capture
            return None

        event_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        app_ctx = App(
            id=matched_app_id,
            type=AppType.native if matched_app_id == "desktop-app" else AppType.web,
            process=info.get("process") or "unknown.exe",
            window_title=title,
        )

        return RawEvent(
            event_id=event_id,
            ts=now_iso,
            source=Source.desktop,
            kind=Kind.window_switch,
            app=app_ctx,
            target=Target(),
            value=Value(redacted=True, shape=Shape.none, text=None),
            page=Page(url_pattern=f"/window/{matched_app_id}"),
            session_id=self.session_id,
        )
