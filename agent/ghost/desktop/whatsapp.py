"""Guided WhatsApp teaching and fresh, foreground-only UIA execution.

No keylogging, chat-history collection, arbitrary script replay, or coordinate replay.
Blinky windows/python/uia.py informs process scoping and fresh element resolution.
"""
from .preparation import PreparationError, diagnosed_prepare

import ctypes
import os
import sys
import time
from pathlib import Path, PureWindowsPath


def attach_default_desktop():
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass


def libraries():
    if sys.platform != "win32":
        raise RuntimeError("This adapter requires Windows")
    attach_default_desktop()
    try:
        from pywinauto import Desktop
        import psutil
    except ImportError as exc:
        missing = getattr(exc, "name", None) or "Windows automation dependency"
        python = f'"{sys.executable}"'
        if missing in ("win32api", "pywintypes", "pythoncom", "win32com"):
            repair = f'{python} -m pip install --ignore-installed --no-deps "pywin32>=311,<400"'
        else:
            repair = f'{python} -m pip install -e ".[windows]"'
        raise RuntimeError(f"Cannot load {missing}. Repair this Python environment with: {repair}") from exc
    return Desktop(backend="uia"), psutil


def executable(pid):
    _, psutil = libraries()
    return os.path.normcase(str(Path(psutil.Process(pid).exe()).resolve()))


def hosted_pid(hwnd, pid, user=None):
    """Store apps (Calculator, Settings) draw inside an ApplicationFrameHost frame; the frame's
    CoreWindow child belongs to the app itself. Returns that app's pid, else `pid`."""
    if not hwnd:
        return pid
    user = user or ctypes.windll.user32
    child = user.FindWindowExW(hwnd, None, "Windows.UI.Core.CoreWindow", None)
    owner = ctypes.c_ulong()
    if child and user.GetWindowThreadProcessId(child, ctypes.byref(owner)) and owner.value:
        return owner.value
    return webview_host(pid)


def webview_host(pid):
    """WebView2 apps (WhatsApp) put their content in msedgewebview2.exe windows, and Windows can report
    one of those as the foreground window while you type. The app is the first non-WebView2 parent."""
    try:
        import psutil
        proc = psutil.Process(pid)
        for _ in range(6):
            if proc.name().lower() != "msedgewebview2.exe":
                return proc.pid
            proc = proc.parent()
            if proc is None:
                break
    except Exception:
        pass
    return pid


def selector(control, role):
    info = control.element_info
    result = {"control_type": info.control_type, "automation_id": info.automation_id or ""}
    # Composer accessible names may contain a draft. Never store that text.
    result["name"] = "" if role == "composer" and result["automation_id"] else (info.name or "")
    if role == "composer" and not result["automation_id"]:
        raise ValueError("Message box must expose a stable AutomationId; coordinate fallback is disabled")
    if not result["automation_id"] and not result["name"]:
        raise ValueError("Control has no stable selector")
    return result


def resolve(window, spec):
    kwargs = {"control_type": spec["control_type"]}
    if spec["name"]:
        kwargs["title"] = spec["name"]
    matches = [c for c in window.descendants(**kwargs)
               if c.is_visible() and c.is_enabled()
               and (not spec.get("automation_id") or getattr(getattr(c, "element_info", None), "automation_id", None) == spec["automation_id"])]
    unique = []
    seen = set()
    for m in matches:
        info = getattr(m, "element_info", None)
        rid = getattr(info, "runtime_id", None) if info else None
        if rid:
            rid = tuple(rid)
        else:
            rid = getattr(info, "handle", id(m))
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(m)
    if len(unique) != 1:
        raise ValueError("Recorded control is missing, disabled, or ambiguous")
    return unique[0]


WHATSAPP_EXES = {"whatsapp.exe", "whatsapp.root.exe"}


def is_whatsapp(path_or_name):
    if not path_or_name:
        return False
    return PureWindowsPath(str(path_or_name)).name.lower() in WHATSAPP_EXES


