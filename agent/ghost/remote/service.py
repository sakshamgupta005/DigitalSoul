from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ..execution.errors import GhostError
from .signing import CommandVerifier, signed_response


class RemoteService:
    def __init__(self, desktop_id, private_key, store, runtime, allow_approval=None):
        self.desktop_id, self.private_key, self.store, self.runtime = desktop_id, private_key, store, runtime
        self.public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.verifier = CommandVerifier(desktop_id, store)
        # Explicit integration registration is required for non-sensitive previews.
        self.allow_approval = allow_approval or (lambda run: False)
        runtime.device_check = store.key

    def authorize(self, envelope):
        body = envelope["payload"]
        kind = body["type"]
        if kind == "run_workflow":
            registration = self.runtime.workflows.get((body["workflow_id"], body["workflow_version"]))
            if not registration or not registration[0]["remote_allowed"]:
                raise GhostError("POLICY_BLOCKED", "Workflow is not enabled for remote execution", 403)
            if any(p["source"] == "user" for p in registration[0]["parameters"]):
                raise GhostError("POLICY_BLOCKED", "Remote workflow must derive its inputs locally", 403)
        elif "run_id" in body:
            run = self.runtime.store.get(body["run_id"])
            if not run["_plan"]["remote_allowed"]:
                raise GhostError("POLICY_BLOCKED", "Run is not enabled for remote control", 403)
            if kind in ("approve_step", "reject_step"):
                if run["workflow_id"] != body["workflow_id"] or run["workflow_version"] != body["workflow_version"]:
                    raise GhostError("STALE_APPROVAL", "Approval workflow binding differs")
                if not self.allow_approval(run):
                    raise GhostError("POLICY_BLOCKED", "This effect requires local preview and approval", 403)

    async def handle(self, raw):
        if len(raw) > 65536:
            raise GhostError("VALIDATION_ERROR", "Remote message exceeds 64 KiB", 422)
        from ..api.m2_app import strict_json
        try:
            parsed = strict_json(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise GhostError("VALIDATION_ERROR", "Invalid remote JSON", 422) from exc
        if isinstance(parsed, dict) and parsed.get("kind") == "pair_claim":
            if (set(parsed) != {"protocol_version", "msg_id", "from_device", "to_device", "kind", "payload"}
                    or parsed["protocol_version"] != 1 or parsed["to_device"] != self.desktop_id
                    or not isinstance(parsed["msg_id"], str) or not isinstance(parsed["from_device"], str)):
                raise GhostError("VALIDATION_ERROR", "Pairing envelope mismatch", 422)
            body = parsed["payload"]
            fields = {"pairing_id", "capability", "device_id", "public_key", "signature"}
            if (not isinstance(body, dict) or set(body) != fields or not all(isinstance(v, str) for v in body.values())
                    or body["device_id"] != parsed["from_device"]):
                raise GhostError("VALIDATION_ERROR", "Pairing payload mismatch", 422)
            claim = self.store.claim(**body)
            return signed_response(self.private_key, self.desktop_id, parsed, {"ok": True, "pairing_claim": claim})
        request = self.verifier.verify(raw, self.authorize)
        body, kind = request["payload"], request["payload"]["type"]
        try:
            # Key can be revoked between verification and dispatch.
            self.store.key(request["from_device"])
            if kind == "run_workflow":
                result = await self.runtime.start(body["workflow_id"], body["workflow_version"], {}, "supervised",
                                                  "remote:" + request["from_device"] + ":" + body["command_id"],
                                                  remote_device=request["from_device"])
            elif kind in ("approve_step", "reject_step", "pause_run", "resume_run", "stop_run"):
                operation = {"approve_step": "approve", "reject_step": "reject", "pause_run": "pause",
                             "resume_run": "resume", "stop_run": "cancel"}[kind]
                result = await self.runtime.control(body["run_id"], operation, body, approval_device=request["from_device"])
            elif kind == "list_workflows":
                result = {"workflows": [{"workflow_id": key[0], "version": key[1]}
                                          for key, (plan, _) in self.runtime.workflows.items() if plan["remote_allowed"]]}
            else:
                result = {"runs": [{"run_id": r["run_id"], "state": r["state"]} for r in self.runtime.store.list()]}
            if "run_id" in result:
                result = {"run_id": result["run_id"], "state": result["state"]}
            response = {"ok": True, "data": result}
        except GhostError as exc:
            response = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        return signed_response(self.private_key, self.desktop_id, request, response)

    async def revoke(self, device_id):
        self.store.revoke(device_id)
        for run in self.runtime.store.all():
            if device_id in (run.get("_remote_device"), run.get("_approval_device")) and run["state"] not in ("succeeded", "completed_unverified", "failed", "cancelled"):
                await self.runtime.control(run["run_id"], "cancel")
