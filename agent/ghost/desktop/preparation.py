"""Allowlisted preparation diagnostics; never persist provider exception text."""
from functools import wraps

REASONS = {
    "payload": "Could not read the saved job. Create a new job from the reviewed routine.",
    "validate": "The saved WhatsApp routine is invalid. Review or record it again.",
    "connect": "Could not initialize Windows automation. Restart DigitalSoul and check its Python environment.",
    "open": "Could not open WhatsApp. Open it manually and check that you are signed in.",
    "window": "Could not access the WhatsApp window. Unlock Windows and bring WhatsApp to the front.",
    "search": "Could not use WhatsApp search. Check that its search box is available.",
    "recipient": "Could not select the intended chat. Check the saved contact name.",
    "composer": "Could not verify the intended chat and message box. Open the chat and review the recording.",
    "draft": "An existing draft prevented preparation. Review or clear it in WhatsApp.",
    "type": "Could not enter the approved message. Check WhatsApp keyboard focus.",
    "verify": "Could not verify the approved message. Inspect the draft before trying again.",
    "focus": "WhatsApp could not take keyboard focus. Bring it to the front on an unlocked desktop.",
    "ambiguous": "More than one chat matches. Use a unique, full saved contact name.",
    "cancelled": "Desktop job was stopped or cancelled.",
    "unknown": "Unexpected preparation failure. Restart DigitalSoul and inspect WhatsApp locally.",
}

class PreparationError(ValueError):
    def __init__(self, code):
        self.code = code if code in REASONS else "unknown"
        super().__init__(REASONS[self.code])


def diagnosed_prepare(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        self.preparation_stage = "validate"
        try:
            return method(self, *args, **kwargs)
        except PreparationError:
            raise
        except Exception as exc:
            raise PreparationError(self.preparation_stage) from exc
    return wrapped


def failure_detail(exc, stage="unknown"):
    code = exc.code if isinstance(exc, PreparationError) else stage
    code = code if code in REASONS else "unknown"
    return f"WhatsApp preparation [{code}]: {REASONS[code]} Send was not attempted; a draft may remain."
