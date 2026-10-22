"""Replay any recorded desktop steps through fresh UIA lookups.

Each action resolves its control again in the recorded app's window; recorded pixels are never
replayed. The run stops at the first step it cannot resolve uniquely, when the foreground app is
not the recorded one, or when F8 is pressed. Adapts Blinky's process-scoped fresh resolution.
"""
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import PureWindowsPath

from .flow import Native, ancestry
from .whatsapp import executable, hosted_pid, libraries

STEP_KINDS = ("open_app", "open_url", "click", "double_click", "right_click", "scroll", "set_text", "press_enter", "press_key", "hotkey")
BROWSER_EXES = {"chrome.exe", "brave.exe", "msedge.exe"}
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}


def unique(controls):
    """WebView2 apps report one control many times; keep one per UIA runtime id."""
    seen, result = set(), []
    for control in controls:
        try:
            key = tuple(control.element_info.runtime_id)
        except Exception:
            key = id(control)
        if key not in seen:
            seen.add(key)
            result.append(control)
    return result


def stable_name(name):
    """The part of a control name that survives between runs: drops unread counters like "(33) "
    and trailing previews such as a chat's last message after the first comma or line break."""
    name = re.sub(r"^\(\d+\)\s*", "", name or "")
    name = name.strip('"').strip("'")
    return re.split(r"[,\n\r\t]", name, maxsplit=1)[0].strip().lower()


