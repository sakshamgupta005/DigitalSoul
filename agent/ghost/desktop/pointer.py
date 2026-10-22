"""Eased pointer travel for replays, so the ghost cursor visibly glides to each target."""
import ctypes
import math
import time
from ctypes import wintypes


def glide(x, y, speed=1700.0, shortest=0.16, longest=0.45):
    """Move the real pointer to (x, y) with ease-in-out; quick enough not to waste time."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    start = wintypes.POINT()
    if not user.GetCursorPos(ctypes.byref(start)):
        return
    distance = math.hypot(x - start.x, y - start.y)
    if distance < 3:
        return
    duration = min(longest, max(shortest, distance / speed))
    frames = max(10, int(duration * 120))
    for frame in range(1, frames + 1):
        u = frame / frames
        ease = 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2  # ease-in-out cubic
        user.SetCursorPos(round(start.x + (x - start.x) * ease), round(start.y + (y - start.y) * ease))
        time.sleep(duration / frames)


def glide_to(control):
    """Glide to a UIA control's centre, where click_input() will click."""
    rect = control.rectangle()
    glide((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
