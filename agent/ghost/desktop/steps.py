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
from .whatsapp import executable, libraries

STEP_KINDS = ("open_app", "open_url", "click", "set_text", "press_enter")
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
    return re.split(r"[,\n]", name, maxsplit=1)[0].strip().lower()


def digest(steps):
    """Binds an approval to the exact steps the user reviewed."""
    return hashlib.sha256(json.dumps(steps, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def keys(text):
    # pywinauto send_keys treats these characters as modifiers or groups.
    return re.sub(r"([+^%~(){}\[\]])", r"{\1}", text)


class Stopped(Exception):
    pass


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
        for window in self.desktop.windows():
            try:
                if not window.is_visible():
                    continue
                if is_explorer and getattr(getattr(window, "element_info", None), "class_name", "") in SHELL_CLASSES:
                    continue
                if executable(window.element_info.process_id) == path:
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
            return window, executable(window.element_info.process_id)
        except Exception:
            return window, None

    def focus(self, window):
        hwnd = getattr(window, "handle", None)
        if not hwnd:
            return
        try:
            if self.user.IsIconic(hwnd):
                self.user.ShowWindow(hwnd, 9)
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
            os.startfile(step["path"])

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

    def park(self):
        if hasattr(self, "ghost"):
            self.ghost.park()


class StepRunner:
    def __init__(self, native, sleep=time.sleep, clock=time.monotonic, timeout=10):
        self.native, self.sleep, self.clock, self.timeout = native, sleep, clock, timeout
        self.window = None
        self.skipped = 0

    def _check(self):
        if self.native.stop_pressed():
            raise Stopped("Stopped with F8")

    def _wait(self, attempt, what, timeout=None):
        deadline = self.clock() + (self.timeout if timeout is None else timeout)
        while True:
            self._check()
            result = attempt()
            if result is not None:
                return result
            if self.clock() > deadline:
                raise ValueError(f"Could not find {what}")
            self.sleep(0.3)

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
        matches = [item for item in matches if item.is_visible() and item.is_enabled()]
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
            loose = [item for item in unique(candidates)
                     if stable_name(item.element_info.name) == name_key and item.is_visible()]
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
        # The ghost cursor follows the real pointer, so travel there visibly instead of jumping.
        glide = getattr(self.native, "glide_to", None)
        if glide:
            try:
                glide(control)
            except Exception:
                pass

    def open_app(self, step):
        if not self.native.windows(step["path"]):
            self.native.launch(step)
        self._app_window(step["path"], title=step.get("title"))

    def click(self, step):
        window = self._app_window(step["path"])
        target = self._wait(lambda: self._find(window, step["target"]), f"“{step.get('label') or 'control'}”")
        self._app_window(step["path"])
        self._glide(target)
        act = getattr(self.native, "act", None)
        if act:
            act(target)  # UI Automation only; the user's pointer stays where it is
        else:
            target.click_input()

    def set_text(self, step, committed=True):
        window = self._app_window(step["path"])
        value = step["value"]
        try:
            # Text that no Enter follows is often incidental focus, so give it less time.
            target = self._wait(lambda: self._find(window, step["target"], editable=True), "the text field",
                                None if committed else min(self.timeout, 2))
        except ValueError:
            if committed:
                raise
            self.skipped += 1
            return
        name = (target.element_info.name or "").strip().rstrip("….").strip()
        if name and value.strip().rstrip("….").strip() == name:
            # Older recordings saved a field's placeholder or label as typed text.
            self.skipped += 1
            return
        self._glide(target)
        try:
            target.set_focus()
        except Exception:
            pass
        try:
            if target.element_info.control_type != "Edit":
                raise ValueError("Not a plain edit")
            target.set_edit_text(value)
        except Exception:
            self._app_window(step["path"])
            self.native.send_keys("^a{BACKSPACE}" + keys(value), with_spaces=True, pause=0.01)

    def open_url(self, step):
        self.native.open_url(step["path"], step["url"])
        self._app_window(step["path"])
        self.sleep(1.5)  # let the page start loading before anything else happens

    def press_enter(self, step):
        self._app_window(step["path"])
        self.native.send_keys("{ENTER}")

    def run(self, steps, progress=lambda index, step: None):
        previous = None
        for index, step in enumerate(steps):
            if step.get("kind") not in STEP_KINDS:
                raise ValueError(f"Step {index + 1} has an unsupported action")
            self._check()
            # A softened version of the recorded rhythm: calm, but without wasting time.
            if previous is not None and isinstance(step.get("at"), (int, float)):
                self.sleep(min(max((step["at"] - previous) * 0.6, 0.5), 1.6))
            previous = step.get("at", previous)
            progress(index, step)
            if step["kind"] == "set_text":
                following = steps[index + 1]["kind"] if index + 1 < len(steps) else None
                self.set_text(step, committed=following == "press_enter")
            else:
                getattr(self, step["kind"])(step)
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
        runner = StepRunner(desktop)

        def progress(index, step):
            nonlocal done
            done = index
        runner.run(payload["steps"], progress)
        skipped = f" Skipped {runner.skipped} text step(s) that were only field placeholders or focus changes." if runner.skipped else ""
        result = {"ok": True, "detail": f"Replayed {len(payload['steps'])} steps.{skipped} Check the apps to confirm the result."}
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
