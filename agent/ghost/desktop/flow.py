"""Record/replay the bounded WhatsApp launch/search/select/compose/send flow."""
import ctypes
import os
import queue
import re
import time
from ctypes import wintypes
from pathlib import PureWindowsPath

from .whatsapp import libraries, executable, WHATSAPP_EXES, is_whatsapp

FORMAT = "digitalsoul.whatsapp-flow.v1"


def ancestry(control):
    result = []
    for _ in range(12):
        control = control.parent()
        if control is None or control.element_info.control_type == "Window":
            break
        info = control.element_info
        result.append([info.control_type, info.automation_id or "", info.class_name or ""])
    return result


def locator(control, editable=False):
    info = control.element_info
    result = {"control_type": info.control_type, "automation_id": info.automation_id or "",
              "name": "" if editable else (info.name or ""), "ancestors": ancestry(control)}
    if not result["automation_id"] and not result["name"] and not result["ancestors"]:
        raise ValueError("Control has no reusable UIA identity")
    return result


def _ancestors_match(actual, expected):
    if not expected or actual == expected:
        return True
    if len(actual) != len(expected):
        return False
    for (a_type, a_id, a_cls), (e_type, e_id, e_cls) in zip(actual, expected):
        if a_type != e_type:
            return False
        if a_id != e_id and not (a_id.startswith("mount_") and e_id.startswith("mount_")):
            return False
    return True


def find(window, spec):
    query = {"control_type": spec["control_type"]}
    name_spec = spec.get("name", "").strip().lower()
    matches = [item for item in window.descendants(**query)
               if item.is_visible() and item.is_enabled()
               and (not spec.get("automation_id") or item.element_info.automation_id == spec["automation_id"])
               and (not name_spec or 
                    (item.element_info.name or "").strip().lower() == name_spec or
                    (item.element_info.name or "").strip().lower().startswith(name_spec + " "))
               and _ancestors_match(ancestry(item), spec.get("ancestors", []))]
    unique = []
    seen = set()
    for m in matches:
        try:
            rid = tuple(m.element_info.runtime_id)
        except Exception:
            rid = m.element_info.handle
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(m)
    if not unique:
        raise ValueError("Recorded UIA target changed or is ambiguous; record again")
    return unique[0]


def header(window, title):
    candidates = []
    seen = set()
    target_clean = title.strip().lower()
    for ctype in ("Button", "Text"):
        for item in window.descendants(control_type=ctype):
            name = (item.element_info.name or "").strip()
            name_clean = name.lower()
            if not (name_clean == target_clean or name_clean.startswith(target_clean + " ") or name_clean.splitlines()[0] == target_clean):
                continue
            if not item.is_visible():
                continue
            chain = ancestry(item)
            if any(entry[0] in ("List", "ListItem", "Tree", "TreeItem") for entry in chain):
                continue
            try:
                rid = tuple(item.element_info.runtime_id)
            except Exception:
                rid = item.element_info.handle
            if rid in seen:
                continue
            seen.add(rid)
            candidates.append(item)
    if len(candidates) != 1:
        raise ValueError("Cannot identify a unique chat header outside the contact list. Use --chat-title with its exact displayed name")
    result = locator(candidates[0])
    result["name"] = title.strip()
    return result


def validate(flow):
    if flow.get("format") != FORMAT:
        raise ValueError("Unsupported flow recording")
    if not flow.get("recipient") or not flow.get("search") or not flow.get("chat_title"):
        raise ValueError("Recording needs a recipient, search query and exact chat title")
    path = PureWindowsPath(flow["executable"])
    if not path.is_absolute() or not is_whatsapp(path):
        raise ValueError("Only the recorded WhatsApp executable can be launched")
    if flow["header"]["name"].strip().lower() != flow["chat_title"].strip().lower():
        raise ValueError("Recorded chat identity does not match")
    actions = flow["actions"]
    kinds = [a.get("kind") for a in actions]
    if not 2 <= len(actions) <= 20 or kinds.count("search") != 1 or kinds[-1] != "compose":
        raise ValueError("Recording must contain search and end with compose")
    if any(kind not in ("navigate", "search", "select", "compose") for kind in kinds):
        raise ValueError("Unsupported recorded action")
    if not any(k == "select" for k in kinds[kinds.index("search") + 1:-1]):
        raise ValueError("Record clicking the intended search result after searching")
    if flow["send"]["kind"] not in ("enter", "button"):
        raise ValueError("Recording has no observed send action")
    if not flow.get("message") or any(ord(c) < 32 for c in flow["message"]):
        raise ValueError("Record a single-line message")