def validate_recipe(recipe):
    if recipe.get("format") == "digitalsoul.whatsapp-send.v1":
        from .whatsapp_send import validate
        return validate(recipe)
    if recipe.get("format") == "digitalsoul.whatsapp-flow.v1":
        from .flow import validate
        return validate(recipe)
    if recipe.get("format") != "digitalsoul.whatsapp-teaching.v1":
        raise ValueError("Unsupported teaching format")
    if not recipe.get("recipient") or not recipe.get("identity_verified"):
        raise ValueError("Recipient was not verified during teaching")
    path = PureWindowsPath(recipe["executable"])
    if not is_whatsapp(path) or not path.is_absolute():
        raise ValueError("Only an explicitly taught WhatsApp executable is supported")
    controls = recipe["controls"]
    if controls["header"]["name"] != recipe["recipient"]:
        raise ValueError("Header must exactly identify the reviewed contact")
    if not controls["header"]["automation_id"]:
        raise ValueError("Chat header needs a stable AutomationId; a name-only match could identify a sidebar contact")
    if controls["composer"]["control_type"] != "Edit" or not controls["composer"]["automation_id"]:
        raise ValueError("A stable Edit control is required")
    if controls["send"]["control_type"] != "Button":
        raise ValueError("A UIA Send button is required")


