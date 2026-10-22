import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from ..execution.canonical import canonical
from ..execution.errors import GhostError


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_approval(run, step, target, risk, origin, policy_generation, clock):
    aid = str(uuid4())
    preview = {
        "preview_version": 1, "approval_id": aid, "run_id": run["run_id"],
        "workflow_id": run["workflow_id"], "workflow_version": run["workflow_version"],
        "workflow_hash": run["workflow_hash"], "step_id": step["step_id"],
        "loop_path": run["checkpoint"]["loop_path"], "origin": origin,
        "target": target, "arguments": {"verb": step.get("verb"), "input": step.get("input")},
        "risk": risk, "policy_generation": policy_generation,
    }
    return {"approval_id": aid, "step_id": step["step_id"], "workflow_version": run["workflow_version"],
            "effect_hash": digest(preview), "preview_data": preview, "risk": risk,
            "expires_at": datetime.fromtimestamp(clock() + 60, timezone.utc).isoformat().replace("+00:00", "Z")}


def matches(pending, binding, clock):
    if not pending or datetime.fromisoformat(pending["expires_at"].replace("Z", "+00:00")).timestamp() <= clock():
        raise GhostError("STALE_APPROVAL", "Approval is absent or expired")
    for field in ("approval_id", "step_id", "workflow_version", "effect_hash"):
        if binding.get(field) != pending[field]:
            raise GhostError("STALE_APPROVAL", "Approval does not match the pending effect")
