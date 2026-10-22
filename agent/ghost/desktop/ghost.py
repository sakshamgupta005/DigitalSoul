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
OFFSCREEN = -3000  # left of this the ghost is parked; real monitors can sit at negative x
_FLAGS = 0x0001 | 0x0010 | 0x0004  # SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER
_FRAME = 1 / 90


def _user():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.FindWindowW.restype = wintypes.HWND
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.IsWindow.argtypes = [wintypes.HWND]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    try:
        # UIA rectangles are physical pixels; a DPI-unaware process would have its moves rescaled.
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2; fails harmlessly if already set
    except Exception:
        pass
    return user


def ease(t):
    """Ease-in-out sine: soft start and landing."""
    return (1 - math.cos(math.pi * t)) / 2


def bend(start, goal, side):
    """Control point of a gentle arc from start to goal, pushed sideways to one side."""
    dx, dy = goal[0] - start[0], goal[1] - start[1]
    distance = math.hypot(dx, dy) or 1
    push = side * min(120.0, distance * 0.22)
    return start[0] + dx / 2 - dy / distance * push, start[1] + dy / 2 + dx / distance * push


def point(start, control, goal, u):
    a, b, c = (1 - u) ** 2, 2 * (1 - u) * u, u ** 2
    return a * start[0] + b * control[0] + c * goal[0], a * start[1] + b * control[1] + c * goal[1]


class Ghost:
    def __init__(self, user=None, sleep=time.sleep, clock=time.monotonic):
        self.user = user or _user()
        self.sleep, self.clock = sleep, clock
        self.hwnd = self.user.FindWindowW(None, TITLE)
        self.flights = 0

    def _rect(self):
        if self.hwnd and not self.user.IsWindow(self.hwnd):  # the notch recreated its overlay
            self.hwnd = None
        self.hwnd = self.hwnd or self.user.FindWindowW(None, TITLE)
        rect = wintypes.RECT()
        if not self.hwnd or not self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return None
        return rect

    def _move(self, x, y):
        self.user.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0, _FLAGS)

    def _animate(self, duration, frame):
        # Driven by the clock, not a frame count: coarse Windows sleeps would otherwise stretch it.
        began = self.clock()
        while True:
            t = min(1.0, (self.clock() - began) / duration)
            frame(t)
            if t >= 1:
                return
            self.sleep(_FRAME)

    def fly_to(self, x, y, speed=850.0, shortest=0.45, longest=0.95):
        """Glide softly along a gentle arc so the ghost hovers just above-left of (x, y)."""
        rect = self._rect()
        if rect is None:
            return
        size = rect.right - rect.left or 72
        # The window centre lands at (x - 0.15 size, y - 0.45 size): close, but not covering the target.
        goal = (x - size * 0.65, y - size * 0.95)
        start = (rect.left, rect.top)
        if start[0] <= OFFSCREEN:  # first appearance: drift in from nearby instead of crossing the screen
            start = (goal[0] + 70, goal[1] - 50)
        distance = math.hypot(goal[0] - start[0], goal[1] - start[1])
        if distance < 2:
            return
        # Alternate sides so successive flights don't look identical.
        self.flights += 1
        control = bend(start, goal, 1 if self.flights % 2 else -1)
        self._animate(min(longest, max(shortest, distance / speed)),
                      lambda t: self._move(*point(start, control, goal, ease(t))))
        self.sleep(0.12)  # a brief hover before acting reads as deliberate, not frantic

    def pulse(self, grow=1.9, duration=0.55):
        """Swell to almost double while acting, then settle back; the ghost scales with its window."""
        rect = self._rect()
        if rect is None:
            return
        base = rect.right - rect.left or 72
        cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2

        def frame(t):
            size = round(base * (1 + (grow - 1) * math.sin(math.pi * t)))
            self.user.SetWindowPos(self.hwnd, None, round(cx - size / 2), round(cy - size / 2), size, size, 0x0010 | 0x0004)
        self._animate(duration, frame)

    def to_control(self, control):
        rect = control.rectangle()
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return  # offscreen or virtualized controls report an empty rectangle
        self.fly_to((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    def park(self):
        if self.hwnd:
            self._move(*PARKED)


def act(control, send_keys=None):
    """'Click' through UI Automation only; the real pointer never moves. Returns the route used."""
    element = control.element_info.element

    def expand_or_collapse():
        pattern = control.iface_expand_collapse
        (pattern.Collapse if pattern.CurrentExpandCollapseState == 1 else pattern.Expand)()

    for pattern, call in (("Invoke", lambda: control.iface_invoke.Invoke()),
                          ("SelectionItem", lambda: control.iface_selection_item.Select()),
                          ("Toggle", lambda: control.iface_toggle.Toggle()),
                          ("ExpandCollapse", expand_or_collapse)):
        try:
            # Chromium can report a pattern as available and still hand back a NULL interface.
            if element.GetCurrentPropertyValue(_PATTERN_AVAILABLE[pattern]):
                call()
                return pattern
        except Exception:
            continue
    try:
        control.iface_legacy_iaccessible.DoDefaultAction()
        return "LegacyIAccessible"
    except Exception:
        pass
    # Last resort, still without the mouse: focus the control and press its activation key, but only
    # once the focus has provably landed on it, so the key can never reach another field.
    key = _ACTIVATION_KEYS.get(control.element_info.control_type)
    if key:
        try:
            control.set_focus()
            if control.has_keyboard_focus():
                if send_keys is None:
                    from pywinauto.keyboard import send_keys
                send_keys(key)
                return "Keyboard"
        except Exception:
            pass
    # Position-preserving fallback for windowless WebView2 elements (e.g. WhatsApp list items)
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        saved = user32.GetCursorPos(ctypes.byref(pt))
        control.click_input()
        if saved:
            user32.SetCursorPos(pt.x, pt.y)
        return "ClickInput"
    except Exception:
        pass
    try:
        rect = control.rectangle()
        if rect.right > rect.left and rect.bottom > rect.top:
            cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
            user32 = ctypes.windll.user32
            user32.SetCursorPos(cx, cy)
            time.sleep(0.04)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.04)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            return "CursorClick"
    except Exception:
        pass
    raise ValueError("This control can't be clicked without moving your mouse, so the ghost stopped here")


# UIA_Is<Pattern>PatternAvailablePropertyId values.
_PATTERN_AVAILABLE = {"Invoke": 30031, "SelectionItem": 30036, "Toggle": 30041, "ExpandCollapse": 30028}
_ACTIVATION_KEYS = {"Button": "{SPACE}", "CheckBox": "{SPACE}", "RadioButton": "{SPACE}",
                    "Hyperlink": "{ENTER}", "ListItem": "{ENTER}", "DataItem": "{ENTER}",
                    "MenuItem": "{ENTER}", "TreeItem": "{ENTER}"}