def find_whatsapp_executable():
    """Find installed WhatsApp desktop executable or running instance."""
    try:
        import psutil
        for p in psutil.process_iter(["name", "exe"]):
            try:
                if p.name().lower() in WHATSAPP_EXES:
                    return os.path.normcase(str(Path(p.exe()).resolve()))
            except Exception:
                pass
    except Exception:
        pass
    import glob
    base = r"C:\Program Files\WindowsApps"
    try:
        matches = glob.glob(base + r"\5319275A.WhatsAppDesktop_*_x64__*\WhatsApp.Root.exe")
        if matches:
            return os.path.normcase(matches[0])
        matches = glob.glob(base + r"\5319275A.WhatsAppDesktop_*_x64__*\WhatsApp.exe")
        if matches:
            return os.path.normcase(matches[0])
    except Exception:
        pass
    local = os.path.expandvars(r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe")
    if os.path.exists(local):
        return os.path.normcase(local)
    return r"C:\Program Files\WindowsApps\WhatsApp.exe"


class WhatsApp:
    def set_guard(self, guard):
        self._guard = guard

    def window(self, recipe):
        validate_recipe(recipe)
        desktop, _ = libraries()
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        handle = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        rec_exe = os.path.normcase(str(recipe.get("executable", ""))) if recipe.get("executable") else None
        if not pid.value or (rec_exe and executable(pid.value) != rec_exe):
            try:
                top = desktop.window(handle=handle).top_level_parent()
                if not rec_exe or executable(top.element_info.process_id) == rec_exe:
                    handle = top.handle
                    pid = wintypes.DWORD(top.element_info.process_id)
            except Exception:
                pass
        if not pid.value or (rec_exe and executable(pid.value) != rec_exe):
            for w in desktop.windows():
                try:
                    w_exe = executable(w.element_info.process_id)
                    if (rec_exe and w_exe == rec_exe) or (not rec_exe and is_whatsapp(w_exe)):
                        handle = w.handle
                        pid = wintypes.DWORD(w.element_info.process_id)
                        user32.ShowWindow(handle, 9)
                        user32.SwitchToThisWindow(handle, True)
                        user32.SetForegroundWindow(handle)
                        time.sleep(0.2)
                        break
                except Exception:
                    continue
        if not pid.value or (rec_exe and executable(pid.value) != rec_exe):
            raise ValueError("Keep the recorded WhatsApp window foreground on an unlocked desktop")
        window = desktop.window(handle=handle).wrapper_object()
        if not window.is_visible() or not window.is_enabled():
            raise ValueError("WhatsApp is unavailable")
        if recipe.get("format") == "digitalsoul.whatsapp-teaching.v1" and "controls" in recipe:
            resolve(window, recipe["controls"]["header"])
        return window

    def check(self, recipe):
        from .whatsapp_send import control_text
        self.preparation_stage = "window"
        window = self.window(recipe)
        self.preparation_stage = "composer"
        composer = resolve(window, recipe["controls"]["composer"])
        val = control_text(composer)
        if val and val.strip():
            raise PreparationError("draft")
        return True

    @diagnosed_prepare
    def prepare(self, recipe, message):
        if recipe.get("format") == "digitalsoul.whatsapp-send.v1":
            from .whatsapp_send import WhatsAppSend
            self._flow_adapter = WhatsAppSend(guard=getattr(self, "_guard", None), ghost=None)
            return self._flow_adapter.prepare(recipe, message)
        if recipe.get("format") == "digitalsoul.whatsapp-flow.v1":
            try:
                from .flow import FlowAdapter
                self._flow_adapter = FlowAdapter(guard=getattr(self, "_guard", None))
                return self._flow_adapter.prepare(recipe, message)
            except PreparationError:
                raise
            except Exception:
                from .whatsapp_send import WhatsAppSend
                self._flow_adapter = WhatsAppSend(guard=getattr(self, "_guard", None), ghost=None)
                return self._flow_adapter.prepare(recipe, message)
        from .whatsapp_send import control_text
        self.preparation_stage = "composer"
        self.check(recipe)
        composer = resolve(self.window(recipe), recipe["controls"]["composer"])
        self.preparation_stage = "type"
        # Literal ValuePattern assignment: braces and + are not keyboard commands.
        try:
            composer.set_edit_text(message)
        except Exception:
            pass
        val = control_text(composer) or ""
        if val.strip() != message.strip():
            composer.set_focus()
            composer.type_keys("^a{BACKSPACE}", set_foreground=False)
            import re
            escaped = re.sub(r'([+^%~{}()])', r'{\1}', message)
            composer.type_keys(escaped, with_spaces=True, set_foreground=False)
        self.preparation_stage = "verify"
        self._ready(recipe, message)

    def _ready(self, recipe, message):
        from .whatsapp_send import control_text
        window = self.window(recipe)  # fresh header and process identity after filling
        composer = resolve(window, recipe["controls"]["composer"])
        val = control_text(composer) or ""
        if val != message and val.strip() != message.strip():
            raise ValueError("Composer differs from the approved message")
        return resolve(window, recipe["controls"]["send"])

    def send(self, recipe, message):
        if recipe.get("format") in ("digitalsoul.whatsapp-flow.v1", "digitalsoul.whatsapp-send.v1"):
            return self._flow_adapter.send(recipe, message)
        self._ready(recipe, message).invoke()


def teach(recipient):
    desktop, _ = libraries()
    print("Open the intended one-to-one WhatsApp chat. Verify its contact phone number yourself.")
    print("Enter a harmless draft so Send is visible. Do NOT press Send during teaching.")
    if input("After verifying the contact, type its exact chat header: ").strip() != recipient:
        raise ValueError("Contact confirmation does not match")
    recipe = {"format": "digitalsoul.whatsapp-teaching.v1", "recipient": recipient,
              "identity_verified": True, "controls": {}}
    for role in ("header", "composer", "send"):
        input(f"Press Enter, then within 5 seconds switch to WhatsApp and POINT at its {role} (do not click Send).")
        time.sleep(5)
        from ctypes import wintypes
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise ValueError("Pointer location unavailable")
        control = desktop.from_point(point.x, point.y)
        # Labels nested inside a button may be the hit-test result.
        expected = {"composer": "Edit", "send": "Button"}.get(role)
        for _ in range(4):
            if expected is None or control.element_info.control_type == expected:
                break
            control = control.parent()
        path = executable(control.element_info.process_id)
        if not is_whatsapp(path):
            raise ValueError("Point only at the WhatsApp desktop app")
        if "executable" in recipe and os.path.normcase(str(recipe["executable"])) != os.path.normcase(str(path)):
            raise ValueError("All controls must belong to the same WhatsApp installation")
        recipe["executable"] = path
        recipe["controls"][role] = selector(control, role)
        window = control.top_level_parent()
        resolve(window, recipe["controls"][role])
        print(f"Recorded unique {role} selector.")
    validate_recipe(recipe)
    # Read-only: no learned action is performed during teaching.
    print("Teaching complete. Clear your draft manually before running a job.")
    return recipe
