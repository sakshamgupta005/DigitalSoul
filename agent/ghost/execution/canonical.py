"""Use the shared RFC 8785 implementation; never substitute json.dumps."""

from .errors import GhostError


def canonical(value):
    try:
        import rfc8785
    except ImportError as exc:
        raise GhostError("INTERNAL", "Install the required rfc8785 dependency", 503) from exc
    try:
        return rfc8785.dumps(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise GhostError("VALIDATION_ERROR", "Value is outside the canonical JSON domain", 422) from exc