def digest(steps):
    """Binds an approval to the exact steps the user reviewed."""
    return hashlib.sha256(json.dumps(steps, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def keys(text):
    # pywinauto send_keys treats these characters as modifiers or groups.
    return re.sub(r"([+^%~(){}\[\]])", r"{\1}", text)


class Stopped(Exception):
    pass


def _shown(control):
    """Visible and enabled; a control that vanished mid-lookup simply doesn't count."""
    try:
        return control.is_visible() and control.is_enabled()
    except Exception:
        return False


def _named(control):
    try:
        return control.element_info.name
    except Exception:
        return ""


def describe(step):
    label = step.get("label") or step.get("url") or PureWindowsPath(step.get("path", "")).name
    return f"{step.get('kind', 'step').replace('_', ' ')} '{label}'" if label else step.get("kind", "step")


class Desktop:
    """Thin native layer so the step logic stays testable."""
    def __init__(self):
        self.desktop, self.psutil = libraries()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = ctypes.c_void_p
        from pywinauto.keyboard import send_keys
        self.send_keys = send_keys

    def stop_pressed(self):
        return bool(self.user.GetAsyncKeyState(0x77) & 0x8001)  # F8

    def windows(self, path):
        found = []
        is_explorer = PureWindowsPath(str(path)).name.lower() == "explorer.exe"
        target_name = PureWindowsPath(str(path)).name.lower()
        for window in self.desktop.windows():
            try:
                if not window.is_visible():
                    continue
                if is_explorer and getattr(getattr(window, "element_info", None), "class_name", "") in SHELL_CLASSES:
                    continue
                exe_path = executable(hosted_pid(window.handle, window.element_info.process_id))
                if exe_path == path or (exe_path and PureWindowsPath(exe_path).name.lower() == target_name):
                    found.append(window)
            except Exception:
                continue
        return found

    def foreground(self):
        hwnd = self.user.GetForegroundWindow()
        if not hwnd:
            return None, None
        window = self.desktop.window(handle=hwnd).wrapper_object()
        try:
            return window, executable(hosted_pid(hwnd, window.element_info.process_id))
        except Exception:
            return window, None

    def focus(self, window):
        hwnd = getattr(window, "handle", None) or getattr(getattr(window, "element_info", None), "handle", None)
        if not hwnd:
            return
        try:
            if self.user.IsIconic(hwnd):
                self.user.ShowWindow(hwnd, 9)
            else:
                self.user.ShowWindow(hwnd, 5)
            self.user.keybd_event(0x12, 0, 0, 0)
            self.user.SetForegroundWindow(hwnd)
            self.user.keybd_event(0x12, 0, 2, 0)
            self.user.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass

    def launch(self, step):
        app_id = step.get("app_id")
        if app_id:
            if not re.fullmatch(r"[\w.!-]{1,512}", app_id):
                raise ValueError("Invalid recorded application identity")
            os.startfile("shell:AppsFolder\\" + app_id)
        else:
            try:
                os.startfile(step["path"])
            except Exception:
                subprocess.Popen([step["path"]], creationflags=0x08000000 if sys.platform == "win32" else 0)

    focused = Native.focused

    def glide_to(self, control):
        # The ghost is the replay's cursor: it flies to the control; the user's mouse never moves.
        if not hasattr(self, "ghost"):
            from .ghost import Ghost
            self.ghost = Ghost()
        self.ghost.to_control(control)
        self.ghost.pulse()

    def act(self, control):
        from .ghost import act
        act(control)

    def open_url(self, path, url):
        # The browser itself opens the exact page in a new tab; no typing into the address bar.
        subprocess.Popen([path, url], creationflags=0x08000000)

    def cursor_click(self, x, y, button="left"):
        """Active cursor capability for whole browser and desktop execution."""
        if hasattr(self, "ghost"):
            try:
                self.ghost.fly_to(x, y)
                self.ghost.pulse()
            except Exception:
                pass
        try:
            self.user.SetCursorPos(int(x), int(y))
            time.sleep(0.04)
            down = 0x0008 if button == "right" else 0x0002
            up = 0x0010 if button == "right" else 0x0004
            self.user.mouse_event(down, 0, 0, 0, 0)
            time.sleep(0.04)
            self.user.mouse_event(up, 0, 0, 0, 0)
        except Exception:
            pass

    def double_click(self, x, y):
        self.cursor_click(x, y, button="left")
        time.sleep(0.06)
        self.cursor_click(x, y, button="left")

    def scroll(self, delta, x=None, y=None):
        if x is not None and y is not None:
            try:
                self.user.SetCursorPos(int(x), int(y))
            except Exception:
                pass
        try:
            self.user.mouse_event(0x0800, 0, 0, int(delta), 0)
        except Exception:
            pass

    def park(self):
        if hasattr(self, "ghost"):
            self.ghost.park()


class StepRunner:
    def __init__(self, native, sleep=time.sleep, clock=time.monotonic, timeout=10, event_sink=None):
        self.native, self.sleep, self.clock, self.timeout = native, sleep, clock, timeout
        self.window = None
        self.skipped = 0
        self.event_sink = event_sink
        self.current_index = 0
        self.total_steps = 0

    def emit_hud(self, event, **data):
        if self.event_sink:
            try:
                self.event_sink({"event": event, **data})
            except Exception:
                pass

    def _control_rect(self, control):
        if not control:
            return None
        try:
            r = control.rectangle()
            if r.right > r.left and r.bottom > r.top:
                return {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}
        except Exception:
            pass
        return None

    def _check(self):
        if self.native.stop_pressed():
            raise Stopped("Stopped with F8")

    def _wait(self, attempt, what, timeout=None):
        effective_timeout = self.timeout if timeout is None else timeout
        deadline = self.clock() + effective_timeout
        healed_once = False
        while True:
            self._check()
            try:
                result = attempt()
            except Stopped:
                raise
            except Exception:
                # Web UIs rebuild constantly; an element that went stale mid-lookup just means "try again".
                result = None
            if result is not None:
                return result
            # Halfway through timeout without finding target, inspect for unexpected blocking dialogs
            if not healed_once and self.clock() > (deadline - effective_timeout * 0.5):
                healed_once = True
                if self._attempt_desktop_recovery(what):
                    # Give extra time to locate the element now that obstacle is cleared
                    deadline = self.clock() + effective_timeout * 0.5
            if self.clock() > deadline:
                raise ValueError(f"Could not find {what}")
            self.sleep(0.3)

    def _attempt_desktop_recovery(self, what):
        try:
            window, current_exe = self.native.foreground()
            if not window:
                return False
            title = (window.window_text() or getattr(window.element_info, "name", "") or "").strip()
            # Check security boundaries
            from .self_healing import is_security_boundary, ObstacleCategory
            is_sec, sec_reason = is_security_boundary(title)
            if is_sec:
                self.emit_hud("hud_obstacle", kind=ObstacleCategory.SECURITY_BOUNDARY.value,
                              label=f"Security Boundary: {title}", recoverable=False, reason=sec_reason)
                self.emit_hud("hud_paused_for_help", reason=f"Security boundary detected ({sec_reason}). Automatic dismissal is blocked.")
                return False

            # Check for recoverable dialogs (e.g. Tips, Welcome, Notifications)
            dismiss_words = ("close", "dismiss", "ok", "got it", "not now", "skip", "cancel")
            candidates = []
            for b in window.descendants(control_type="Button"):
                name = (b.element_info.name or "").strip().lower()
                if any(w == name or w in name for w in dismiss_words):
                    candidates.append(b)

            if candidates:
                btn = candidates[0]
                btn_name = btn.element_info.name or "Close"
                rect = self._control_rect(window)
                self.emit_hud("hud_obstacle", kind=ObstacleCategory.MODAL_OVERLAY.value,
                              label=f"Unexpected Dialog: '{title or 'Notice'}'", recoverable=True, rect=rect)
                self.emit_hud("hud_recovering", strategy="dismiss_dialog",
                              status=f"Recovering: Dismissing dialog '{title}' via '{btn_name}'", rect=rect)
                self.native.act(btn)
                self.emit_hud("hud_retrying_verification",
                              status=f"Retrying verification: Confirming '{title}' dismissed...")
                self.sleep(0.4)
                return True
        except Exception:
            pass
        return False

    def _app_window(self, path, title=None):
        # Prefer the recorded app's foreground window (dialogs included), else its first window.
        is_explorer = PureWindowsPath(str(path)).name.lower() == "explorer.exe"
        def attempt():
            window, current = self.native.foreground()
            if current == path:
                if not (is_explorer and getattr(getattr(window, "element_info", None), "class_name", "") in SHELL_CLASSES):
                    return window
            windows = self.native.windows(path)
            if windows:
                target_window = windows[0]
                if title:
                    clean_t = title.strip().lower()
                    for w in windows:
                        try:
                            w_title = (w.window_text() or w.element_info.name or "").strip().lower()
                            if clean_t in w_title or w_title in clean_t:
                                target_window = w
                                break
                        except Exception:
                            continue
                self.native.focus(target_window)
                self.sleep(0.2)
                window, current = self.native.foreground()
                if current == path:
                    return window
                return target_window
            return None
        self.window = self._wait(attempt, f"a foreground {PureWindowsPath(path).name} window")
        return self.window

    def _find(self, window, spec, editable=False):
        query = {"control_type": spec["control_type"]}
        if spec.get("name"):
            query["title"] = spec["name"]
        # Cheap identity checks first: web apps can expose tens of thousands of controls.
        matches = unique([item for item in window.descendants(**query)
                          if (not spec.get("automation_id") or item.element_info.automation_id == spec["automation_id"])])
        matches = [item for item in matches if _shown(item)]
        exact = [item for item in matches if ancestry(item) == spec.get("ancestors", [])]
        for group in (exact, matches if spec.get("name") or spec.get("automation_id") else []):
            if len(group) == 1:
                return group[0]
        name_key = stable_name(spec.get("name") or "")
        if name_key:
            types = (spec["control_type"],) if spec["control_type"] not in ("Text", "ListItem", "DataItem") else ("Text", "ListItem", "DataItem")
            candidates = []
            for c_type in types:
                try:
                    candidates.extend(window.descendants(control_type=c_type))
                except Exception:
                    pass
            # Compare names before anything else: each property read is a cross-process call.
            loose = [item for item in unique([item for item in candidates if stable_name(_named(item)) == name_key])
                     if _shown(item)]
            if not loose:
                # Substring / prefix fallback (e.g. File Explorer item details, button labels)
                loose = [item for item in unique([
                    item for item in candidates
                    if name_key in stable_name(_named(item)) or stable_name(_named(item)) in name_key
                ]) if _shown(item)]
            if len(loose) == 1:
                return loose[0]
            if len(loose) > 1 and spec.get("ancestors"):
                with_ancestors = [item for item in loose if ancestry(item) == spec["ancestors"]]
                if len(with_ancestors) == 1:
                    return with_ancestors[0]
            return None
        if editable:
            # Web fields often lack stable ids: accept the field that already has focus.
            try:
                focus = self.native.focused()
            except Exception:
                return None
            if focus is not None and focus.element_info.control_type == spec["control_type"] and focus.element_info.process_id == window.element_info.process_id:
                return focus
        return None

    def _glide(self, control):
        # The ghost is the replay's visible cursor, so it travels there instead of jumping.
        glide = getattr(self.native, "glide_to", None)
        if glide:
            try:
                glide(control)
            except Exception:
                pass

    def open_app(self, step):
        app_name = PureWindowsPath(step.get("path", "")).name
        self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=app_name, rect=None, status=f"Opening {app_name}")
        if not self.native.windows(step["path"]):
            self.native.launch(step)
        window = self._app_window(step["path"], title=step.get("title"))
        rect = self._control_rect(window)
        self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="open_app", label=app_name, rect=rect, status=f"Focusing {app_name}")
        self._glide(window)  # the ghost visibly arrives at the app it is about to use

    def click(self, step):
        window = self._app_window(step["path"])
        label = step.get("label") or step.get("target", {}).get("name") or "control"
        self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=None, status=f"Locating: {label}")
        target = None
        has_coord = bool(step.get("point") or step.get("relative_point"))
        if step.get("target"):
            try:
                target = self._wait(lambda: self._find(window, step["target"]), f"'{label}'",
                                    timeout=min(self.timeout, 3) if has_coord else self.timeout)
            except Exception:
                target = None

        if target is not None:
            self._app_window(step["path"])
            rect = self._control_rect(target)
            self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=rect, status=f"Targeting: {label}")
            self._glide(target)
            self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="click", label=label, rect=rect, status=f"Clicking {label}")
            try:
                self.native.act(target)
                return
            except Exception as act_err:
                try:
                    rect = target.rectangle()
                    if rect.right > rect.left and rect.bottom > rect.top and hasattr(self.native, "cursor_click"):
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        self.native.cursor_click(cx, cy)
                        return
                except Exception:
                    pass
                raise act_err

        # Cursor capability fallback: resolve by relative or absolute coordinates
        rel_pt = step.get("relative_point")
        point = step.get("point")
        if (rel_pt or point) and hasattr(self.native, "cursor_click"):
            try:
                wrect = window.rectangle()
                cx = wrect.left + rel_pt[0] if rel_pt else point[0]
                cy = wrect.top + rel_pt[1] if rel_pt else point[1]
                rect = {"left": cx - 8, "top": cy - 8, "width": 16, "height": 16}
                self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=rect, status=f"Targeting: {label}")
                self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="click", label=label, rect=rect, status=f"Cursor clicking {label}")
                self.native.cursor_click(cx, cy)
                return
            except Exception:
                pass

        if target is None:
            raise ValueError(f"Could not find '{label}'")

    def set_text(self, step, committed=True):
        window = self._app_window(step["path"])
        value = step["value"]
        label = step.get("label") or step.get("target", {}).get("name") or "text field"
        self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=None, status=f"Locating: {label}")
        target = None
        has_coord = bool(step.get("point") or step.get("relative_point"))
        try:
            target = self._wait(lambda: self._find(window, step["target"], editable=True), "the text field",
                                min(self.timeout, 3) if (has_coord or not committed) else self.timeout)
        except ValueError:
            target = None

        if target is not None:
            name = (target.element_info.name or "").strip().rstrip("….").strip()
            if name and value.strip().rstrip("….").strip() == name:
                self.skipped += 1
                return
            rect = self._control_rect(target)
            self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=rect, status=f"Targeting: {label}")
            self._glide(target)
            preview = value[:22] + ("…" if len(value) > 22 else "")
            self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="set_text", label=label, rect=rect, status=f"Typing '{preview}'")
            try:
                target.set_focus()
            except Exception:
                pass
            try:
                if target.element_info.control_type != "Edit":
                    raise ValueError("Not a plain edit")
                target.set_edit_text(value)
                return
            except Exception:
                self._app_window(step["path"])
                self.native.send_keys("^a{BACKSPACE}" + keys(value), with_spaces=True, pause=0.04)
                return

        # Cursor capability fallback for browser web input fields
        rel_pt = step.get("relative_point")
        point = step.get("point")
        if (rel_pt or point) and hasattr(self.native, "cursor_click"):
            try:
                wrect = window.rectangle()
                cx = wrect.left + rel_pt[0] if rel_pt else point[0]
                cy = wrect.top + rel_pt[1] if rel_pt else point[1]
                rect = {"left": cx - 8, "top": cy - 8, "width": 16, "height": 16}
                self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=rect, status=f"Targeting: {label}")
                self.native.cursor_click(cx, cy)
                self.sleep(0.1)
                preview = value[:22] + ("…" if len(value) > 22 else "")
                self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="set_text", label=label, rect=rect, status=f"Typing '{preview}'")
                self.native.send_keys("^a{BACKSPACE}" + keys(value), with_spaces=True, pause=0.04)
                return
            except Exception:
                pass

        if committed:
            raise ValueError("Could not find the text field")
        self.skipped += 1

    def open_url(self, step):
        url = step.get("url", "")
        self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=url, rect=None, is_browser_level=True, status=f"Navigating to {url}")
        self.native.open_url(step["path"], step["url"])
        window = self._app_window(step["path"])
        self.sleep(1.5)  # let the page start loading before anything else happens
        # Show the ghost at the address bar that now holds the page, or at least at the browser.
        try:
            bar = next(iter(window.descendants(control_type="Edit", title="Address and search bar")), None)
        except Exception:
            bar = None
        rect = self._control_rect(bar or window)
        self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="open_url", label=url, rect=rect, status="Loaded page")
        self._glide(bar or window)

    def press_enter(self, step):
        self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="press_enter", label="Enter", rect=None, status="Submitting (Enter)")
        self._app_window(step["path"])
        self.native.send_keys("{ENTER}")

    def double_click(self, step):
        self.click(step)
        self.sleep(0.06)
        rel_pt = step.get("relative_point")
        point = step.get("point")
        if (rel_pt or point) and hasattr(self.native, "cursor_click"):
            window = self._app_window(step["path"])
            try:
                wrect = window.rectangle()
                cx = wrect.left + rel_pt[0] if rel_pt else point[0]
                cy = wrect.top + rel_pt[1] if rel_pt else point[1]
                self.native.cursor_click(cx, cy)
            except Exception:
                pass

    def right_click(self, step):
        window = self._app_window(step["path"])
        label = step.get("label") or "context menu"
        self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=None, status=f"Locating: {label}")
        rel_pt = step.get("relative_point")
        point = step.get("point")
        if (rel_pt or point) and hasattr(self.native, "cursor_click"):
            try:
                wrect = window.rectangle()
                cx = wrect.left + rel_pt[0] if rel_pt else point[0]
                cy = wrect.top + rel_pt[1] if rel_pt else point[1]
                rect = {"left": cx - 8, "top": cy - 8, "width": 16, "height": 16}
                self.emit_hud("hud_targeting", step_index=self.current_index + 1, total_steps=self.total_steps, label=label, rect=rect, status=f"Right click: {label}")
                self.native.cursor_click(cx, cy, button="right")
                return
            except Exception:
                pass
        self._app_window(step["path"])
        self.native.send_keys("+{F10}")

    def scroll(self, step):
        delta = step.get("delta", -120)
        window = self._app_window(step["path"])
        rel_pt = step.get("relative_point")
        point = step.get("point")
        cx, cy = None, None
        if rel_pt or point:
            try:
                wrect = window.rectangle()
                cx = wrect.left + rel_pt[0] if rel_pt else point[0]
                cy = wrect.top + rel_pt[1] if rel_pt else point[1]
            except Exception:
                pass
        self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="scroll", label=f"Scroll ({delta})", rect=None, status="Scrolling")
        if hasattr(self.native, "scroll"):
            self.native.scroll(delta, cx, cy)

    def press_key(self, step):
        key = step.get("key", "").lower()
        key_map = {"tab": "{TAB}", "escape": "{ESC}", "esc": "{ESC}", "backspace": "{BACKSPACE}", "delete": "{DELETE}",
                   "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}", "space": "{SPACE}"}
        mapped = key_map.get(key, key)
        self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="press_key", label=key.upper(), rect=None, status=f"Key: {key.upper()}")
        self._app_window(step["path"])
        self.native.send_keys(mapped)

    def hotkey(self, step):
        combo = step.get("combo") or step.get("keys") or ""
        self.emit_hud("hud_acting", step_index=self.current_index + 1, total_steps=self.total_steps, action="hotkey", label=combo, rect=None, status=f"Hotkey: {combo}")
        self._app_window(step["path"])
        self.native.send_keys(combo)

    def run(self, steps, progress=lambda index, step: None):
        self.total_steps = len(steps)
        previous = None
        for index, step in enumerate(steps):
            self.current_index = index
            if step.get("kind") not in STEP_KINDS:
                raise ValueError(f"Step {index + 1} has an unsupported action")
            self._check()
            # A softened version of the recorded rhythm: calm, but without wasting time.
            if index:
                gap = step["at"] - previous if isinstance(step.get("at"), (int, float)) and previous is not None else 0
                self.sleep(min(max(gap * 0.6, 0.5), 1.6))
            previous = step.get("at", previous)
            progress(index, step)
            try:
                if step["kind"] == "set_text":
                    following = steps[index + 1]["kind"] if index + 1 < len(steps) else None
                    self.set_text(step, committed=following == "press_enter")
                else:
                    getattr(self, step["kind"])(step)
            except (Stopped, ValueError):
                raise
            except Exception as exc:
                # Provider messages can quote private UI text; name the step and the failure type only.
                raise ValueError(f"{describe(step)} failed ({type(exc).__name__})") from exc
        return len(steps)


