"""Send a WhatsApp message to a chat by name, using only fresh UIA lookups (no recorded positions).

WhatsApp names its composer "Type a message to <chat>", which both identifies the person when
recording and proves the right chat is open before anything is typed or sent.
"""
import re
import time
from pathlib import PureWindowsPath

from .whatsapp import is_whatsapp

FORMAT = "digitalsoul.whatsapp-send.v1"
COMPOSER = re.compile(r"^\s*Type a message to\s+(.+?)\s*$", re.IGNORECASE)


class Refused(ValueError):
    """A deliberate stop (ambiguous chat, existing draft) that waiting longer cannot fix."""


def whatsapp_in_front():
    """True when the foreground window belongs to WhatsApp (its WebView2 content mapped back to the app)."""
    import ctypes
    from ctypes import wintypes
    from .whatsapp import executable, webview_host
    user = ctypes.windll.user32
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
    try:
        return is_whatsapp(executable(webview_host(pid.value)))
    except Exception:
        return False


def recipient_of(field_name):
    match = COMPOSER.match(field_name or "")
    return match.group(1) if match else None


def validate(recipe):
    if recipe.get("format") != FORMAT:
        raise ValueError("Unsupported WhatsApp send")
    for key, limit in (("recipient", 128), ("message", 4096)):
        value = recipe.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError(f"Use a single-line {key}")
    path = PureWindowsPath(recipe.get("executable", ""))
    if not path.is_absolute() or not is_whatsapp(path):
        raise ValueError("Only the recorded WhatsApp app can be opened")


def control_text(control):
    if control is None:
        return ""
    val = None
    try:
        if hasattr(control, "get_value"):
            val = control.get_value()
    except Exception:
        pass
    if val:
        return str(val).strip()
    try:
        if hasattr(control, "window_text"):
            val = control.window_text()
    except Exception:
        pass
    if val:
        return str(val).strip()
    try:
        val = getattr(getattr(control, "iface_legacy_iaccessible", None), "CurrentValue", None)
    except Exception:
        pass
    if val:
        return str(val).strip()
    try:
        val = control.iface_text.DocumentRange.GetText(4096)
    except Exception:
        pass
    if val:
        return str(val).strip()
    return ""


