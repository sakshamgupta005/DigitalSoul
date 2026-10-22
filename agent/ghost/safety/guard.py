from ..execution.errors import GhostError


def require_unchanged(before, after):
    if before != after:
        raise GhostError("STALE_APPROVAL", "Target or mutation context changed after approval")