def validate(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 500:
        raise ValueError("Nothing to replay")
    for step in steps:
        if step.get("kind") not in STEP_KINDS or not PureWindowsPath(step.get("path", "")).is_absolute():
            raise ValueError("Recording contains a step that cannot be replayed")
        if step["kind"] == "set_text" and not isinstance(step.get("value"), str):
            raise ValueError("Recording contains a text step without text")
        if step["kind"] == "open_url" and (PureWindowsPath(step["path"]).name.lower() not in BROWSER_EXES
                                           or not re.fullmatch(r"https?://[^\s]{1,2040}", step.get("url", ""))):
            raise ValueError("Recording contains a web address that cannot be opened")


def main():
    """Worker entry: reads {"steps": [...]} on stdin, prints one JSON result line."""
    payload = json.loads(sys.stdin.readline())
    done = 0
    desktop = None
    try:
        validate(payload["steps"])
        desktop = Desktop()
        runner = StepRunner(desktop, event_sink=lambda evt: print(json.dumps(evt), flush=True))

        def progress(index, step):
            nonlocal done
            done = index
        runner.run(payload["steps"], progress)
        skipped = f" Skipped {runner.skipped} text step(s) that were only field placeholders or focus changes." if runner.skipped else ""
        count = len(payload["steps"])
        result = {"ok": True, "detail": f"Replayed {count} step{'' if count == 1 else 's'}.{skipped} Check the apps to confirm the result."}
    except Stopped as exc:
        result = {"ok": False, "detail": f"{exc} before step {done + 1}."}
    except Exception as exc:
        # UIA text can contain private UI content; report the step, not the provider message.
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        result = {"ok": False, "detail": f"Stopped at step {done + 1}: {reason[:200]}"}
    finally:
        if desktop:
            desktop.park()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
