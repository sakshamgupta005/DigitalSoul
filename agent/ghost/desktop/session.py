"""Explicit, app-scoped recorder driven by the notch's Start/Stop controls."""
import contextlib
import ctypes
import functools
import queue
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import PureWindowsPath

from .events import WinEvents
from .flow import Capture, Native, ancestry, header, locator, validate
from .whatsapp import executable, hosted_pid, WHATSAPP_EXES, is_whatsapp

# Chromium browsers spread one window across child processes.
BROWSER_EXES = {"chrome.exe", "brave.exe", "msedge.exe"}
CLICKABLE_KINDS = ("Button", "ListItem", "Edit", "Hyperlink", "MenuItem", "TabItem", "CheckBox", "RadioButton",
                   "DataItem", "TreeItem", "Text", "Image", "ComboBox", "SplitButton")
# Clicking a page or window root says nothing about what was clicked (typing is captured separately).
ROOT_KINDS = ("Window", "Pane", "Document")
# "Everything" mode still never watches DigitalSoul itself, sign-in prompts or password managers.
NEVER_RECORD = {"digitalsoul-notch.exe", "consent.exe", "lockapp.exe", "credentialuibroker.exe", "logonui.exe",
                "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe", "lastpass.exe",
                "enpass.exe", "nordpass.exe", "roboform.exe", "keeperpasswordmanager.exe"}
# DigitalSoul's own windows (notch, ghost overlay), whatever executable a dev build runs as.
OWN_TITLES = {"DigitalSoul", "DigitalSoul ghost"}
# Fields whose text is never recorded even when the app does not flag them as passwords.
SECRET_FIELD = re.compile(r"pass(word|code|phrase)|\bpin\b|\bcvv\b|\bcvc\b|security code|one[- ]time|\botp\b", re.I)
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
# Browser steps closer than this to the page they led to are folded into it (see compress_browsing).
FOLD_SECONDS = 60


def clean_control_name(control, name, kind):
    """Return a clean, human-readable name for an element without noisy timestamps, internal IDs or container prefixes."""
    if not name:
        try:
            name = control.window_text() or ""
        except Exception:
            name = ""
    name = (name or "").strip()
    if name.startswith("System.") or name in ("ItemNameDisplay", "Name"):
        try:
            for child in control.children():
                c_name = (child.element_info.name or child.window_text() or "").strip()
                if c_name and not c_name.startswith("System."):
                    name = c_name
                    break
        except Exception:
            pass
    if not name:
        return "Text field" if kind == "Edit" else (kind or "control")
    first = re.sub(r"^\(\d+\)\s*", "", name).strip()
    first = re.split(r"[,\n\r]", first, maxsplit=1)[0].strip()
    return first or name


def web_address(value):
    """An address-bar value as a full http(s) URL, or None (search text, internal pages)."""
    value = (value or "").strip()
    if not value or " " in value or len(value) > 2000:
        return None
    if not re.match(r"https?://", value):
        # Chromium hides the scheme; local dev servers are plain http.
        if re.match(r"localhost(:\d+)?(/|$|\?)", value):
            return "http://" + value
        if not re.match(r"[\w-]+(\.[\w-]+)+(:\d+)?(/|$|\?)", value):
            return None
        value = "https://" + value
    return value


def with_time(url, seconds):
    """A YouTube watch URL that resumes at `seconds`."""
    url = re.sub(r"([?&])t=\d+s?&?", r"\1", url).rstrip("?&")
    return f"{url}{'&' if '?' in url else '?'}t={int(seconds)}s"


# A results page (Google, Bing, Brave Search, DuckDuckGo, YouTube search): a way to a page, not a destination.
SEARCH_PAGE = re.compile(r"https?://[^/?#]+/(search|results)?\?([^#]*&)?(q|query|search_query)=", re.I)


def is_search(url):
    return bool(SEARCH_PAGE.match(url or ""))


def site(url):
    return re.sub(r"^(www|m)\.", "", re.sub(r"^https?://", "", url or "").split("/")[0].split("?")[0].lower())


def page_title(window_title):
    """A browser window's title without the browser name: 'Alan Turing - Wikipedia - Brave' ->
    'Alan Turing - Wikipedia'. Empty for a window that shows no page title (a new tab)."""
    title = re.sub(r"^\(\d+\)\s*", "", (window_title or "").strip())  # unread counters such as YouTube's "(3) "
    title = re.sub(r"(\s+and \d+ more pages?)?(\s+-\s+[^-]+)?\s+-\s+Microsoft​?\s*Edge$", "", title)
    title = re.sub(r"\s+-\s+(Brave|Google Chrome|Chromium)$", "", title).strip()
    return "" if title.lower() in ("", "brave", "google chrome", "new tab", "chromium") else title


def is_video(url):
    return bool(re.match(r"https?://(www\.|m\.)?(youtube\.com/(watch|shorts/)|youtu\.be/)", url or ""))


# A page left within this many seconds was only passed through (e.g. YouTube home on the way to a
# video); pages you stayed on, like a video you watched before the next one, keep their own step.
PASS_THROUGH_SECONDS = 8


DISMISS_WORDS = {
    "close", "close tab", "close window", "cancel", "dismiss", "exit", "back",
    "not now", "later", "no thanks", "don't save", "dont save", "discard", "ignore", "skip", "x"
}

