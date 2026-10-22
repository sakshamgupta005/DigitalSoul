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


def derive(steps):
    """Rebuild 'send <message> to <person>' from a recording's WhatsApp text steps."""
    whatsapp = [step for step in steps if is_whatsapp(step.get("path", ""))]
    composed = [(i, step) for i, step in enumerate(whatsapp)
                if step["kind"] == "set_text" and recipient_of(step.get("field")) and step.get("value", "").strip()]
    if not composed:
        return None
    index, composer = composed[-1]
    message = " ".join(composer["value"].split())
    searches = [step["value"] for step in whatsapp[:index] if step["kind"] == "set_text" and not recipient_of(step.get("field"))]
    after = whatsapp[index + 1:]
    sent = any(step["kind"] == "press_enter" or (step["kind"] == "click" and "send" in (step.get("label") or "").lower()) for step in after)
    app = next((step for step in whatsapp if step["kind"] == "open_app"), {})
    recipe = {"format": FORMAT, "recipient": recipient_of(composer["field"]), "search": (searches[-1] if searches else ""),
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
            except (ValueError, OSError):
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
            except Exception:
                pass

    def _window(self, recipe):
        return self.native.active(recipe["executable"])[0]

    def _edits(self, recipe):
        from .steps import unique
        return [edit for edit in unique(self._window(recipe).descendants(control_type="Edit")) if edit.is_visible()]

    def _composer(self, recipe, recipient=None):
        for edit in self._edits(recipe):
            name = recipient_of(edit.element_info.name)
            if name and (recipient is None or name.lower() == recipient.lower()):
                return edit
        return None

    def _search_box(self, recipe):
        boxes = [edit for edit in self._edits(recipe) if not recipient_of(edit.element_info.name)]
        return boxes[0] if boxes else None

    def _result(self, recipe, wanted):
        from .flow import chat_name
        from .steps import unique
        window = self._window(recipe)
        rows = [row for row in unique([row for kind in ("DataItem", "ListItem") for row in window.descendants(control_type=kind)
                                       if chat_name(row.element_info.name).lower().startswith(wanted.lower())])
                if row.is_visible()]
        exact = [row for row in rows if chat_name(row.element_info.name).lower() == wanted.lower()]
        for group in (exact, rows):
            if len(group) == 1:
                return group[0]
        if len(rows) > 1:
            raise RuntimeError(f"More than one chat matches {wanted!r}; use their full saved name")
        return None

    def _type(self, control, text):
        self._visit(control)
        control.set_focus()
        try:
            control.set_edit_text(text)
        except Exception:
            pass
        if (control.get_value() or "").strip() != text.strip():
            from pywinauto.keyboard import send_keys
            from .steps import keys
            send_keys("^a{BACKSPACE}" + keys(text), with_spaces=True, pause=0.01)

    def prepare(self, recipe, message):
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
            (self.press or (lambda control: control.click_input()))(row)
            self.chat = chat_name(row.element_info.name)
        # Proof of identity: the composer must be addressed to this chat before anything is typed.
        composer = self._wait(lambda: self._composer(recipe, self.chat), f"the message box for {self.chat!r}")
        if (composer.get_value() or "").strip():
            raise ValueError("An unsent draft is already in that chat; it will not be overwritten")
        self._type(composer, message)
        self._wait(lambda: composer if (composer.get_value() or "").strip() == message.strip() else None, "the typed message")

    def send(self, recipe, message):
        composer = self._composer(recipe, self.chat)
        if composer is None or (composer.get_value() or "").strip() != message.strip():
            raise ValueError("Chat or message changed before sending")
        self._visit(composer)
        composer.set_focus()
        from pywinauto.keyboard import send_keys
        send_keys("{ENTER}")