class Native:
    def __init__(self):
        self.desktop, self.psutil = libraries()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def active(self, path=None):
        hwnd = self.user.GetForegroundWindow()
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            raise ValueError("No foreground desktop window")
        current = executable(pid.value)
        if not is_whatsapp(current) or (path and current != path):
            raise ValueError("WhatsApp must be foreground on an unlocked desktop")
        return self.desktop.window(handle=hwnd).wrapper_object(), pid.value, current

    def focused(self):
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.controls.uiawrapper import UIAWrapper
        return UIAWrapper(UIAElementInfo(IUIA().iuia.GetFocusedElement()))

    def app_id(self, pid):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetApplicationUserModelId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        process = kernel.OpenProcess(0x1000, False, pid)
        if not process:
            return None
        try:
            length = wintypes.UINT(512)
            buffer = ctypes.create_unicode_buffer(512)
            if kernel.GetApplicationUserModelId(process, ctypes.byref(length), buffer) == 0:
                return buffer.value
        finally:
            kernel.CloseHandle(process)
        return None

    def open(self, flow):
        # Restore an existing recorded process first. No arbitrary shell commands.
        matches = []
        for window in self.desktop.windows():
            try:
                if executable(window.element_info.process_id) == flow["executable"]:
                    matches.append(window)
            except (OSError, self.psutil.Error):
                continue
        if len(matches) > 1:
            raise ValueError("Multiple WhatsApp windows found; close extras before replay")
        if matches:
            matches[0].restore()
            matches[0].set_focus()
            return
        app_id = flow.get("app_id")
        if app_id:
            if not re.fullmatch(r"[\w.!-]{1,512}", app_id) or "whatsapp" not in app_id.lower():
                raise ValueError("Invalid recorded WhatsApp application identity")
            os.startfile("shell:AppsFolder\\" + app_id)
        else:
            os.startfile(flow["executable"])

    def click(self, flow, spec):
        window, _, _ = self.active(flow["executable"])
        target = find(window, spec)
        # The point comes from the freshly resolved control, never recorded pixels.
        self.active(flow["executable"])
        target.click_input()


class FlowAdapter:
    def __init__(self, native=None, guard=None):
        self.native = native
        self.guard = guard or (lambda: True)

    def _allow(self):
        if not self.guard():
            raise ValueError("Desktop job stopped or cancelled")

    def _window(self, flow):
        self._allow()
        return self.native.active(flow["executable"])[0]

    def _wait(self, callback):
        deadline = time.monotonic() + 8
        while True:
            self._allow()
            try:
                return callback()
            except (ValueError, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.15)

    def prepare(self, flow, message):
        validate(flow)
        self.native = self.native or Native()
        self._allow()
        self.native.open(flow)
        self._wait(lambda: self._window(flow))
        for action in flow["actions"]:
            if action["kind"] in ("navigate", "select"):
                self._wait(lambda: find(self._window(flow), action["target"]))
                self._allow()
                self.native.click(flow, action["target"])
            else:
                if action["kind"] == "compose":
                    self._wait(lambda: find(self._window(flow), flow["header"]))
                target = self._wait(lambda: find(self._window(flow), action["target"]))
                value = message if action["kind"] == "compose" else flow["search"]
                if action["kind"] == "compose" and target.get_value() and target.get_value().strip():
                    raise ValueError("Existing message draft will not be overwritten")
                self._allow()
                try:
                    target.set_edit_text(value)
                except Exception:
                    pass
                val = target.get_value() or ""
                if val.strip() != value.strip():
                    target.set_focus()
                    target.type_keys("^a{BACKSPACE}", set_foreground=False)
                    escaped = re.sub(r'([+^%~{}()])', r'{\1}', value)
                    target.type_keys(escaped, with_spaces=True, set_foreground=False)
                self._wait(lambda: self._value_matches(flow, action["target"], value))
        self.ready(flow, message)

    def _value_matches(self, flow, spec, value):
        target = find(self._window(flow), spec)
        val = target.get_value() or ""
        if val != value and val.strip() != value.strip():
            raise ValueError("Recorded field value has changed")
        return target

    def ready(self, flow, message):
        find(self._window(flow), flow["header"])
        return self._value_matches(flow, flow["actions"][-1]["target"], message)

    def send(self, flow, message):
        composer = self.ready(flow, message)
        if flow["send"]["kind"] == "button":
            self.native.click(flow, flow["send"]["target"])
        else:
            composer.set_focus()
            self.ready(flow, message)
            composer.type_keys("{ENTER}", set_foreground=False)


