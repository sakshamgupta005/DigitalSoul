"""Explicit, app-scoped recorder driven by the notch's Start/Stop controls."""
import contextlib
import queue
import re
import sys
import time
from pathlib import PureWindowsPath

from .events import WinEvents
from .flow import Capture, Native, ancestry, header, locator, validate
from .whatsapp import executable, WHATSAPP_EXES, is_whatsapp


def matches_send_snapshot(snapshot, event):
    if not snapshot or event["kind"] != "click" or not event.get("point"):
        return False
    allowed_pids = snapshot.get("pids", {snapshot.get("pid")})
    if event["pid"] not in allowed_pids:
        return False
    age = event["time"] - snapshot["time"]
    x, y = event["point"]
    left, top, right, bottom = snapshot["bounds"]
    return 0 <= age <= 3 and left <= x < right and top <= y < bottom


class Session:
    def __init__(self, options):
        self.name = options.get("name", "").strip()
        if not 1 <= len(self.name) <= 80:
            raise ValueError("Give this recording a name (1–80 characters)")
        self.apps = options.get("apps", ["WhatsApp.exe"])
        if not isinstance(self.apps, list) or not 1 <= len(self.apps) <= 8 or any(
                not isinstance(app, str) or not re.fullmatch(r"[\w .-]+\.exe", app, re.I) for app in self.apps):
            raise ValueError("Choose 1–8 executable names, such as WhatsApp.exe or notepad.exe")
        self.allowed = set()
        for app in self.apps:
            name = app.lower()
            self.allowed.add(name)
            if is_whatsapp(name):
                self.allowed.update(WHATSAPP_EXES)
        self.native = Native()
        self.steps = []
        self.started = time.monotonic()
        self.last_edit = None
        self.send_snapshot = None
        self.last_foreground = None
        self.observed_paths = {}
        self.capture = None
        if options.get("recipient"):
            recipient = options["recipient"].strip()
            message = options.get("message", "hello")
            search = options.get("search", recipient).strip() or recipient
            title = options.get("chat_title", recipient).strip() or recipient
            if not message or len(message) > 4096 or any(ord(c) < 32 for c in message) or search == message:
                raise ValueError("Use a distinct contact search and single-line message (max 4096 characters)")
            self.capture = Capture(recipient, search, title, message)
        self.replay_error = None
        self.events = WinEvents()
        self.events.__enter__()

    def _active_pids(self, pid, path):
        pids = {pid}
        if is_whatsapp(path):
            try:
                proc = self.native.psutil.Process(pid)
                for child in proc.children(recursive=True):
                    pids.add(child.pid)
            except (OSError, self.native.psutil.Error):
                pass
        return pids

    def _foreground(self):
        user = self.native.user
        import ctypes
        from ctypes import wintypes
        hwnd = user.GetForegroundWindow()
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        path = self.observed_paths.get(pid.value)
        if path is None:
            path = executable(pid.value)
            self.observed_paths[pid.value] = path
        if PureWindowsPath(path).name.lower() not in self.allowed:
            return None
        window = self.native.desktop.window(handle=hwnd).wrapper_object()
        active_pids = self._active_pids(pid.value, path)
        self.events.pids.update(active_pids)
        return window, pid.value, path, active_pids

    def append(self, step):
        if len(self.steps) >= 500:
            raise ValueError("Recording reached 500 steps; stop and save it")
        step["at"] = round(time.monotonic() - self.started, 2)
        if step["kind"] == "set_text" and self.steps and self.steps[-1]["kind"] == "set_text" and (
                self.steps[-1].get("target") == step.get("target") and self.steps[-1].get("path") == step.get("path")):
            self.steps[-1] = step
        else:
            self.steps.append(step)

    def poll(self):
        if self.events.error:
            raise RuntimeError("Windows input event recording failed") from self.events.error
        if time.monotonic() - self.started > 300:
            return "limit"
        active = None
        try:
            active = self._foreground()
        except (OSError, ValueError, self.native.psutil.Error):
            pass
        if active:
            window, pid, path, active_pids = active
            if self.last_foreground != path:
                self.append({"kind": "open_app", "app": PureWindowsPath(path).name, "path": path})
                self.last_foreground = path
            if self.capture and "executable" not in self.capture.flow and is_whatsapp(path):
                self.capture.flow.update(executable=path, app_id=self.native.app_id(pid))
        while True:
            try:
                event = self.events.queue.get_nowait()
            except queue.Empty:
                break
            if event["kind"] == "stop":
                return "stop"
            if not active or event["pid"] not in active_pids:
                continue
            if time.monotonic() - event["time"] > 2:
                self.replay_error = "Input events arrived too late for reliable replay; evidence saved for review"
                continue
            try:
                if event["kind"] == "enter":
                    self.append({"kind": "press_enter", "app": PureWindowsPath(path).name, "path": path})
                    if self.capture:
                        if not self.capture.composed and is_whatsapp(path):
                            try:
                                focus = self.native.focused()
                                if focus.element_info.process_id in active_pids and focus.element_info.control_type == "Edit":
                                    val = (focus.get_value() or "").strip()
                                    if val and val != self.capture.flow["search"].strip():
                                        ident = header(window, self.capture.flow["chat_title"])
                                        spec_edit = locator(focus, editable=True)
                                        with contextlib.redirect_stdout(sys.stderr):
                                            self.capture.field(spec_edit, val, ident)
                            except Exception:
                                pass
                        if self.capture.composed:
                            self.capture.enter()
                else:
                    # Send can disappear before post-click hit-testing. Use only a
                    # recent, process-bound UIA observation to recognize the demo.
                    # These pixels are transient evidence and are never replayed.
                    if self.capture and is_whatsapp(path) and not self.capture.composed:
                        try:
                            focus = self.native.focused()
                            if focus.element_info.process_id in active_pids and focus.element_info.control_type == "Edit":
                                val = (focus.get_value() or "").strip()
                                if val and val != self.capture.flow["search"].strip():
                                    ident = header(window, self.capture.flow["chat_title"])
                                    spec_edit = locator(focus, editable=True)
                                    with contextlib.redirect_stdout(sys.stderr):
                                        self.capture.field(spec_edit, val, ident)
                        except Exception:
                            pass
                    if self.capture and self.capture.composed and matches_send_snapshot(self.send_snapshot, event):
                        snapshot = self.send_snapshot
                        with contextlib.redirect_stdout(sys.stderr):
                            self.capture.click(snapshot["target"], snapshot["name"], "Button")
                        self.append({"kind": "click", "app": PureWindowsPath(path).name, "path": path,
                                     "label": "Send", "target": snapshot["target"]})
                        continue
                    control = self.native.desktop.from_point(*event["point"])
                    if control.element_info.process_id not in active_pids:
                        continue
                    for _ in range(5):
                        name, kind = control.element_info.name or "", control.element_info.control_type
                        if name and kind in ("Edit", "Button", "ListItem", "Text"):
                            break
                        parent = control.parent()
                        if parent is None:
                            break
                        control = parent
                    spec = locator(control, editable=kind == "Edit")
                    self.append({"kind": "click", "app": PureWindowsPath(path).name, "path": path,
                                 "label": name if kind != "Edit" else "Text field", "target": spec})
                    if self.capture and is_whatsapp(path):
                        with contextlib.redirect_stdout(sys.stderr):
                            self.capture.click(spec, name, kind)
            except Exception:
                self.replay_error = "A clicked control disappeared before UIA could resolve it; record more slowly for replay"
        if not active:
            return None
        try:
            focus = self.native.focused()
            if focus.element_info.process_id not in active_pids or focus.element_info.control_type != "Edit":
                return None
            if focus.element_info.element.CurrentIsPassword:
                return None
            value = focus.get_value()
            if not isinstance(value, str) or len(value) > 4096:
                return None
            value_clean = value.strip()
            if not value_clean:
                return None
            spec = locator(focus, editable=True)
            self.last_edit = spec
            if not self.steps or self.steps[-1].get("value") != value_clean or self.steps[-1].get("target") != spec:
                self.append({"kind": "set_text", "app": PureWindowsPath(path).name, "path": path,
                             "label": "Text field", "target": spec, "value": value_clean})
            if self.capture and not self.capture.done and is_whatsapp(path):
                is_msg = (value_clean == self.capture.flow["message"].strip()) or (not self.capture.composed and value_clean != self.capture.flow["search"].strip())
                identity = header(window, self.capture.flow["chat_title"]) if is_msg else None
                with contextlib.redirect_stdout(sys.stderr):
                    self.capture.field(spec, value_clean, identity)
                if self.capture.composed and value_clean == self.capture.flow["message"].strip():
                    buttons = [b for b in window.descendants(control_type="Button")
                               if b.is_visible() and b.is_enabled() and
                               ((b.element_info.name or "").lower().startswith("send") or
                                "send" in (b.element_info.automation_id or "").lower())]
                    if len(buttons) == 1:
                        button = buttons[0]
                        rect = button.rectangle()
                        self.send_snapshot = {"pid": pid, "pids": active_pids, "time": time.monotonic(),
                                              "bounds": (rect.left, rect.top, rect.right, rect.bottom),
                                              "target": locator(button), "name": button.element_info.name or "Send"}
                elif value_clean != self.capture.flow["message"].strip():
                    self.send_snapshot = None
        except Exception:
            # Raw evidence remains reviewable; missing identity must never create a runnable flow.
            self.replay_error = "Some editor/header evidence was unavailable; inspect the saved steps"
        return None

    def finish(self):
        self.events.__exit__(None, None, None)
        flow = None
        if self.capture and self.capture.done and not self.replay_error:
            try:
                validate(self.capture.flow)
                flow = self.capture.flow
            except (ValueError, KeyError) as exc:
                self.replay_error = str(exc)
        if self.capture and not flow and not self.replay_error:
            self.replay_error = "Search, contact click, exact message, verified header and Send must all be observed for replay"
        return {"name": self.name, "apps": self.apps, "steps": self.steps, "flow": flow,
                "duration": round(time.monotonic() - self.started, 1), "replay_error": self.replay_error}
