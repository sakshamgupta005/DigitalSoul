from ..execution.errors import GhostError


def coordinate_input(*args, **kwargs):
    raise GhostError("POLICY_BLOCKED", "Coordinate fallback is disabled until grounding adapters are reviewed", 403)