BLANK_URLS = {
    "about:blank", "chrome://newtab", "chrome://newtab/", "brave://newtab", "brave://newtab/",
    "edge://newtab", "edge://newtab/", "about:newtab"
}


def is_dismiss_or_close(step):
    """Detects clicks that close, cancel, dismiss or exit without producing an outcome."""
    if step.get("kind") != "click":
        return False
    label = (step.get("label") or "").strip().lower()
    target = step.get("target") or {}
    auto_id = str(target.get("automation_id") or "").lower()
    name = str(target.get("name") or "").lower()
    if label in DISMISS_WORDS or label.startswith("close tab") or label.startswith("close window"):
        return True
    if auto_id in ("closebutton", "closetabbutton", "close", "btnclose", "cancelbutton"):
        return True
    if name in DISMISS_WORDS or name.startswith("close tab") or name.startswith("close window"):
        return True
    return False


def is_blank_url(url):
    return (url or "").strip().rstrip("/").lower() in BLANK_URLS


def is_substantive_step(step):
    """Determine whether a step achieved a concrete action or state change."""
    kind = step.get("kind")
    if kind == "set_text":
        return bool((step.get("value") or "").strip())
    if kind == "press_enter":
        return True
    if kind == "open_url":
        return not is_blank_url(step.get("url"))
    if kind == "click":
        if is_dismiss_or_close(step):
            return False
        label = (step.get("label") or "").strip().lower()
        if label in ("send", "submit", "save", "ok", "confirm", "next", "apply", "create",
                     "delete", "post", "run", "play", "add", "import", "export", "download",
                     "upload", "continue", "finish", "done", "open"):
            return True
        target = step.get("target") or {}
        c_type = target.get("control_type")
        path = step.get("path") or step.get("app") or ""
        # Chat items require typing/sending to produce substance
        if is_whatsapp(path):
            return False
        # Pressing a button does something (Calculator's 7, + and = are the whole routine).
        if c_type in ("ListItem", "DataItem", "TreeItem", "Button", "SplitButton", "MenuItem", "CheckBox", "RadioButton"):
            return True
        if label and not c_type:
            return True
    return False


def drop_non_substantive_steps(steps):
    """Filter out no-ops, aborted chat selections, dismissals from the start, and fruitless app visits."""
    if not steps:
        return []

    # 1. Group steps into contiguous visits per application
    visits = []
    current_visit = []
    current_app = None
    for step in steps:
        app = PureWindowsPath(step.get("path") or step.get("app") or "").name.lower()
        if step.get("kind") == "open_app" or (current_app is not None and app != current_app):
            if current_visit:
                visits.append(current_visit)
            current_visit = [step]
            current_app = app
        else:
            current_visit.append(step)
            if current_app is None:
                current_app = app
    if current_visit:
        visits.append(current_visit)

    substantive_visits = []
    for visit in visits:
        # A. Ignore close/dismiss clicks from the very start of a visit before any substantive action
        cleaned = []
        saw_substantive = False
        for s in visit:
            if not saw_substantive and is_dismiss_or_close(s):
                continue
            if is_substantive_step(s):
                saw_substantive = True
            cleaned.append(s)

        # B. Drop blank / new tab pages that had no interaction
        no_blanks = []
        for i, s in enumerate(cleaned):
            if s.get("kind") == "open_url" and is_blank_url(s.get("url")):
                following = cleaned[i + 1:]
                if any(f.get("kind") == "open_url" for f in following) or not any(is_substantive_step(f) for f in following):
                    continue
            no_blanks.append(s)

        # C. Drop dead-end chat / item clicks (superseded by another item selection before any action)
        no_dead_ends = []
        for i, s in enumerate(no_blanks):
            c_type = (s.get("target") or {}).get("control_type")
            is_item = s.get("kind") == "click" and c_type in ("ListItem", "DataItem", "TreeItem")
            if is_item:
                superseded = False
                for follow in no_blanks[i + 1:]:
                    if is_substantive_step(follow):
                        break
                    follow_ctype = (follow.get("target") or {}).get("control_type")
                    if follow.get("kind") == "click" and follow_ctype in ("ListItem", "DataItem", "TreeItem"):
                        superseded = True
                        break
                if superseded:
                    continue
            no_dead_ends.append(s)

        # D. Check if this visit produced any substantive outcome
        has_substance = any(is_substantive_step(s) for s in no_dead_ends)
        if has_substance:
            substantive_visits.append(no_dead_ends)

    out = [s for visit in substantive_visits for s in visit]
    return out


def drop_empty_visits(steps):
    """An app that was only passed through (in front when recording started, or alt-tabbed past)
    has nothing to replay; switching to it would just pull it in front for no reason."""
    out = []
    for index, step in enumerate(steps):
        following = steps[index + 1] if index + 1 < len(steps) else None
        if step["kind"] == "open_app" and (following is None or following.get("path") != step.get("path")
                                           or following["kind"] == "open_app"):
            continue
        out.append(step)
    return out


