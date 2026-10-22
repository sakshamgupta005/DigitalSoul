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
    whatsapp = [step for step in steps if is_whatsapp(step.get("path", ""))]
    composed = []
    for i, step in enumerate(whatsapp):
        if step["kind"] == "set_text" and step.get("value", "").strip():
            field = step.get("field", "")
            rec = recipient_of(field)
            if rec or (field and field.lower().startswith("type a message")):
                composed.append((i, step, rec))
    if not composed:
        return None
    index, composer, rec_from_field = composed[-1]
    message = " ".join(composer["value"].split())
    searches = [step["value"] for step in whatsapp[:index] if step["kind"] == "set_text" and not recipient_of(step.get("field"))]
    recipient = rec_from_field
    if not recipient:
        clicks = [step.get("label", "") for step in whatsapp[:index] if step["kind"] == "click" and step.get("label")]
        if clicks:
            recipient = clicks[-1]
        elif searches:
            recipient = searches[-1]
    if not recipient:
        return None
    after = whatsapp[index + 1:]
    sent = any(step["kind"] == "press_enter" or (step["kind"] == "click" and "send" in (step.get("label") or "").lower()) for step in after)
    app = next((step for step in whatsapp if step["kind"] == "open_app"), {})
    recipe = {"format": FORMAT, "recipient": recipient, "search": (searches[-1] if searches else recipient),
              "message": message, "executable": composer["path"], "app_id": app.get("app_id"),
              "send": {"kind": "enter"}, "unsent": not sent}
    validate(recipe)
    return recipe


class WhatsAppSend:
    def __init__(self, native=None, guard=None, ghost=None, press=None, sleep=time.sleep, timeout=15):
        self.native, self.guard, self.ghost = native, guard or (lambda: True), ghost
        self.press, self.sleep, self.timeout = press, sleep, timeout

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
        from .ghost import act
        act(control)  # UI Automation only; never the user's pointer

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
        return boxes[0] if boxes else None

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
        for group in (exact, rows):
            if len(group) == 1:
                return group[0]
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

    def _type(self, control, text):
        self._visit(control)
        focused = self._focus(control)
        try:
            control.set_edit_text(text)
        except Exception:
            pass
        if (control_text(control) or "").strip() != text.strip():
            if not focused:  # refuse rather than clear some other field
                raise ValueError("WhatsApp: the text field did not take keyboard focus")
            from pywinauto.keyboard import send_keys
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
        self._allow()
        self.native.open({"executable": recipe["executable"], "app_id": recipe.get("app_id")})
        self._wait(lambda: self._window(recipe), "the WhatsApp window")
        recipient = recipe["recipient"].strip()
        self.chat = recipient
        if not self._composer(recipe, recipient):
            search = self._wait(lambda: self._search_box(recipe), "the search box")
            self._type(search, recipe.get("search") or recipient)
            row = self._wait(lambda: self._result(recipe, recipient), f"a chat named {recipient!r}")
            self._visit(row)
            self._press(row)
            self.chat = chat_name(row.element_info.name)
            self._edit_cache = None  # WhatsApp redraws its DOM after opening a chat; force a fresh walk
        # Proof of identity: the composer must be addressed to this chat before anything is typed.
        composer = self._wait(lambda: self._composer(recipe, self.chat), f"the message box for {self.chat!r}")
        if (control_text(composer) or "").strip():
            raise Refused("An unsent draft is already in that chat; it will not be overwritten")
        self._type(composer, message)
        self._wait(lambda: composer if (control_text(composer) or "").strip() == message.strip() else None, "the typed message")

    def send(self, recipe, message):
        try:
            composer = self._composer(recipe, self.chat)
            if composer is None or (control_text(composer) or "").strip() != message.strip():
                raise ValueError("Chat or message changed before sending")
            self._visit(composer)
            if not self._focus(composer):
                raise ValueError("WhatsApp: the message box did not take keyboard focus")
            from pywinauto.keyboard import send_keys
            send_keys("{ENTER}")
        finally:
            self._park()