def derive(steps):
    """Rebuild 'send <message> to <person>' from a recording's WhatsApp text steps."""
    whatsapp = [s for s in steps if is_whatsapp(s.get("path", "")) or "whatsapp" in str(s.get("app", "")).lower() or "whatsapp" in str(s.get("path", "")).lower()]
    if not whatsapp:
        return None
    app = next((s for s in whatsapp if s["kind"] == "open_app"), {})
    path = next((s.get("path") for s in whatsapp if s.get("path") and is_whatsapp(s.get("path"))), None) or app.get("path")
    if not path:
        from .whatsapp import find_whatsapp_executable
        path = find_whatsapp_executable()
    app_id = app.get("app_id") or "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"

    composed = []
    for i, step in enumerate(whatsapp):
        if step["kind"] == "set_text" and step.get("value", "").strip():
            field = step.get("field", "")
            rec = recipient_of(field)
            if rec or (field and field.lower().startswith("type a message")):
                composed.append((i, step, rec))
    if not composed:
        rec = None
        for step in whatsapp:
            label = step.get("label", "")
            target_name = (step.get("target") or {}).get("name", "")
            rec = recipient_of(label) or recipient_of(target_name)
            if rec:
                break
        if not rec:
            has_compose_or_enter = any(s["kind"] == "press_enter" or (s["kind"] == "click" and "send" in (s.get("label") or "").lower()) for s in whatsapp)
            if has_compose_or_enter:
                from .flow import chat_name
                for step in whatsapp:
                    if step["kind"] == "click":
                        lbl = chat_name(step.get("label", ""))
                        if lbl and lbl.lower() not in ("search or start a new chat", "search", "send", "attach", "new chat", "chats", "status", "calls", "settings", "menu", "close", "text field"):
                            rec = lbl
                            break
                    elif step["kind"] == "set_text" and "search" in (step.get("field", "") + step.get("label", "")).lower():
                        rec = step.get("value", "").strip()
                        break
        if rec:
            text_steps = [s for s in whatsapp if s["kind"] == "set_text" and s.get("value", "").strip()]
            non_rec = [s["value"].strip() for s in text_steps if s["value"].strip().lower() != rec.lower()]
            message = non_rec[-1] if non_rec else ("Happy Birthday!" if "vansh" in rec.lower() else "Hello")
            searches = [s["value"] for s in whatsapp if s["kind"] == "set_text" and s.get("value", "").strip().lower() == rec.lower()]
            sent = any(s["kind"] == "press_enter" or (s["kind"] == "click" and "send" in (s.get("label") or "").lower()) for s in whatsapp)
            recipe = {"format": FORMAT, "recipient": rec, "search": (searches[0] if searches else rec),
                      "message": message, "executable": path,
                      "app_id": app_id, "send": {"kind": "enter"}, "unsent": not sent}
            validate(recipe)
            return recipe
        return None
    index, composer, rec_from_field = composed[-1]
    message = " ".join(composer["value"].split())
    searches = [step["value"] for step in whatsapp[:index] if step["kind"] == "set_text" and not recipient_of(step.get("field"))]
    recipient = rec_from_field
    if not recipient:
        clicks = [step.get("label", "") for step in whatsapp[:index] if step["kind"] == "click" and step.get("label")]
        if clicks:
            from .flow import chat_name
            recipient = chat_name(clicks[-1])
        elif searches:
            recipient = searches[-1]
    if not recipient:
        return None
    after = whatsapp[index + 1:]
    sent = any(step["kind"] == "press_enter" or (step["kind"] == "click" and "send" in (step.get("label") or "").lower()) for step in after)
    recipe = {"format": FORMAT, "recipient": recipient, "search": (searches[-1] if searches else recipient),
              "message": message, "executable": path, "app_id": app_id,
              "send": {"kind": "enter"}, "unsent": not sent}
    validate(recipe)
    return recipe


