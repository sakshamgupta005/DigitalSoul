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

# Chromium browsers spread one window across child processes.
BROWSER_EXES = {"chrome.exe", "brave.exe", "msedge.exe"}
# "Everything" mode still never watches DigitalSoul itself, sign-in prompts or password managers.
NEVER_RECORD = {"digitalsoul-notch.exe", "consent.exe", "lockapp.exe", "credentialuibroker.exe", "logonui.exe",
                "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe", "lastpass.exe"}


def field_text(control, browser=False):
    """Typed text of a focused edit. Browser rich-text boxes expose it only via TextPattern."""
    try:
        value = control.get_value()
    except Exception:
        value = None
    if not browser:
        if value is None and control.element_info.control_type == "Document":
            return document_text(control)
        return value
    name = (control.element_info.name or "").strip().rstrip("….�").strip()

    def typed(text):
        # Chromium reports the placeholder or label as text while a field is empty.
        text = (text or "").strip() if isinstance(text, str) else ""
        return "" if text.rstrip("….�").strip() == name else text

    if typed(value):
        return typed(value)
    return typed(document_text(control))


def document_text(control, limit=4096):
    """Text of a document surface (Word body, rich web editors), or None when unavailable or too long."""
    try:
        text = control.iface_text.DocumentRange.GetText(limit + 1)
    except Exception:
        return None
    return text if isinstance(text, str) and len(text) <= limit else None


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
                not isinstance(app, str) or not (app == "*" or re.fullmatch(r"[\w .-]+\.exe", app, re.I)) for app in self.apps):
            raise ValueError("Choose 1–8 executable names, such as WhatsApp.exe or notepad.exe")
        self.everything = "*" in self.apps
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
        self.focus_key = self.focus_base = None
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
        elif options.get("whatsapp"):
            self.capture = Capture()
        self.replay_error = None
        self.events = WinEvents()
        self.events.__enter__()

    def _active_pids(self, pid, path):
        pids = {pid}
        if is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES:
            try:
                proc = self.native.psutil.Process(pid)
                for child in proc.children(recursive=True):
                    pids.add(child.pid)
            except (OSError, self.native.psutil.Error):
                pass
        return pids

    def _identity(self, window):
        try:
            return header(window, self.capture.flow["chat_title"])
        except ValueError:
            # A clicked search result can carry extra text; the header usually matches the search.
            return header(window, self.capture.flow["search"])

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
        exe = PureWindowsPath(path).name.lower()
        if exe in NEVER_RECORD or not (self.everything or exe in self.allowed):
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
                step = {"kind": "open_app", "app": PureWindowsPath(path).name, "path": path}
                if "\\windowsapps\\" in path.lower():
                    # Store apps cannot be started from their executable; replay launches the app identity.
                    step["app_id"] = self.native.app_id(pid)
                self.append(step)
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
                                    if val and (not self.capture.auto or self.capture.selected) and val != self.capture.flow["search"].strip():
                                        ident = self._identity(window)
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
                                if val and (not self.capture.auto or self.capture.selected) and val != self.capture.flow["search"].strip():
                                    ident = self._identity(window)
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
            kinds = ("Edit",) if is_whatsapp(path) else ("Edit", "Document")
            if focus.element_info.process_id not in active_pids or focus.element_info.control_type not in kinds:
                return None
            if focus.element_info.element.CurrentIsPassword:
                return None
            value = field_text(focus, PureWindowsPath(path).name.lower() in BROWSER_EXES)
            if not isinstance(value, str) or len(value) > 4096:
                return None
            value_clean = value.strip()
            try:
                key = tuple(focus.element_info.runtime_id)
            except Exception:
                key = (path, focus.element_info.control_type, focus.element_info.automation_id)
            if key != self.focus_key:
                # Text already in a field when it gains focus (a URL, a placeholder) was not typed by the user.
                self.focus_key, self.focus_base = key, value_clean
            if not value_clean or value_clean == self.focus_base:
                return None
            spec = locator(focus, editable=True)
            self.last_edit = spec
            if not self.steps or self.steps[-1].get("value") != value_clean or self.steps[-1].get("target") != spec:
                self.append({"kind": "set_text", "app": PureWindowsPath(path).name, "path": path,
                             "label": "Text field", "target": spec, "value": value_clean})
            if self.capture and not self.capture.done and is_whatsapp(path):
                is_msg = (value_clean == self.capture.flow["message"].strip()) or (not self.capture.composed and (not self.capture.auto or self.capture.selected) and value_clean != self.capture.flow["search"].strip())
                identity = self._identity(window) if is_msg else None
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