class Capture:
    """State machine fed only explicit search/message values and scoped clicks."""
    def __init__(self, recipient, search, chat_title, message):
        self.flow = {"format": FORMAT, "recipient": recipient, "search": search,
                     "chat_title": chat_title, "message": message, "actions": []}
        self.composed = False
        self.done = False

    def field(self, spec, value, chat_header=None):
        actions = self.flow["actions"]
        val_clean = (value or "").strip()
        if not val_clean:
            return
        if val_clean.lower() == self.flow["search"].strip().lower() and not any(a["kind"] == "search" for a in actions):
            actions.append({"kind": "search", "target": spec})
            self.flow["search"] = val_clean
            print("Recorded search text.")
        elif (val_clean == self.flow["message"].strip() or (not self.composed and any(a["kind"] == "select" for a in actions) and val_clean != self.flow["search"].strip())) and chat_header and not self.composed:
            if not any(a["kind"] == "select" for a in actions):
                raise ValueError("Click the intended search result before composing")
            self.flow["header"] = chat_header
            self.flow["chat_title"] = chat_header.get("name") or self.flow["chat_title"]
            self.flow["message"] = val_clean
            actions.append({"kind": "compose", "target": spec})
            self.composed = True
            print("Recorded exact message and current chat header. Now send once to finish the demonstration.")

    def click(self, spec, name, control_type):
        actions = self.flow["actions"]
        if self.composed:
            if name.lower().startswith("send") or "send" in spec["automation_id"].lower():
                self.flow["send"] = {"kind": "button", "target": spec}
                self.done = True
            return
        if control_type in ("Edit", "Document"):
            return
        if any(a["kind"] == "search" for a in actions):
            target_names = {self.flow["chat_title"].strip().lower(), self.flow["recipient"].strip().lower()}
            clean_name = name.strip().lower()
            first_line = clean_name.splitlines()[0].strip() if clean_name else ""
            if (clean_name in target_names or 
                first_line in target_names or 
                any(t in clean_name for t in target_names if len(t) >= 3)):
                actions.append({"kind": "select", "target": spec})
                print("Recorded recipient selection.")
        elif control_type == "Button" and name.lower() in ("search", "new chat", "new message"):
            actions.append({"kind": "navigate", "target": spec})

    def enter(self):
        if self.composed:
            self.flow["send"] = {"kind": "enter"}
            self.done = True


def record_flow(recipient, search, chat_title, message, seconds=120):
    from .events import WinEvents
    if search == message or not message or len(message) > 4096 or any(ord(c) < 32 for c in message):
        raise ValueError("Use distinct search text and a 1–4096 character single-line message")
    native = Native()
    capture = Capture(recipient, search, chat_title, message)
    print(f"Recording for up to {seconds} seconds. F8 stops without saving an incomplete flow.")
    print(f"Open WhatsApp, type the full search query {search!r}, CLICK the verified contact, type {message!r}, then send once.")
    print("The demonstration sends a real message. Pause briefly after typing search and message so UIA can observe them.")
    deadline = time.monotonic() + seconds
    with WinEvents() as events:
        while time.monotonic() < deadline and not capture.done:
            if events.error:
                raise RuntimeError("Input recording failed") from events.error
            try:
                window, pid, path = native.active(capture.flow.get("executable"))
            except (ValueError, OSError, native.psutil.Error):
                time.sleep(0.05)
                continue
            active_pids = {pid}
            if is_whatsapp(path):
                try:
                    for child in native.psutil.Process(pid).children(recursive=True):
                        active_pids.add(child.pid)
                except (OSError, native.psutil.Error):
                    pass
            events.pids.update(active_pids)
            if "executable" not in capture.flow:
                capture.flow.update(executable=path, app_id=native.app_id(pid))
                print("Observed WhatsApp opening/activation.")
            # Resolve pending click events BEFORE reading the newly focused editor.
            while True:
                try:
                    event = events.queue.get_nowait()
                except queue.Empty:
                    break
                if event["kind"] == "stop":
                    raise ValueError("Recording stopped; no completed flow saved")
                if event["pid"] not in active_pids:
                    continue
                if time.monotonic() - event["time"] > 2:
                    raise ValueError("Input processing fell behind; repeat the demonstration more slowly")
                if event["kind"] == "enter":
                    capture.enter()
                elif event["kind"] == "click":
                    control = native.desktop.from_point(*event["point"])
                    if control.element_info.process_id not in active_pids:
                        continue
                    # Preserve exact contact text; lift icons inside actionable buttons.
                    for _ in range(5):
                        name, kind = control.element_info.name or "", control.element_info.control_type
                        if name and kind in ("Edit", "Button", "ListItem", "Text"):
                            break
                        parent = control.parent()
                        if parent is None:
                            break
                        control = parent
                    capture.click(locator(control), name, kind)
            if capture.done:
                break
            try:
                focus = native.focused()
                if focus.element_info.process_id in active_pids and focus.element_info.control_type == "Edit":
                    value = (focus.get_value() or "").strip()
                    if value:
                        chat_header = header(window, chat_title) if value == message.strip() else None
                        capture.field(locator(focus, editable=True), value, chat_header)
            except (ValueError, OSError) as exc:
                # Unknown header while composing is a hard stop, never a guessed recipient.
                if 'value' in locals() and value == message.strip() and not capture.composed:
                    raise ValueError(str(exc)) from exc
            time.sleep(0.04)
    if not capture.done:
        raise ValueError("No complete search/select/message/send demonstration observed. Nothing saved; use F8 to stop or try again")
    validate(capture.flow)
    return capture.flow
