"""Discover the user's running Chromium browser session (Brave, Chrome or Edge) without launching or
copying profiles. The user's default browser is tried first; each browser writes DevToolsActivePort into
its own profile folder once 'Allow remote debugging for this browser instance' is switched on."""
import os
from pathlib import Path
from urllib.parse import urlsplit

# name: (profile folder under %LOCALAPPDATA%, where to switch remote debugging on, display name)
BROWSERS = {
    "brave": ("BraveSoftware/Brave-Browser/User Data", "brave://inspect/#remote-debugging", "Brave"),
    "chrome": ("Google/Chrome/User Data", "chrome://inspect/#remote-debugging", "Chrome"),
    "edge": ("Microsoft/Edge/User Data", "edge://inspect/#remote-debugging", "Edge"),
}
DEFAULT_BROWSER_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"


def validate_local_cdp(endpoint):
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"http", "ws"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("The browser debugging endpoint must be local")
    if parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError("The browser debugging endpoint needs a valid port")
    return endpoint


def default_browser():
    """'brave', 'chrome' or 'edge' when one of them opens https links, else None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DEFAULT_BROWSER_KEY) as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
    except (OSError, ImportError):
        return None
    for name, marker in (("brave", "brave"), ("chrome", "chrome"), ("edge", "msedge")):
        if prog_id.startswith(marker):
            return name
    return None


def browser_order():
    first = default_browser()
    return ([first] if first else []) + [name for name in BROWSERS if name != first]


def existing_browser_endpoint():
    """The debugging endpoint of whichever supported browser has remote debugging on, default browser first."""
    configured = os.environ.get("DIGITALSOUL_BROWSER_CDP_URL") or os.environ.get("DIGITALSOUL_EDGE_CDP_URL")
    if configured:
        return validate_local_cdp(configured)
    if os.environ.get("DIGITALSOUL_EDGE_USER_DATA_DIR"):
        return _endpoint_from(Path(os.environ["DIGITALSOUL_EDGE_USER_DATA_DIR"]), "Edge")
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    order = browser_order()
    for name in order:
        folder, _, label = BROWSERS[name]
        if (local / folder / "DevToolsActivePort").is_file():
            return _endpoint_from(local / folder, label)
    _, inspect, label = BROWSERS[order[0]]
    raise RuntimeError(f"In your regular {label}, open {inspect} and enable 'Allow remote debugging for this "
                       "browser instance', then retry. DigitalSoul will reuse your open tabs and login.")


def existing_edge_endpoint(user_data_dir=None):
    """Kept for existing callers: with no folder given, any supported browser works (default first)."""
    if user_data_dir is None:
        return existing_browser_endpoint()
    configured = os.environ.get("DIGITALSOUL_EDGE_CDP_URL")
    if configured:
        return validate_local_cdp(configured)
    return _endpoint_from(Path(user_data_dir), "Edge")


def _endpoint_from(root, label):
    """Use the permission-enabled WebSocket endpoint, not an unrelated fixed port."""
    try:
        lines = (root / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise RuntimeError(f"In your regular {label}, open its inspect page (e.g. edge://inspect/#remote-debugging) "
                           "and enable 'Allow remote debugging for this browser instance', then retry. "
                           "DigitalSoul will reuse your open tabs and login.") from None
    if len(lines) != 2 or not lines[0].isdigit() or not lines[1].startswith("/devtools/browser/"):
        raise RuntimeError(f"{label}'s debugging endpoint is invalid; toggle remote debugging off and on in {label}")
    path = lines[1]
    if any(c.isspace() for c in path) or "?" in path or "#" in path:
        raise RuntimeError(f"{label}'s debugging endpoint is invalid")
    return validate_local_cdp(f"ws://127.0.0.1:{lines[0]}{path}")
