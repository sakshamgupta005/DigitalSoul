"""The ghost does the replay: it glides to each control and acts there. The user's mouse is never moved.

The overlay window ("DigitalSoul ghost") is created by the notch, click-through and never focused.
Moving it with SetWindowPos(SWP_NOACTIVATE) cannot steal focus from the app being replayed. Actions
go through UI Automation patterns (Invoke, SelectionItem, Toggle, the accessible default action),
never through synthesized mouse input.
"""
import ctypes
import math
import time
from ctypes import wintypes

TITLE = "DigitalSoul ghost"
PARKED = (-4000, -4000)
_FLAGS = 0x0001 | 0x0010 | 0x0004  # SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER


def _user():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.FindWindowW.restype = wintypes.HWND
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    return user


class Ghost:
    def __init__(self):
        self.user = _user()
        self.hwnd = self.user.FindWindowW(None, TITLE)
        self.flights = 0

    def _move(self, x, y):
        self.user.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0, _FLAGS)

    def fly_to(self, x, y, speed=850.0, shortest=0.45, longest=0.95):
        """Glide softly along a gentle arc so the ghost hovers just above-left of (x, y)."""
        if not self.hwnd:
            return
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        size = rect.right - rect.left or 44
        goal = (x - size * 0.35, y - size * 0.85)
        start = (rect.left, rect.top)
        if start[0] < -1000:  # first appearance: drift in from nearby instead of crossing the screen
            start = (goal[0] + 70, goal[1] - 50)
        dx, dy = goal[0] - start[0], goal[1] - start[1]
        distance = math.hypot(dx, dy)
        if distance < 2:
            return
        # Arc: a control point pushed sideways, alternating sides so successive flights don't look identical.
        self.flights += 1
        bend = (1 if self.flights % 2 else -1) * min(120.0, distance * 0.22)
        control = (start[0] + dx / 2 - dy / distance * bend, start[1] + dy / 2 + dx / distance * bend)
        duration = min(longest, max(shortest, distance / speed))
        frames = max(24, int(duration * 90))
        for frame in range(1, frames + 1):
            u = (1 - math.cos(math.pi * frame / frames)) / 2  # ease-in-out sine: soft start and landing
            a, b, c = (1 - u) ** 2, 2 * (1 - u) * u, u ** 2
            self._move(a * start[0] + b * control[0] + c * goal[0], a * start[1] + b * control[1] + c * goal[1])
            time.sleep(duration / frames)
        time.sleep(0.12)  # a brief hover before acting reads as deliberate, not frantic

    def pulse(self, grow=1.9, duration=0.55):
        """Swell to almost double while acting, then settle back; the ghost scales with its window."""
        if not self.hwnd:
            return
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        base = rect.right - rect.left or 44
        cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2
        frames = 30
        for frame in range(1, frames + 1):
            size = base * (1 + (grow - 1) * math.sin(math.pi * frame / frames))
            self.user.SetWindowPos(self.hwnd, None, int(cx - size / 2), int(cy - size / 2), int(size), int(size), 0x0010 | 0x0004)
            time.sleep(duration / frames)

    def to_control(self, control):
        rect = control.rectangle()
        self.fly_to((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    def park(self):
        if self.hwnd:
            self._move(*PARKED)


def act(control):
    """'Click' through UI Automation only; the real pointer never moves."""
    element = control.element_info.element
    for pattern, call in (("Invoke", lambda: control.iface_invoke.Invoke()),
                          ("SelectionItem", lambda: control.iface_selection_item.Select()),
                          ("Toggle", lambda: control.iface_toggle.Toggle()),
                          ("ExpandCollapse", lambda: control.iface_expand_collapse.Expand())):
        try:
            if element.GetCurrentPropertyValue(_PATTERN_AVAILABLE[pattern]):
                call()
                return
        except Exception:
            continue
    try:
        control.iface_legacy_iaccessible.DoDefaultAction()
        return
    except Exception:
        pass
    raise ValueError("This control can't be clicked without moving your mouse, so the ghost stopped here")


# UIA_Is<Pattern>PatternAvailablePropertyId values.
_PATTERN_AVAILABLE = {"Invoke": 30031, "SelectionItem": 30036, "Toggle": 30041, "ExpandCollapse": 30028}