def clean_steps(steps):
    """What is worth replaying. Applied when a recording is saved and again when it is shown or replayed,
    so older recordings benefit too.
    - Clicks on panes, groups and toolbars, or on the taskbar ('Appid: ...'), are one-off noise.
    - In browsers the pages you ended up on are the routine: in-page clicks (play, pause, panes) are
      dropped, and typing is kept only when it was submitted with Enter.
    - Dead-end chat/item clicks, startup dismissals, and empty app visits that achieved nothing of substance are ignored.
    - Apps visited without doing anything are dropped."""
    out = []
    for index, step in enumerate(steps):
        target = step.get("target") or {}
        browser = PureWindowsPath(step.get("path", "")).name.lower() in BROWSER_EXES
        following = steps[index + 1] if index + 1 < len(steps) else {}
        if step["kind"] == "click" and (browser or (target.get("control_type") and target.get("control_type") not in CLICKABLE_KINDS)
                                        or str(target.get("automation_id", "")).startswith("Appid:")):
            continue
        if step["kind"] == "set_text" and browser and following.get("kind") != "press_enter":
            continue
        out.append(step)

    # Prune non-substantive steps (aborted chat selections, startup dismissals, empty visits)
    substantive = drop_non_substantive_steps(out)
    if substantive:
        out = substantive

    # Dropping noise can leave visits empty; repeat until stable.
    while True:
        trimmed = drop_empty_visits(out)
        if trimmed == out:
            break
        out = trimmed
    if not out:
        # A routine that only opens an app ("open Notepad") keeps the app it ended in.
        opened = [step for step in steps if step["kind"] == "open_app"]
        out = opened[-1:]
    return out


def compress_browsing(steps):
    """A URL step already captures where typing, Enter and link clicks in that browser led,
    so those are folded into it and replay opens the exact page directly. Clicks and typing well
    before the navigation (a Like, a posted comment) did something on the previous page and stay."""
    out, droppable = [], []
    for step in steps:
        kind, path = step["kind"], step.get("path", "")
        browser = PureWindowsPath(path).name.lower() in BROWSER_EXES
        b_name = PureWindowsPath(path).name.lower()
        if kind == "open_url":
            at = step.get("at", 0)
            url = step.get("url", "")
            video = is_video(url)
            typed = any(out[i]["kind"] == "set_text" and "address" in (out[i].get("field") or "").lower()
                        and PureWindowsPath(out[i].get("path", "")).name.lower() == b_name for i in droppable)
            drop = {i for i in droppable if PureWindowsPath(out[i].get("path", "")).name.lower() == b_name and (
                video or out[i]["kind"] == "open_app" or at - out[i].get("at", at) <= FOLD_SECONDS)}
            out = [s for i, s in enumerate(out) if i not in drop]
            droppable = []
            if out and out[-1]["kind"] == "open_url" and PureWindowsPath(out[-1].get("path", "")).name.lower() == b_name:
                prev_url = out[-1].get("url", "")
                # A page you left by typing in the address bar was somewhere you went, however briefly;
                # results pages, and a site's own pages on the way to its video, were only a way there.
                if (is_search(prev_url) or (video and not is_video(prev_url) and site(prev_url) == site(url))
                        or (not typed and at - out[-1].get("at", at) <= PASS_THROUGH_SECONDS)):
                    out.pop()  # passed straight through that page: open only where it ended up
        elif browser and kind in ("open_app", "set_text", "press_enter", "click"):
            droppable.append(len(out))
        else:
            droppable = []
        out.append(step)

    # Post-pass: if a browser session concludes with an open_url to a video, drop redundant earlier
    # intermediate homepage visits or redundant open_app steps for that same browser.
    final_out = []
    for i, step in enumerate(out):
        if step["kind"] == "open_app" and PureWindowsPath(step.get("path", "")).name.lower() in BROWSER_EXES:
            # Check if immediately followed by an open_url in the same browser
            if i + 1 < len(out) and out[i + 1]["kind"] == "open_url" and PureWindowsPath(out[i + 1].get("path", "")).name.lower() == PureWindowsPath(step.get("path", "")).name.lower():
                continue  # open_url directly launches the browser to that page
        final_out.append(step)
    return final_out


@functools.cache
def _user32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return user


def top_window(hwnd=None, point=None):
    """(title, pid) of the top-level window that owns `hwnd`, or the one under a screen point."""
    user = _user32()
    if point is not None:
        hwnd = user.WindowFromPoint(wintypes.POINT(*point))
    root = user.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    title = ctypes.create_unicode_buffer(256)
    user.GetWindowTextW(root, title, 256)
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return title.value, pid.value


def wake_tree(hwnd):
    """Walk one window's UIA tree on a worker thread; big web apps take seconds the first time."""
    try:
        import comtypes
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except Exception:
        pass
    try:
        from pywinauto import Desktop
        Desktop(backend="uia").window(handle=hwnd).wrapper_object().descendants(control_type="Edit")
    except Exception:
        pass


