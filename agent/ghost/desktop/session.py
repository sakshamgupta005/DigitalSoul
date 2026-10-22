"""Explicit, app-scoped recorder driven by the notch's Start/Stop controls."""
import contextlib
import queue
import re
import sys
import threading
import time
from pathlib import PureWindowsPath

from .events import WinEvents
from .flow import Capture, Native, ancestry, header, locator, validate
from .whatsapp import executable, WHATSAPP_EXES, is_whatsapp

# Chromium browsers spread one window across child processes.
BROWSER_EXES = {"chrome.exe", "brave.exe", "msedge.exe"}
CLICKABLE_KINDS = ("Button", "ListItem", "Edit", "Hyperlink", "MenuItem", "TabItem", "CheckBox", "RadioButton",
                   "DataItem", "TreeItem", "Text", "Image", "ComboBox", "SplitButton")
# Clicking a page or window root says nothing about what was clicked (typing is captured separately).
ROOT_KINDS = ("Window", "Pane", "Document")
# "Everything" mode still never watches DigitalSoul itself, sign-in prompts or password managers.
NEVER_RECORD = {"digitalsoul-notch.exe", "consent.exe", "lockapp.exe", "credentialuibroker.exe", "logonui.exe",
                "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe", "lastpass.exe"}
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}


def clean_control_name(control, name, kind):
    """Return a clean, human-readable name for an element without noisy timestamps, internal IDs or container prefixes."""
    if not name:
        try:
            name = control.window_text() or ""
        except Exception:
            name = ""
    name = (name or "").strip()
    if name.startswith("System.") or name in ("ItemNameDisplay", "Name"):
        try:
            for child in control.children():
                c_name = (child.element_info.name or child.window_text() or "").strip()
                if c_name and not c_name.startswith("System."):
                    name = c_name
                    break
        except Exception:
            pass
    if not name:
        return "Text field" if kind == "Edit" else (kind or "control")
    first = re.sub(r"^\(\d+\)\s*", "", name).strip()
    first = re.split(r"[,\n\r]", first, maxsplit=1)[0].strip()
    return first or name


def web_address(value):
    """An address-bar value as a full http(s) URL, or None (search text, internal pages)."""
    value = (value or "").strip()
    if not value or " " in value or len(value) > 2000:
        return None
    if not re.match(r"https?://", value):
        if not re.match(r"[\w-]+(\.[\w-]+)+(:\d+)?(/|$|\?)", value):
            return None
        value = "https://" + value
    return value


def with_time(url, seconds):
    """A YouTube watch URL that resumes at `seconds`."""
    url = re.sub(r"([?&])t=\d+s?&?", r"\1", url).rstrip("?&")
    return f"{url}{'&' if '?' in url else '?'}t={int(seconds)}s"


def is_video(url):
    return bool(re.match(r"https?://(www\.|m\.)?(youtube\.com/(watch|shorts/)|youtu\.be/)", url or ""))


def compress_browsing(steps):
    """A URL step already captures where typing, Enter and link clicks in that browser led,
    so those are folded into it and replay opens the exact page directly."""
    out, droppable = [], []
    for step in steps:
        kind, path = step["kind"], step.get("path", "")
        browser = PureWindowsPath(path).name.lower() in BROWSER_EXES
        if kind == "open_url":
            drop = {i for i in droppable if out[i].get("path") == path}
            out = [s for i, s in enumerate(out) if i not in drop]
            droppable = []
            if out and out[-1]["kind"] == "open_url" and out[-1].get("path") == path:
                out.pop()  # passed straight through that page: open only where it ended up
        elif browser and kind in ("open_app", "set_text", "press_enter", "click"):
            droppable.append(len(out))
        else:
            droppable = []
        out.append(step)
    return out


def wake_tree(hwnd):
    """Walk one window's UIA tree on a worker thread; big web apps take seconds the first time."""
    try:
        import comtypes
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except Exception:
        pass
    try:
        from pywinauto import Desktop
        Desktop(backend="uia").window(handle=hwnd).wrapper_object().descendants(control_type="Edit")
    except Exception:
        pass


