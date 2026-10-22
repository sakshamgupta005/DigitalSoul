"""Discover the user's running Edge session without launching or copying profiles."""
import os
from pathlib import Path
from urllib.parse import urlsplit


def validate_local_cdp(endpoint):
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"http", "ws"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("The Edge debugging endpoint must be local")
    if parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError("The Edge debugging endpoint needs a valid port")
    return endpoint


def existing_edge_endpoint(user_data_dir=None):
    """Use the permission-enabled WebSocket endpoint, not an unrelated fixed port."""
    configured = os.environ.get("DIGITALSOUL_EDGE_CDP_URL")
    if configured:
        return validate_local_cdp(configured)
    root = Path(user_data_dir or os.environ.get("DIGITALSOUL_EDGE_USER_DATA_DIR") or
                Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")))
    if user_data_dir is None and not os.environ.get("DIGITALSOUL_EDGE_USER_DATA_DIR"):
        root = root / "Microsoft/Edge/User Data"
    try:
        lines = (root / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise RuntimeError("In your regular Edge, open edge://inspect/#remote-debugging and enable "
                           "'Allow remote debugging for this browser instance', then retry. "
                           "DigitalSoul will reuse your open tabs and login.") from None
    if len(lines) != 2 or not lines[0].isdigit() or not lines[1].startswith("/devtools/browser/"):
        raise RuntimeError("Edge's debugging endpoint is invalid; toggle remote debugging off and on in Edge")
    path = lines[1]
    if any(c.isspace() for c in path) or "?" in path or "#" in path:
        raise RuntimeError("Edge's debugging endpoint is invalid")
    return validate_local_cdp(f"ws://127.0.0.1:{lines[0]}{path}")
