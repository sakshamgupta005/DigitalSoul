import json
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from jsonschema import Draft202012Validator

from contracts.validate import FORMATS
from ..api.m2_app import strict_json
from ..execution.canonical import canonical
from ..execution.errors import GhostError

SCHEMA = Path(__file__).resolve().parents[3] / "contracts" / "command.schema.json"
COMMAND_DOMAIN = b"digitalghost.command.v1\n"
RESPONSE_DOMAIN = b"digitalghost.response.v1\n"


class CommandVerifier:
    def __init__(self, desktop_id, store):
        self.desktop_id, self.store = desktop_id, store
        self.validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FORMATS)

    def verify(self, raw, authorize):
        if len(raw) > 65536:
            raise GhostError("VALIDATION_ERROR", "Command exceeds 64 KiB", 422)
        try:
            envelope = strict_json(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise GhostError("VALIDATION_ERROR", "Invalid command JSON", 422) from exc
        if list(self.validator.iter_errors(envelope)):
            raise GhostError("VALIDATION_ERROR", "Command schema mismatch", 422)
        payload = envelope["payload"]
        if envelope["to_device"] != self.desktop_id or payload["device_id"] != envelope["from_device"]:
            raise GhostError("UNAUTHORIZED", "Command routing identity mismatch", 403)
        key = self.store.key(envelope["from_device"])
        signed = {k: v for k, v in envelope.items() if k != "sig"}
        try:
            key.verify(bytes.fromhex(envelope["sig"]), COMMAND_DOMAIN + canonical(signed))
        except (ValueError, InvalidSignature) as exc:
            raise GhostError("SIGNATURE_INVALID", "Command signature invalid", 403) from exc
        issued = datetime.fromisoformat(payload["issued_at"].replace("Z", "+00:00")).timestamp()
        if abs(self.store.clock() - issued) > 60:
            raise GhostError("COMMAND_EXPIRED", "Command timestamp is outside the freshness window")
        authorize(envelope)
        self.store.consume(envelope["from_device"], payload["nonce"], payload["command_id"])
        return envelope


def signed_response(private_key, desktop_id, request, payload):
    from uuid import uuid4
    from ..safety.approvals import now
    envelope = {"protocol_version": 1, "msg_id": uuid4().hex, "from_device": desktop_id,
                "to_device": request["from_device"], "kind": "response", "issued_at": now(),
                "command_id": request["payload"].get("command_id", request["msg_id"]), "payload": payload}
    envelope["sig"] = private_key.sign(RESPONSE_DOMAIN + canonical(envelope)).hex()
    return envelope