def field_text(control, browser=False):
    """Typed text of a focused edit. Supports ValuePattern, TextPattern, and window text."""
    value = None
    try:
        value = control.get_value()
    except Exception:
        value = None
    if value is None:
        try:
            value = control.window_text()
        except Exception:
            pass
    if value is None:
        value = document_text(control)
    if value is None:
        try:
            value = getattr(getattr(control, "iface_value", None), "CurrentValue", None)
        except Exception:
            pass
    if not browser:
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
        self.woken = set()
        self.omnibox = {}
        self.url_checked = 0.0
        self.last_url = None
        self.last_browser = None
        self.capture = None
        if options.get("recipient"):
            recipient = options["recipient"].strip()
            message = options.get("message", "hello")
            search = options.get("search", recipient).strip() or recipient
            title = options.get("chat_title", recipient).strip() or recipient
            if not message or len(message) > 4096 or any(ord(c) < 32 for c in message) or search == message:
                raise ValueError("Use a distinct contact search and single-line message (max 4096 characters)")
            self.capture = Capture(recipient, search, title, message)
        elif options.get("whatsapp") or self.everything:
            # Any recording that touches WhatsApp also learns who was searched and what was typed.
            self.capture = Capture()
        self.visual_memory = None
        if options.get("enable_visual_memory", True):
            try:
                from agent.ghost.collector.visual_memory import VisualMemory
                self.visual_memory = VisualMemory()
            except Exception:
                self.visual_memory = None
        self.shopify_active = options.get("shopify", True)
        self.shopify_detected = False
        self.shopify_data = {
            "title": "",
            "description": "",
            "price": "29.99",
            "compare_at_price": "39.99",
            "cost_per_item": "12.00",
            "sku": "",
            "inventory_quantity": 50,
            "category": "Apparel & Accessories",
            "tags": "featured, new-arrival",
            "status": "Active",
            "store_url": "https://admin.shopify.com/store/gzn0vr-qn/products/new",
        }
        self.replay_error = None
        self.events = WinEvents(everything=self.everything)
        self.events.__enter__()

    def _active_pids(self, pid, path):
        pids = {pid}
        if self.everything or is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES:
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

    def _wake(self, window):
        # Chromium, Electron and WebView2 apps (WhatsApp, Chrome, Brave) expose only their page
        # root until a UIA client walks the tree; one walk per window turns on the real controls.
        if window.handle in self.woken:
            return
        self.woken.add(window.handle)
        threading.Thread(target=wake_tree, args=(window.handle,), daemon=True).start()

    def _hit(self, window, point):
        self._wake(window)
        control = self.native.desktop.from_point(*point)
        # While Chromium builds its tree the hit-test returns the page root; retry briefly.
        for _ in range(3):
            if control.element_info.control_type not in ROOT_KINDS:
                break
            time.sleep(0.15)
            control = self.native.desktop.from_point(*point)
        return control

    def _track_url(self, window, path):
        """Browsers: remember the exact page whenever the address bar settles on a new one."""
        if PureWindowsPath(path).name.lower() not in BROWSER_EXES or time.monotonic() - self.url_checked < 1.0:
            return
        self.url_checked = time.monotonic()
        try:
            bar = self.omnibox.get(window.handle)
            if bar is None:
                bar = next(iter(window.descendants(control_type="Edit", title="Address and search bar")), None)
                if bar is None:
                    return
                self.omnibox[window.handle] = bar
            if bar.has_keyboard_focus():
                return  # the user is still typing there
            url = web_address(bar.get_value())
        except Exception:
            self.omnibox.pop(window.handle, None)
            return
        if url and url != self.last_url:
            self.last_url, self.last_browser = url, (window.handle, path)
            self.append({"kind": "open_url", "app": PureWindowsPath(path).name, "path": path, "url": url,
                         "label": re.sub(r"^https?://(www\.)?", "", url)[:80]})

    def _video_time(self):
        """On stop: if the last page is a YouTube video, note where playback is so replay resumes there."""
        if not is_video(self.last_url) or not self.last_browser:
            return
        try:
            window = self.native.desktop.window(handle=self.last_browser[0]).wrapper_object()
            seek = next(s for s in window.descendants(control_type="Slider") if "seek" in (s.element_info.name or "").lower())
            seconds = int(seek.iface_range_value.CurrentValue)
        except Exception:
            return
        for step in reversed(self.steps):
            if step["kind"] == "open_url" and step["url"] == self.last_url:
                step["url"] = with_time(step["url"], seconds)
                step["resume"] = seconds
                break

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
        if not (self.everything or exe in self.allowed):
            try:
                top = self.native.desktop.window(handle=hwnd).top_level_parent()
                top_pid = top.element_info.process_id
                top_path = self.observed_paths.get(top_pid) or executable(top_pid)
                self.observed_paths[top_pid] = top_path
                top_exe = PureWindowsPath(top_path).name.lower()
                if self.everything or top_exe in self.allowed:
                    hwnd = top.handle
                    pid = wintypes.DWORD(top_pid)
                    path = top_path
                    exe = top_exe
            except Exception:
                pass
        if exe in NEVER_RECORD or not (self.everything or exe in self.allowed):
            return None
        window = self.native.desktop.window(handle=hwnd).wrapper_object()
        if exe == "explorer.exe":
            cls = getattr(getattr(window, "element_info", None), "class_name", "") or ""
            if cls in SHELL_CLASSES:
                return None
        active_pids = self._active_pids(pid.value, path)
        self.events.pids.update(active_pids)
        return window, pid.value, path, active_pids

    def append(self, step):
        if len(self.steps) >= 500:
            raise ValueError("Recording reached 500 steps; stop and save it")
        step["at"] = round(time.monotonic() - self.started, 2)
        if self.visual_memory:
            try:
                app_name = step.get("app") or "Desktop"
                window_title = self.last_foreground or app_name
                target_name = step.get("label")
                if not target_name and isinstance(step.get("target"), dict):
                    target_name = step["target"].get("name")
                mem_item = self.visual_memory.capture_and_remember(
                    app=app_name,
                    window_title=window_title,
                    action_kind=step.get("kind", "action"),
                    target_name=target_name,
                    session_id=self.name,
                    analyze_with_omniroute=True,
                )
                if mem_item.screenshot_path:
                    step["screenshot_path"] = mem_item.screenshot_path
                if mem_item.ocr_text:
                    step["ocr_snippet"] = mem_item.ocr_text[:120].strip()
                if mem_item.intent_summary:
                    step["intent"] = mem_item.intent_summary
            except Exception:
                pass
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
                title = ""
                try:
                    title = (window.window_text() or window.element_info.name or "").strip()
                except Exception:
                    pass
                step = {"kind": "open_app", "app": PureWindowsPath(path).name, "path": path}
                if title:
                    step["title"] = title
                if "\\windowsapps\\" in path.lower():
                    # Store apps cannot be started from their executable; replay launches the app identity.
                    step["app_id"] = self.native.app_id(pid)
                self.append(step)
                self.last_foreground = path
            self._wake(window)
            self._track_url(window, path)
            if self.capture and "executable" not in self.capture.flow and is_whatsapp(path):
                self.capture.flow.update(executable=path, app_id=self.native.app_id(pid))
        while True:
            try:
                event = self.events.queue.get_nowait()
            except queue.Empty:
                break
            if event["kind"] == "stop":
                return "stop"
            if not active or (not self.everything and event["pid"] not in active_pids):
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
                                    val = (field_text(focus, is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES) or "").strip()
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
                                val = (field_text(focus, is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES) or "").strip()
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
                    control = self._hit(window, event["point"])
                    if not self.everything and control.element_info.process_id not in active_pids:
                        continue
                    # Climb to the nearest named control, but never past a page or window root:
                    # "Click <whole page>" cannot be replayed.
                    leaf = control
                    for _ in range(6):
                        name, kind = control.element_info.name or "", control.element_info.control_type
                        if kind in ROOT_KINDS or (name and kind in CLICKABLE_KINDS):
                            break
                        parent = control.parent()
                        if parent is None:
                            break
                        control = parent
                    if kind in ROOT_KINDS:
                        control = leaf
                        name, kind = control.element_info.name or "", control.element_info.control_type
                    if kind in ROOT_KINDS:
                        continue
                    label = clean_control_name(control, name, kind)
                    spec = locator(control, editable=kind == "Edit")
                    if label and label not in ("Text field", kind) and not spec.get("name"):
                        spec["name"] = label
                    self.append({"kind": "click", "app": PureWindowsPath(path).name, "path": path,
                                 "label": label, "target": spec})
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
            value = field_text(focus, is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES)
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
                step = {"kind": "set_text", "app": PureWindowsPath(path).name, "path": path,
                        "label": "Text field", "target": spec, "value": value_clean}
                field = (focus.element_info.name or "").strip()
                if field and field != value_clean and len(field) <= 160:
                    step["field"] = field  # e.g. WhatsApp's "Type a message to <chat>" names the recipient
                self.append(step)
            if self.shopify_active and not is_whatsapp(path):
                win_text = ""
                try:
                    win_text = (window.window_text() or "").lower()
                except Exception:
                    pass
                name_low = self.name.lower()
                fname = (focus.element_info.name or "").lower()
                fid = (focus.element_info.automation_id or "").lower()
                if ("shopify" in win_text or "products" in win_text or "shopify" in name_low
                        or any("shopify" in str(s.get("value", "")).lower() or "shopify" in str(s.get("label", "")).lower() for s in self.steps)):
                    self.shopify_detected = True
                    if "price" in fname or "price" in fid or (value_clean.replace(".", "", 1).isdigit() and len(value_clean) <= 7):
                        self.shopify_data["price"] = value_clean
                    elif "sku" in fname or "sku" in fid:
                        self.shopify_data["sku"] = value_clean
                    elif "desc" in fname or "desc" in fid or len(value_clean) > 40:
                        self.shopify_data["description"] = value_clean
                    elif not self.shopify_data["title"] or "title" in fname or "title" in fid:
                        self.shopify_data["title"] = value_clean
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
        if self.capture and self.capture.composed and not self.capture.done:
            # Typed but never sent: the routine still means "send this", with Enter on replay.
            self.capture.enter()
            self.capture.flow["unsent"] = True
        # validate() is the integrity guard; unrelated evidence gaps elsewhere must not drop a complete send.
        if self.capture and self.capture.done:
            try:
                validate(self.capture.flow)
                flow = self.capture.flow
            except (ValueError, KeyError) as exc:
                self.replay_error = str(exc)
        if not flow:
            # Sturdier fallback: rebuild the send from WhatsApp's labelled message box and what was typed.
            from .whatsapp_send import derive
            try:
                flow = derive(self.steps)
            except ValueError:
                flow = None
            if flow:
                self.replay_error = None
        if self.shopify_active and (self.shopify_detected or "shopify" in self.name.lower()) and not flow:
            from ..browser.shopify import generate_product_fields
            flow = {
                "kind": "shopify",
                "product": generate_product_fields(self.shopify_data),
            }
            self.replay_error = None
        if self.capture and not flow and not self.replay_error:
            self.replay_error = "Search, contact click, exact message, verified header and Send must all be observed for replay"
        self._video_time()
        self.steps = compress_browsing(self.steps)
        return {"name": self.name, "apps": self.apps, "steps": self.steps, "flow": flow,
                "duration": round(time.monotonic() - self.started, 1), "replay_error": self.replay_error}