class BrowserWatch(threading.Thread):
    """Reads browser address bars and YouTube's seek slider once a second, off the poll thread:
    the first UIA lookup inside a large page can take seconds. Poll sets `target` to the
    foreground browser window and drains `pages`; `seek` maps video URLs to the last position seen."""

    def __init__(self):
        super().__init__(daemon=True)
        self.target = None  # (hwnd, path) of the foreground browser window, or None
        self.pages = queue.Queue()
        self.seek = {}
        self.titles = {}  # url -> page title, refreshed while the page is shown so it is the settled one
        self.closed = threading.Event()

    def run(self):
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            pass
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
        except Exception:
            return
        bars, sliders, shown = {}, {}, {}
        video = None  # (hwnd, url): keeps tracking playback after the user moves to another app
        while True:
            closing = self.closed.wait(1.0)  # one last read after stop, so the seek time is fresh
            windows = [self.target] if self.target else []
            if video and not any(hwnd == video[0] for hwnd, _ in windows):
                windows.append((video[0], None))  # background video: track its time, record no page step
            for hwnd, path in windows:
                try:
                    if hwnd not in bars:
                        window = desktop.window(handle=hwnd).wrapper_object()
                        edits = window.descendants(control_type="Edit")
                        bar_match = None
                        for e in edits:
                            el = getattr(e, "element_info", None)
                            name = (getattr(el, "name", "") or "").lower()
                            auto_id = (getattr(el, "auto_id", "") or "").lower()
                            cls = (getattr(el, "class_name", "") or "").lower()
                            if any(term in name for term in ("address and search bar", "address bar", "search or type", "search or enter", "url", "location")) or "omnibox" in auto_id or "omnibox" in cls:
                                bar_match = e
                                break
                        if not bar_match:
                            for e in edits:
                                try:
                                    val = e.get_value() if hasattr(e, "get_value") else None
                                except Exception:
                                    val = None
                                if not val:
                                    val = field_text(e, browser=True)
                                if val and web_address(val):
                                    bar_match = e
                                    break
                        bars[hwnd] = bar_match or (edits[0] if edits else None)
                    bar = bars.get(hwnd)
                    if bar is None:
                        continue
                    url_val = None
                    # While the bar has focus it shows what is being typed, with inline autocomplete
                    # ("https://www." completes to a history entry): not a page. Read it once focus leaves.
                    if not bar.has_keyboard_focus():
                        try:
                            url_val = bar.get_value() if hasattr(bar, "get_value") else None
                        except Exception:
                            url_val = None
                        if not url_val:
                            url_val = field_text(bar, browser=True)
                    url = web_address(url_val)
                    if path and url and url != shown.get(hwnd):
                        shown[hwnd] = url
                        self.pages.put((hwnd, path, url))
                    if path and url:
                        # The title can lag the address by a moment; later reads overwrite it.
                        title = page_title(top_window(hwnd)[0])
                        if title:
                            self.titles[url] = title
                    if is_video(url):
                        video = (hwnd, url)
                        if sliders.get(hwnd) is None:
                            try:
                                window = desktop.window(handle=hwnd).wrapper_object()
                                sliders[hwnd] = next((s for s in window.descendants(control_type="Slider")
                                                      if "seek" in (getattr(getattr(s, "element_info", None), "name", "") or "").lower()), None)
                            except Exception:
                                pass
                        seek_slider = sliders.get(hwnd)
                        if seek_slider:
                            try:
                                self.seek[url] = int(seek_slider.iface_range_value.CurrentValue)
                            except Exception:
                                pass
                    elif video and video[0] == hwnd:
                        video = None
                except Exception:
                    # Closed window, reloaded page or tree still waking: look the controls up again next time.
                    bars.pop(hwnd, None)
                    sliders.pop(hwnd, None)
            if closing:
                return


def field_text(control, browser=False):
    """Typed text of a focused edit. Supports ValuePattern, TextPattern, and window text."""
    value = None
    try:
        value = control.get_value()
    except Exception:
        value = None
    if value is None:
        try:
            value = control.window_text()
        except Exception:
            pass
    if value is None:
        value = document_text(control)
    if value is None:
        try:
            value = getattr(getattr(control, "iface_value", None), "CurrentValue", None)
        except Exception:
            pass
    if not browser:
        return value
    name = (control.element_info.name or "").strip().rstrip("….�").strip()

    def typed(text):
        # Chromium reports the placeholder or label as text while a field is empty.
        text = (text or "").strip() if isinstance(text, str) else ""
        return "" if text.rstrip("….�").strip() == name else text

    if typed(value):
        return typed(value)
    return typed(document_text(control))


def document_text(control, limit=4096):
    """Text of a document surface (Word body, rich web editors), or None when unavailable or too long."""
    try:
        text = control.iface_text.DocumentRange.GetText(limit + 1)
    except Exception:
        return None
    return text if isinstance(text, str) and len(text) <= limit else None


def matches_send_snapshot(snapshot, event):
    if not snapshot or event["kind"] != "click" or not event.get("point"):
        return False
    allowed_pids = snapshot.get("pids", {snapshot.get("pid")})
    if event["pid"] not in allowed_pids:
        return False
    age = event["time"] - snapshot["time"]
    x, y = event["point"]
    left, top, right, bottom = snapshot["bounds"]
    return 0 <= age <= 3 and left <= x < right and top <= y < bottom