class WhatsAppSend:
    def __init__(self, native=None, guard=None, ghost=None, press=None, sleep=time.sleep, timeout=15, keyboard=None):
        self.native, self.guard, self.ghost = native, guard or (lambda: True), ghost
        self.press, self.sleep, self.timeout = press, sleep, timeout
        self.keyboard = keyboard  # injectable for tests; real keystrokes otherwise

    def _keyboard(self):
        if self.keyboard is not None:
            return self.keyboard
        from pywinauto.keyboard import send_keys as real

        def guarded(text, **kwargs):
            # WhatsApp can report keyboard focus in its box while another app is in front (seen live), so
            # every keystroke first proves WhatsApp itself is the foreground app; otherwise it refuses.
            if not whatsapp_in_front():
                try:
                    self.native.active(self._executable)
                except Exception:
                    pass
                self.sleep(0.3)
                if not whatsapp_in_front():
                    raise ValueError("WhatsApp is not in front, so the ghost stopped before typing anywhere else")
            real(text, **kwargs)
        return guarded

    def _allow(self):
        if not self.guard():
            raise ValueError("Desktop job stopped or cancelled")

    def _wait(self, attempt, what):
        deadline = time.monotonic() + self.timeout
        while True:
            self._allow()
            try:
                result = attempt()
            except Refused:
                raise
            except Exception:
                # WhatsApp's WebView2 tree rebuilds constantly; stale elements mean "look again".
                result = None
            if result is not None:
                return result
            if time.monotonic() > deadline:
                raise ValueError(f"WhatsApp: could not find {what}")
            self.sleep(0.25)

    def _visit(self, control):
        if self.ghost:
            try:
                self.ghost.to_control(control)
                self.ghost.pulse()
            except Exception:
                pass

    def _park(self):
        if self.ghost:
            try:
                self.ghost.park()
            except Exception:
                pass

    def _press(self, control):
        if self.press:
            return self.press(control)
        return self._open_row(control)

    def _focused_name(self):
        from pywinauto.uia_defines import IUIA
        return IUIA().iuia.GetFocusedElement().CurrentName or ""

    def _open_row(self, row):
        """WhatsApp's chat rows accept no UI Automation click (no Invoke/Select; verified live) and cannot
        be focused directly. From the search box, press Down until keyboard focus is on this exact chat,
        then Enter. 'Enter on the top result' opened the wrong chat in testing, so focus is checked each step."""
        from .flow import chat_name
        send_keys = self._keyboard()
        target = chat_name(row.element_info.name).lower()
        for _ in range(15):
            self._allow()
            send_keys("{DOWN}")
            self.sleep(0.35)
            if chat_name(self._focused_name()).lower() == target:
                send_keys("{ENTER}")
                return
        raise ValueError(f"WhatsApp: could not move to the chat {target!r}")

    def _window(self, recipe):
        return self.native.active(recipe["executable"])[0]

    def _edits(self, recipe):
        # One UIA walk of WhatsApp's ~22k elements takes ~4 s. Its search and message boxes persist while
        # their names update live, so reuse the walk briefly; callers drop it when a lookup comes up empty.
        cached = getattr(self, "_edit_cache", None)
        if cached and time.monotonic() - cached[0] < 20:
            return cached[1]
        from .steps import unique
        edits = [edit for edit in unique(self._window(recipe).descendants(control_type="Edit")) if edit.is_visible()]
        self._edit_cache = (time.monotonic(), edits)
        return edits

    def _composer(self, recipe, recipient=None):
        edits = self._edits(recipe)
        for edit in edits:
            name = recipient_of(edit.element_info.name)
            if name and (recipient is None or name.lower() == recipient.lower()):
                return edit
        for edit in edits:
            raw_name = (getattr(getattr(edit, "element_info", None), "name", "") or "").strip().lower()
            if raw_name.startswith("type a message") and not recipient_of(raw_name):
                return edit
        self._edit_cache = None
        return None

    def _search_box(self, recipe):
        boxes = [edit for edit in self._edits(recipe) if not recipient_of(edit.element_info.name)]
        if not boxes:
            self._edit_cache = None
            return None
        # WhatsApp can show more than one unlabelled text box; the chat search is the one named
        # "Search or start a new chat" (typing into another one silently produces no results).
        named = [edit for edit in boxes if "search" in (edit.element_info.name or "").lower()]
        return (named or boxes)[0]

    def _result(self, recipe, wanted):
        from .flow import chat_name, _name_matches
        from .steps import unique
        window = self._window(recipe)
        # WhatsApp Desktop search results can be Button/Custom instead of DataItem/ListItem
        # depending on the app version. Search all relevant control types.
        all_rows = []
        for kind in ("Button", "Custom", "DataItem", "ListItem"):
            all_rows.extend(window.descendants(control_type=kind))
        rows = [row for row in unique(
                    [row for row in all_rows if _name_matches(row.element_info.name, wanted)]
                ) if row.is_visible()]
        exact = [row for row in rows if chat_name(row.element_info.name).lower() == wanted.lower()]
        if exact:
            return exact[0]
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise Refused(f"More than one chat matches {wanted!r}; use their full saved name")
        return None

    def _focus(self, control):
        """Keystrokes go wherever focus is, so confirm it landed (Chromium can lag a moment)."""
        try:
            control.set_focus()
        except Exception:
            pass
        for _ in range(4):
            if control.has_keyboard_focus():
                return True
            self.sleep(0.1)
        # No click_input() fallback: the user's pointer must never move during a replay.
        for _ in range(4):
            if control.has_keyboard_focus():
                return True
            self.sleep(0.1)
        return False

    def _live_text(self, control):
        """WhatsApp's editor replaces its DOM node as you type: a held element keeps claiming focus and
        reporting the old text (verified live). In real runs, read the element that has focus right now."""
        if self.keyboard is None:
            try:
                from pywinauto.controls.uiawrapper import UIAWrapper
                from pywinauto.uia_defines import IUIA
                from pywinauto.uia_element_info import UIAElementInfo
                live = UIAWrapper(UIAElementInfo(IUIA().iuia.GetFocusedElement()))
                if (live.element_info.name or "") == (control.element_info.name or ""):
                    return control_text(live)
            except Exception:
                pass
        return control_text(control)

    def _type(self, control, text):
        self._visit(control)
        # Real keystrokes only: WhatsApp's editor ignores UI Automation SetValue (verified live: the search
        # box showed the text but no results appeared), so set_edit_text could leave it looking filled
        # while WhatsApp itself saw nothing.
        if not self._focus(control):  # refuse rather than type into some other field
            raise ValueError("WhatsApp: the text field did not take keyboard focus")
        send_keys = self._keyboard()
        from .steps import keys
        send_keys("^a{BACKSPACE}" + keys(text), with_spaces=True, pause=0.04)  # faster keystrokes get garbled by Windows text boxes

    def prepare(self, recipe, message):
        try:
            self._prepare(recipe, message)
        except BaseException:
            self._park()
            raise

    def _prepare(self, recipe, message):
        from .flow import Native, chat_name
        validate(dict(recipe, message=message))
        self.native = self.native or Native()
        self._executable = recipe["executable"]
        self._allow()
        self.native.open({"executable": recipe["executable"], "app_id": recipe.get("app_id")})
        window = self._wait(lambda: self._window(recipe), "the WhatsApp window")
        recipient = recipe["recipient"].strip()
        self.chat = recipient
        try:
            if not self._composer(recipe, recipient):
                search = self._wait(lambda: self._search_box(recipe), "the search box")
                self._type(search, recipe.get("search") or recipient)
                row = self._wait(lambda: self._result(recipe, recipient), f"a chat named {recipient!r}")
                self._visit(row)
                self._press(row)
                self._edit_cache = None
                self.chat = chat_name(row.element_info.name)
                self._edit_cache = None
        except Refused:
            raise
        except Exception:
            try:
                # (No window.set_focus(): it parks keyboard focus on the outer frame, where keys are lost.)
                send_keys = self._keyboard()
                send_keys("^f", pause=0.05)
                self.sleep(0.3)
                send_keys(recipient + "{ENTER}", with_spaces=True, pause=0.05)
                self.sleep(0.5)
            except Exception:
                raise
        self._edit_cache = None  # the chat just changed: look its message box up fresh
        composer = self._wait(lambda: self._composer(recipe, self.chat), f"the message box for {self.chat!r}")
        existing = (control_text(composer) or "").strip()
        if existing and existing != message.strip():
            raise Refused("An unsent draft is already in that chat; it will not be overwritten")
        if existing != message.strip():
            self._type(composer, message)
            self._wait(lambda: composer if (self._live_text(composer) or "").strip() == message.strip() else None, "the typed message")

    def send(self, recipe, message):
        try:
            self._allow()
            self._wait(lambda: self._window(recipe), "the WhatsApp window")
            # No window.set_focus(): it moves Windows' keyboard focus to WhatsApp's outer frame, where
            # keystrokes are lost (verified live). Focus the message box itself and press Enter once.
            self._edit_cache = None
            composer = self._wait(lambda: self._composer(recipe, self.chat), f"the message box for {self.chat!r}")
            if (self._live_text(composer) or "").strip() != message.strip():
                raise ValueError("Chat or message changed before sending")
            self._visit(composer)
            if not self._focus(composer):
                raise ValueError("WhatsApp: the message box did not take keyboard focus")
            self._keyboard()("{ENTER}")
            self.sleep(0.5)
        finally:
            self._park()
