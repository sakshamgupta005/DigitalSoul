from ..execution.errors import GhostError

LEVELS = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


def effective_risk(step, registered_risk):
    level = max(LEVELS[step["risk"]], LEVELS[registered_risk])
    if level == 3:
        raise GhostError("POLICY_BLOCKED", "R3 actions are disabled", 403)
    return f"R{level}"


def needs_approval(risk, mode, trusted):
    return risk == "R2" or (risk == "R1" and (mode == "supervised" or not trusted))