class Session:
    def __init__(self, options):
        self.name = options.get("name", "").strip()
        if not 1 <= len(self.name) <= 80:
            raise ValueError("Give this recording a name (1–80 characters)")
        self.apps = options.get("apps", ["WhatsApp.exe"])
        if not isinstance(self.apps, list) or not 1 <= len(self.apps) <= 8 or any(
                not isinstance(app, str) or not (app == "*" or re.fullmatch(r"[\w .-]+\.exe", app, re.I)) for app in self.apps):
            raise ValueError("Choose 1–8 executable names, such as WhatsApp.exe or notepad.exe")
        self.everything = "*" in self.apps
        self.allowed = set()
        for app in self.apps:
            name = app.lower()
            self.allowed.add(name)
            if is_whatsapp(name):
                self.allowed.update(WHATSAPP_EXES)
        self.native = Native()
        self.steps = []
        self.started = time.monotonic()
        self.last_edit = None
        self.focus_key = self.focus_base = None
        self.send_snapshot = None
        self.send_scanned = 0.0
        self.captured = None  # (field, text) last given to Capture
        self.last_foreground = None
        self.observed_paths = {}
        self.woken = set()
        self.last_url = None
        self.capture = None
        if options.get("recipient"):
            recipient = options["recipient"].strip()
            message = options.get("message", "hello")
            search = options.get("search", recipient).strip() or recipient
            title = options.get("chat_title", recipient).strip() or recipient
            if not message or len(message) > 4096 or any(ord(c) < 32 for c in message) or search == message:
                raise ValueError("Use a distinct contact search and single-line message (max 4096 characters)")
            self.capture = Capture(recipient, search, title, message)
        elif options.get("whatsapp") or self.everything:
            # Any recording that touches WhatsApp also learns who was searched and what was typed.
            self.capture = Capture()
        self.visual_memory = None
        if options.get("enable_visual_memory", True):
            try:
                from agent.ghost.collector.visual_memory import VisualMemory
                self.visual_memory = VisualMemory()
            except Exception:
                self.visual_memory = None
        self.shopify_active = options.get("shopify", True)
        self.shopify_detected = False
        self.shopify_data = {
            "title": "",
            "description": "",
            "price": "29.99",
            "compare_at_price": "39.99",
            "cost_per_item": "12.00",
            "sku": "",
            "inventory_quantity": 50,
            "category": "Apparel & Accessories",
            "tags": "featured, new-arrival",
            "status": "Active",
            "store_url": "https://admin.shopify.com/store/gzn0vr-qn/products/new",
        }
        self.replay_error = None
        self.events = WinEvents(everything=self.everything)
        self.events.__enter__()
        self.watch = BrowserWatch()
        self.watch.start()

    def _active_pids(self, pid, path):
        pids = {pid}
        if self.everything or is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES:
            try:
                proc = self.native.psutil.Process(pid)
                for child in proc.children(recursive=True):
                    pids.add(child.pid)
            except (OSError, self.native.psutil.Error):
                pass
        return pids

    def _ignored(self, hwnd=None, point=None):
        """DigitalSoul's own windows (notch, ghost) and never-record apps, judged by the top-level
        window: in Everything mode a click on the notch must not become a step of the app behind it."""
        try:
            title, pid = top_window(hwnd, point)
            if title in OWN_TITLES:
                return True
            if pid not in self.observed_paths:
                self.observed_paths[pid] = executable(pid)
            return PureWindowsPath(self.observed_paths[pid]).name.lower() in NEVER_RECORD
        except Exception:
            return True  # an owner we cannot identify is not recorded

    def _identity(self, window):
        try:
            return header(window, self.capture.flow["chat_title"])
        except ValueError:
            # A clicked search result can carry extra text; the header usually matches the search.
            return header(window, self.capture.flow["search"])

    def _wake(self, window):
        # Chromium, Electron and WebView2 apps (WhatsApp, Chrome, Brave) expose only their page
        # root until a UIA client walks the tree; one walk per window turns on the real controls.
        if window.handle in self.woken:
            return
        self.woken.add(window.handle)
        threading.Thread(target=wake_tree, args=(window.handle,), daemon=True).start()

    def _hit(self, window, point):
        self._wake(window)
        control = self.native.desktop.from_point(*point)
        # While Chromium builds its tree the hit-test returns the page root; retry briefly.
        for _ in range(3):
            if control.element_info.control_type not in ROOT_KINDS:
                break
            time.sleep(0.15)
            control = self.native.desktop.from_point(*point)
        return control

    def _track_url(self, window, path):
        """Browsers: each new page the address bar settles on (typed, a link, a tab switch) is a step."""
        browser = window is not None and PureWindowsPath(path).name.lower() in BROWSER_EXES
        self.watch.target = (window.handle, path) if browser else None
        while True:
            try:
                _, page_path, url = self.watch.pages.get_nowait()
            except queue.Empty:
                return
            if url != self.last_url:
                self.last_url = url
                self.append({"kind": "open_url", "app": PureWindowsPath(page_path).name, "path": page_path, "url": url,
                             "label": re.sub(r"^https?://(www\.)?", "", url)[:80]})

    def _video_time(self):
        """On stop: YouTube videos resume where they were last seen playing, even if the user moved on."""
        self.watch.closed.set()
        self.watch.join(timeout=3)
        while True:
            try:
                _, page_path, url = self.watch.pages.get_nowait()
                if url != self.last_url:
                    self.last_url = url
                    self.append({"kind": "open_url", "app": PureWindowsPath(page_path).name, "path": page_path, "url": url,
                                 "label": re.sub(r"^https?://(www\.)?", "", url)[:80]})
            except queue.Empty:
                break
        for step in self.steps:
            if step["kind"] == "open_url" and self.watch.titles.get(step["url"]):
                step["title"] = self.watch.titles[step["url"]]
            seconds = self.watch.seek.get(step.get("url")) if step["kind"] == "open_url" else None
            if seconds and is_video(step["url"]):
                step["url"] = with_time(step["url"], seconds)
                step["resume"] = seconds

    def _foreground(self):
        user = self.native.user
        import ctypes
        from ctypes import wintypes
        hwnd = user.GetForegroundWindow()
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        pid = wintypes.DWORD(hosted_pid(hwnd, pid.value))  # a Store app, not its frame host
        path = self.observed_paths.get(pid.value)
        if path is None:
            path = executable(pid.value)
            self.observed_paths[pid.value] = path
        exe = PureWindowsPath(path).name.lower()
        if not (self.everything or exe in self.allowed):
            try:
                top = self.native.desktop.window(handle=hwnd).top_level_parent()
                top_pid = top.element_info.process_id
                top_path = self.observed_paths.get(top_pid) or executable(top_pid)
                self.observed_paths[top_pid] = top_path
                top_exe = PureWindowsPath(top_path).name.lower()
                if self.everything or top_exe in self.allowed:
                    hwnd = top.handle
                    pid = wintypes.DWORD(top_pid)
                    path = top_path
                    exe = top_exe
            except Exception:
                pass
        if exe in NEVER_RECORD or not (self.everything or exe in self.allowed) or self._ignored(hwnd):
            return None
        window = self.native.desktop.window(handle=hwnd).wrapper_object()
        if exe == "explorer.exe":
            cls = getattr(getattr(window, "element_info", None), "class_name", "") or ""
            if cls in SHELL_CLASSES:
                return None
        active_pids = self._active_pids(pid.value, path)
        self.events.pids.update(active_pids)
        return window, pid.value, path, active_pids

    def append(self, step):
        if len(self.steps) >= 500:
            raise ValueError("Recording reached 500 steps; stop and save it")
        step["at"] = round(time.monotonic() - self.started, 2)
        if getattr(self, "visual_memory", None):
            try:
                app_name = step.get("app") or "Desktop"
                window_title = getattr(self, "last_foreground", None) or app_name
                target_name = step.get("label")
                if not target_name and isinstance(step.get("target"), dict):
                    target_name = step["target"].get("name")
                mem_item = self.visual_memory.capture_and_remember(
                    app=app_name,
                    window_title=window_title,
                    action_kind=step.get("kind", "action"),
                    target_name=target_name,
                    session_id=self.name,
                    analyze_with_omniroute=True,
                )
                if mem_item.screenshot_path:
                    step["screenshot_path"] = mem_item.screenshot_path
                if mem_item.ocr_text:
                    step["ocr_snippet"] = mem_item.ocr_text[:120].strip()
                if mem_item.intent_summary:
                    step["intent"] = mem_item.intent_summary
            except Exception:
                pass
        last = self.steps[-1] if self.steps else {}
        # Typing is polled, so one field's growing (or shrinking) text replaces its previous step. Fields
        # with the same locator but other labels (WhatsApp's search box and composer) stay separate.
        if step["kind"] == "set_text" and last.get("kind") == "set_text" and all(
                last.get(key) == step.get(key) for key in ("target", "path")) and (
                last.get("field") == step.get("field") or
                step["value"].startswith(last["value"]) or last["value"].startswith(step["value"])):
            self.steps[-1] = step
        else:
            self.steps.append(step)

    def poll(self):
        if self.events.error:
            raise RuntimeError("Windows input event recording failed") from self.events.error
        if time.monotonic() - self.started > 300:
            return "limit"
        active = None
        try:
            active = self._foreground()
        except (OSError, ValueError, self.native.psutil.Error):
            pass
        if active:
            window, pid, path, active_pids = active
            if self.last_foreground != path:
                title = ""
                try:
                    title = (window.window_text() or window.element_info.name or "").strip()
                except Exception:
                    pass
                step = {"kind": "open_app", "app": PureWindowsPath(path).name, "path": path}
                if title:
                    step["title"] = title
                if "\\windowsapps\\" in path.lower():
                    # Store apps cannot be started from their executable; replay launches the app identity.
                    step["app_id"] = self.native.app_id(pid)
                self.append(step)
                self.last_foreground = path
            self._wake(window)
            if self.capture and "executable" not in self.capture.flow and is_whatsapp(path):
                self.capture.flow.update(executable=path, app_id=self.native.app_id(pid))
        self._track_url(active[0] if active else None, active[2] if active else None)
        while True:
            try:
                event = self.events.queue.get_nowait()
            except queue.Empty:
                break
            if event["kind"] == "stop":
                return "stop"
            if not active or (not self.everything and event["pid"] not in active_pids):
                continue
            if time.monotonic() - event["time"] > 2:
                self.replay_error = "Input events arrived too late for reliable replay; evidence saved for review"
                continue
            try:
                if event["kind"] == "enter":
                    self.append({"kind": "press_enter", "app": PureWindowsPath(path).name, "path": path})
                    if self.capture:
                        if not self.capture.composed and is_whatsapp(path):
                            try:
                                focus = self.native.focused()
                                if focus.element_info.process_id in active_pids and focus.element_info.control_type == "Edit":
                                    val = (field_text(focus, is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES) or "").strip()
                                    if val and (not self.capture.auto or self.capture.selected) and val != self.capture.flow["search"].strip():
                                        ident = self._identity(window)
                                        spec_edit = locator(focus, editable=True)
                                        with contextlib.redirect_stdout(sys.stderr):
                                            self.capture.field(spec_edit, val, ident)
                            except Exception:
                                pass
                        if self.capture.composed:
                            self.capture.enter()
                else:
                    if self._ignored(point=event["point"]):
                        continue  # the notch's own buttons, or a never-record window
                    # Send can disappear before post-click hit-testing. Use only a
                    # recent, process-bound UIA observation to recognize the demo.
                    # These pixels are transient evidence and are never replayed.
                    if self.capture and is_whatsapp(path) and not self.capture.composed:
                        try:
                            focus = self.native.focused()
                            if focus.element_info.process_id in active_pids and focus.element_info.control_type == "Edit":
                                val = (field_text(focus, is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES) or "").strip()
                                if val and (not self.capture.auto or self.capture.selected) and val != self.capture.flow["search"].strip():
                                    ident = self._identity(window)
                                    spec_edit = locator(focus, editable=True)
                                    with contextlib.redirect_stdout(sys.stderr):
                                        self.capture.field(spec_edit, val, ident)
                        except Exception:
                            pass
                    if self.capture and self.capture.composed and matches_send_snapshot(self.send_snapshot, event):
                        snapshot = self.send_snapshot
                        with contextlib.redirect_stdout(sys.stderr):
                            self.capture.click(snapshot["target"], snapshot["name"], "Button")
                        self.append({"kind": "click", "app": PureWindowsPath(path).name, "path": path,
                                     "label": "Send", "target": snapshot["target"]})
                        continue
                    control = self._hit(window, event["point"])
                    # The taskbar, desktop or another app's window: not part of the app in front (Everything
                    # mode's active_pids already include the app's child processes, e.g. WebView2).
                    if control.element_info.process_id not in active_pids:
                        continue
                    # Climb to the nearest named control, but never past a page or window root:
                    # "Click <whole page>" cannot be replayed.
                    leaf, found, named = control, None, None
                    for _ in range(6):
                        name, kind = control.element_info.name or "", control.element_info.control_type
                        if kind in ROOT_KINDS:
                            break
                        if name and kind in CLICKABLE_KINDS:
                            found = control
                            break
                        named = named or (control if name else None)
                        control = control.parent()
                        if control is None:
                            break
                    # Fall back to the nearest named element, then the clicked element itself.
                    control = found or named or leaf
                    name, kind = control.element_info.name or "", control.element_info.control_type
                    if kind not in CLICKABLE_KINDS:
                        continue  # panes, groups, toolbars, page roots: nothing replayable was clicked
                    label = clean_control_name(control, name, kind)
                    spec = locator(control, editable=kind == "Edit")
                    if label and label not in ("Text field", kind) and not spec.get("name"):
                        spec["name"] = label
                    self.append({"kind": "click", "app": PureWindowsPath(path).name, "path": path,
                                 "label": label, "target": spec})
                    if self.capture and is_whatsapp(path):
                        with contextlib.redirect_stdout(sys.stderr):
                            self.capture.click(spec, name, kind)
            except Exception:
                self.replay_error = "A clicked control disappeared before UIA could resolve it; record more slowly for replay"
        if not active:
            return None
        try:
            focus = self.native.focused()
            is_excel = "excel" in PureWindowsPath(path).name.lower()
            # Browsers expose their editable boxes as Edit; a focused web page (Document) is the whole page,
            # whose text must never be recorded as typing.
            browser = PureWindowsPath(path).name.lower() in BROWSER_EXES
            kinds = ("Edit",) if is_whatsapp(path) or browser else (("Edit", "Document", "DataItem") if is_excel else ("Edit", "Document"))
            if focus.element_info.process_id not in active_pids or focus.element_info.control_type not in kinds:
                return None
            if focus.element_info.element.CurrentIsPassword or SECRET_FIELD.search(focus.element_info.name or ""):
                return None
            value = field_text(focus, is_whatsapp(path) or PureWindowsPath(path).name.lower() in BROWSER_EXES)
            if not isinstance(value, str) or len(value) > 4096:
                return None
            value_clean = value.strip()
            try:
                key = tuple(focus.element_info.runtime_id)
            except Exception:
                key = (path, focus.element_info.control_type, focus.element_info.automation_id)
            if key != self.focus_key:
                # Text already in a field when it gains focus (a URL, a placeholder) was not typed by the user.
                self.focus_key, self.focus_base = key, value_clean
            if not value_clean or value_clean == self.focus_base:
                return None
            spec = locator(focus, editable=True)
            if is_excel and focus.element_info.control_type == "DataItem":
                spec["name"] = focus.element_info.name or ""
            self.last_edit = spec
            if not self.steps or self.steps[-1].get("value") != value_clean or self.steps[-1].get("target") != spec:
                label = "Text field"
                field = (focus.element_info.name or "").strip()
                if is_excel and field:
                    from .excel import clean_cell_name
                    cell_coord = clean_cell_name(field)
                    if cell_coord:
                        label = f"Cell {cell_coord}"
                step = {"kind": "set_text", "app": PureWindowsPath(path).name, "path": path,
                        "label": label, "target": spec, "value": value_clean}
                if field and field != value_clean and len(field) <= 160:
                    step["field"] = field  # e.g. WhatsApp's "Type a message to <chat>" names the recipient
                self.append(step)
            if self.shopify_active and not is_whatsapp(path):
                win_text = ""
                try:
                    win_text = (window.window_text() or "").lower()
                except Exception:
                    pass
                name_low = self.name.lower()
                fname = (focus.element_info.name or "").lower()
                fid = (focus.element_info.automation_id or "").lower()
                if ("shopify" in win_text or "products" in win_text or "shopify" in name_low
                        or any("shopify" in str(s.get("value", "")).lower() or "shopify" in str(s.get("label", "")).lower() for s in self.steps)):
                    self.shopify_detected = True
                    if "price" in fname or "price" in fid or (value_clean.replace(".", "", 1).isdigit() and len(value_clean) <= 7):
                        self.shopify_data["price"] = value_clean
                    elif "sku" in fname or "sku" in fid:
                        self.shopify_data["sku"] = value_clean
                    elif "desc" in fname or "desc" in fid or len(value_clean) > 40:
                        self.shopify_data["description"] = value_clean
                    elif not self.shopify_data["title"] or "title" in fname or "title" in fid:
                        self.shopify_data["title"] = value_clean
            if self.capture and not self.capture.done and is_whatsapp(path):
                # The chat-header lookup and Send-button scan walk the whole window, so they run when
                # the text changes (and the scan every 2 s while it rests), not on every 40 ms poll.
                if (key, value_clean) != self.captured:
                    # Capture ignores the header once composed; only an uncomposed message needs it.
                    is_msg = not self.capture.composed and (value_clean == self.capture.flow["message"].strip() or (
                        (not self.capture.auto or self.capture.selected) and value_clean != self.capture.flow["search"].strip()))
                    identity = self._identity(window) if is_msg else None
                    with contextlib.redirect_stdout(sys.stderr):
                        self.capture.field(spec, value_clean, identity)
                    self.captured = (key, value_clean)
                    if value_clean != self.capture.flow["message"].strip():
                        self.send_snapshot = None
                if self.capture.composed and value_clean == self.capture.flow["message"].strip() and (
                        time.monotonic() - self.send_scanned > 2):
                    self.send_scanned = time.monotonic()
                    buttons = [b for b in window.descendants(control_type="Button")
                               if b.is_visible() and b.is_enabled() and
                               ((b.element_info.name or "").lower().startswith("send") or
                                "send" in (b.element_info.automation_id or "").lower())]
                    if len(buttons) == 1:
                        button = buttons[0]
                        rect = button.rectangle()
                        self.send_snapshot = {"pid": pid, "pids": active_pids, "time": time.monotonic(),
                                              "bounds": (rect.left, rect.top, rect.right, rect.bottom),
                                              "target": locator(button), "name": button.element_info.name or "Send"}
        except Exception:
            # Raw evidence remains reviewable; missing identity must never create a runnable flow.
            self.replay_error = "Some editor/header evidence was unavailable; inspect the saved steps"
        return None

    def finish(self):
        self.events.__exit__(None, None, None)
        flow = None
        if self.capture and self.capture.composed and not self.capture.done:
            # Typed but never sent: the routine still means "send this", with Enter on replay.
            self.capture.enter()
            self.capture.flow["unsent"] = True
        # validate() is the integrity guard; unrelated evidence gaps elsewhere must not drop a complete send.
        if self.capture and self.capture.done:
            try:
                validate(self.capture.flow)
                flow = self.capture.flow
            except (ValueError, KeyError) as exc:
                self.replay_error = str(exc)
        if not flow:
            # Sturdier fallback: rebuild the send from WhatsApp's labelled message box and what was typed.
            from .whatsapp_send import derive
            try:
                flow = derive(self.steps)
            except ValueError:
                flow = None
            if flow:
                self.replay_error = None
        if not flow:
            from .excel import derive_excel_flow
            flow = derive_excel_flow(self.steps)
            if flow:
                self.replay_error = None
        if getattr(self, "shopify_active", False) and (getattr(self, "shopify_detected", False) or "shopify" in self.name.lower()) and not flow:
            from ..browser.shopify import generate_product_fields
            flow = {
                "kind": "shopify",
                "product": generate_product_fields(getattr(self, "shopify_data", {})),
            }
            self.replay_error = None
        used_whatsapp = any(is_whatsapp(step.get("path", "")) and step["kind"] != "open_app" for step in self.steps)
        if self.capture and used_whatsapp and not flow and not self.replay_error:
            self.replay_error = "Search, contact click, exact message, verified header and Send must all be observed for replay"
        self._video_time()
        self.steps = clean_steps(compress_browsing(self.steps))
        return {"name": self.name, "apps": self.apps, "steps": self.steps, "flow": flow,
                "duration": round(time.monotonic() - self.started, 1), "replay_error": self.replay_error}
