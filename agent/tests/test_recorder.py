"""Recorder accuracy: pure helpers and Session pieces driven with fakes (no hooks, no UIA)."""
import queue
import sys
import threading
import types

import pytest

from agent.ghost.desktop import session as rec
from agent.ghost.desktop.catalog import clock, summarize, understand_steps
from agent.ghost.desktop.session import Session, compress_browsing, is_video, web_address, with_time

BRAVE = r"C:\Apps\brave.exe"
CHROME = r"C:\Apps\chrome.exe"
WA = r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.2640.100.0_x64__cv1g1gvanyjgm\WhatsApp.Root.exe"
VIDEO = "https://www.youtube.com/watch?v=abc"


def step(kind, path=BRAVE, at=0, **extra):
    return {"kind": kind, "path": path, "app": path.rsplit("\\", 1)[-1], "at": at, **extra}


# --- web_address / with_time / is_video -------------------------------------------------------

@pytest.mark.parametrize("value, url", [
    ("youtube.com/watch?v=x", "https://youtube.com/watch?v=x"),
    ("https://example.com/a b", None),  # spaces mean search text
    ("http://example.com", "http://example.com"),
    ("example.com", "https://example.com"),
    ("example.com:8080/x", "https://example.com:8080/x"),
    ("localhost:3000/app", "http://localhost:3000/app"),
    ("chrome://settings", None),
    ("how to cook rice", None),
    ("", None),
    (None, None),
    ("a.b/" + "x" * 2001, None),
])
def test_web_address(value, url):
    assert web_address(value) == url


def test_with_time_replaces_any_existing_time():
    assert with_time(VIDEO, 95) == VIDEO + "&t=95s"
    assert with_time(VIDEO + "&t=3s", 95) == VIDEO + "&t=95s"
    assert with_time("https://youtu.be/abc?t=3", 7) == "https://youtu.be/abc?t=7s"
    assert with_time("https://youtu.be/abc?t=3&si=q", 7) == "https://youtu.be/abc?si=q&t=7s"
    assert with_time("https://youtu.be/abc", 7.9) == "https://youtu.be/abc?t=7s"


@pytest.mark.parametrize("window, title", [
    ("Alan Turing - Wikipedia - Brave", "Alan Turing - Wikipedia"),
    ("(3) Me at the zoo - YouTube - Brave", "Me at the zoo - YouTube"),
    ("Inbox - Google Chrome", "Inbox"),
    ("Docs and 4 more pages - Work - Microsoft​ Edge", "Docs"),
    ("New Tab - Brave", ""),
    ("Brave", ""),
    ("", ""),
])
def test_page_title_drops_the_browser_name(window, title):
    assert rec.page_title(window) == title


def test_is_video():
    assert is_video(VIDEO) and is_video("https://youtu.be/abc") and is_video("https://m.youtube.com/shorts/x")
    assert not is_video("https://www.youtube.com/") and not is_video(None)


# --- compress_browsing ------------------------------------------------------------------------

def test_typing_and_enter_that_led_to_a_page_fold_into_it():
    steps = [step("open_app"), step("set_text", at=2, value="youtube"), step("press_enter", at=3),
             step("open_url", at=4, url="https://youtube.com/")]
    assert [s["kind"] for s in compress_browsing(steps)] == ["open_url"]


def test_an_old_click_on_the_previous_page_stays():
    steps = [step("open_url", at=0, url="https://news.example/"), step("click", at=5, label="Like"),
             step("click", at=200, label="Next story"), step("open_url", at=201, url="https://news.example/2")]
    kept = compress_browsing(steps)
    assert [s.get("label") or s["url"] for s in kept] == ["https://news.example/", "Like", "https://news.example/2"]


def test_other_apps_and_other_browsers_are_never_folded():
    steps = [step("set_text", path=WA, at=1, value="hi"), step("click", path=CHROME, at=2, label="Tab"),
             step("open_url", at=3, url="https://a.example/")]
    assert compress_browsing(steps) == steps


def test_pages_passed_straight_through_keep_only_the_last():
    steps = [step("open_url", at=0, url="https://youtube.com/"), step("open_url", at=1, url=VIDEO),
             step("set_text", path=WA, at=9, value="hi")]
    assert [s.get("url") for s in compress_browsing(steps)] == [VIDEO, None]


@pytest.mark.parametrize("url, search", [
    ("https://search.brave.com/search?q=alan+turing&source=desktop", True),
    ("https://www.google.com/search?client=x&q=alan", True),
    ("https://duckduckgo.com/?q=alan", True),
    ("https://www.youtube.com/results?search_query=zoo", True),
    ("https://en.wikipedia.org/wiki/Alan_Turing", False),
    ("https://example.com/item?query_id=3", False),
    (VIDEO, False),
])
def test_is_search(url, search):
    assert rec.is_search(url) is search


def test_a_search_then_a_result_then_a_typed_address_keeps_both_destinations():
    """Real Brave recording: search typed in the bar, a result clicked, then a video's address typed ~7 s later."""
    wiki = "https://en.wikipedia.org/wiki/Alan_Turing"
    steps = [step("open_app", at=1.6), step("set_text", at=5.9, value="alan turing wikipedia", field="Address and search bar"),
             step("press_enter", at=7.4), step("open_url", at=7.7, url="https://search.brave.com/search?q=alan+turing+wikipedia"),
             step("click", at=14.4, label="Alan Turing - Wikipedia", target={"control_type": "Hyperlink"}, point=[737, 388]),
             step("double_click", at=14.6, label="Alan Turing - Wikipedia", point=[737, 388]),
             step("open_url", at=15.8, url=wiki), step("hotkey", at=21.5, combo="^l"),
             step("set_text", at=22.0, value=VIDEO, field="Address and search bar"), step("press_enter", at=22.8),
             step("open_url", at=23.0, url=VIDEO)]
    assert [s.get("url") for s in rec.clean_steps(compress_browsing(steps))] == [wiki, VIDEO]


def test_a_site_page_on_the_way_to_its_video_is_dropped_but_another_site_is_not():
    steps = [step("open_url", at=0, url="https://www.youtube.com/"), step("open_url", at=60, url=VIDEO)]
    assert [s["url"] for s in compress_browsing(steps)] == [VIDEO]
    steps = [step("open_url", at=0, url="https://news.example/"), step("open_url", at=60, url=VIDEO)]
    assert [s["url"] for s in compress_browsing(steps)] == ["https://news.example/", VIDEO]


def test_empty_and_non_browser_recordings_are_unchanged():
    assert compress_browsing([]) == []
    steps = [step("open_app", path=WA), step("set_text", path=WA, value="x"), step("press_enter", path=WA)]
    assert compress_browsing(steps) == steps


# --- catalog summaries ------------------------------------------------------------------------

def test_clock():
    assert clock(5) == "0:05" and clock(95) == "1:35" and clock(3725) == "1:02:05"


def test_summarize_shows_the_page_and_resume_time():
    value = {"steps": [step("open_app"), step("open_url", url=VIDEO + "&t=95s", label="youtube.com/watch?v=abc", resume=95),
                       step("set_text", value="nice"), step("click", label="Text field"), step("click", label="Subscribe"),
                       step("click", label="Subscribe")]}
    assert summarize(value) == [{"kind": "url", "text": "youtube.com/watch?v=abc @ 1:35"},
                                {"kind": "typed", "text": "nice"}, {"kind": "click", "text": "Subscribe"}]
    assert summarize({"flow": {"recipient": "Vansh", "message": "hi"}, "steps": []})[0] == {"kind": "to", "text": "Vansh"}
    assert summarize({"steps": []}) == []
    titled = {"steps": [step("open_url", url="https://en.wikipedia.org/wiki/Alan_Turing", label="en.wikipedia.org/wiki/Alan_Turing",
                             title="Alan Turing - Wikipedia")]}
    assert summarize(titled) == [{"kind": "url", "text": "Alan Turing - Wikipedia"}]
    assert understand_steps(titled["steps"])["explanations"] == ["Opened “Alan Turing - Wikipedia”"]


def test_browsing_recordings_are_understood_as_navigation():
    u = understand_steps([step("open_url", url=VIDEO + "&t=95s", label="youtube.com/watch?v=abc", resume=95)])
    assert u["intent"] == f"Navigated to {VIDEO}&t=95s"
    assert u["explanations"] == ["Opened youtube.com/watch?v=abc at 1:35"]


# --- Session pieces with fakes ----------------------------------------------------------------

class FakeWatch:
    def __init__(self, seek=None):
        self.target, self.pages, self.seek, self.closed = None, queue.Queue(), seek or {}, threading.Event()
        self.titles = {}

    def join(self, timeout=None):
        pass


def bare_session(steps=(), seek=None):
    """A Session without Win32 hooks, UIA or the browser thread."""
    s = Session.__new__(Session)
    s.name, s.apps, s.started = "test", ["*"], 0.0
    s.steps, s.capture, s.replay_error, s.last_url = list(steps), None, None, None
    s.events = types.SimpleNamespace(__exit__=lambda *a: None)
    s.watch = FakeWatch(seek)
    return s


def test_typing_merges_per_field_but_search_and_composer_stay_separate():
    s = bare_session()
    target = {"control_type": "Edit", "automation_id": "", "name": "", "ancestors": []}
    for value in ("v", "va", "vansh"):
        s.append(step("set_text", path=WA, target=target, value=value, field="Search"))
    s.append(step("set_text", path=WA, target=target, value="vans", field="Search"))  # backspace
    s.append(step("set_text", path=WA, target=target, value="hello", field="Type a message to Vansh"))
    s.append(step("set_text", path=WA, target=target, value="hello there", field="Type a message to Vansh"))
    assert [(x["value"], x["field"]) for x in s.steps] == [("vans", "Search"), ("hello there", "Type a message to Vansh")]


def test_page_steps_come_from_the_watch_thread_and_repeat_only_on_change():
    s = bare_session()
    window = types.SimpleNamespace(handle=7)
    for url in ("https://a.example/", "https://a.example/", "https://b.example/"):
        s.watch.pages.put((7, BRAVE, url))
    s._track_url(window, BRAVE)
    assert s.watch.target == (7, BRAVE)
    assert [x["url"] for x in s.steps] == ["https://a.example/", "https://b.example/"]
    assert s.steps[1]["label"] == "b.example/"
    s._track_url(types.SimpleNamespace(handle=8), WA)  # WhatsApp in front: stop reading the browser
    assert s.watch.target is None
    s._track_url(None, None)
    assert s.watch.target is None


def test_finish_resumes_videos_even_after_the_user_moved_on():
    s = bare_session([step("open_url", url=VIDEO), step("set_text", path=WA, at=20, value="hi"),
                      step("open_url", at=30, url="https://www.youtube.com/")], seek={VIDEO: 95})
    result = s.finish()
    assert s.watch.closed.is_set()
    video = next(x for x in result["steps"] if x.get("url", "").startswith(VIDEO))
    assert video["url"] == VIDEO + "&t=95s" and video["resume"] == 95
    assert result["flow"] is None and result["replay_error"] is None


def test_finish_names_each_page_by_its_settled_title():
    s = bare_session([step("open_url", url=VIDEO), step("open_url", at=30, url="https://en.wikipedia.org/wiki/X")],
                     seek={VIDEO: 95})
    s.watch.titles = {VIDEO: "Me at the zoo - YouTube"}
    steps = s.finish()["steps"]
    assert steps[0]["title"] == "Me at the zoo - YouTube" and steps[0]["url"] == VIDEO + "&t=95s"
    assert "title" not in steps[1]  # never seen settled: the address is shown instead


def test_finish_rebuilds_a_whatsapp_send_from_the_labelled_composer():
    s = bare_session([step("open_app", path=WA, app_id="5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"),
                      step("set_text", path=WA, value="vansh", field="Search input textbox"),
                      step("click", path=WA, label="Vansh Arora"),
                      step("set_text", path=WA, value="birthday   postponed", field="Type a message to Vansh Arora"),
                      step("press_enter", path=WA)])
    flow = s.finish()["flow"]
    assert flow["recipient"] == "Vansh Arora" and flow["search"] == "vansh"
    assert flow["message"] == "birthday postponed" and flow["unsent"] is False


def test_browser_watch_reads_the_address_bar_and_seek_slider(monkeypatch):
    bar = types.SimpleNamespace(has_keyboard_focus=lambda: False, get_value=lambda: "youtube.com/watch?v=abc")
    slider = types.SimpleNamespace(element_info=types.SimpleNamespace(name="Seek slider"),
                                   iface_range_value=types.SimpleNamespace(CurrentValue=95.4))

    class Window:
        def descendants(self, control_type, **_):
            return [bar] if control_type == "Edit" else [slider]

    class Desktop:
        def __init__(self, backend):
            pass

        def window(self, handle):
            return types.SimpleNamespace(wrapper_object=Window)

    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    watch = rec.BrowserWatch()
    watch.target = (7, BRAVE)
    watch.closed.set()  # one final read, then exit
    monkeypatch.setattr(rec, "top_window", lambda hwnd: ("(2) Me at the zoo - YouTube - Brave", 1))
    watch.run()
    assert watch.pages.get_nowait() == (7, BRAVE, "https://youtube.com/watch?v=abc")
    assert watch.seek == {"https://youtube.com/watch?v=abc": 95}
    assert watch.titles == {"https://youtube.com/watch?v=abc": "Me at the zoo - YouTube"}


def test_browser_watch_ignores_the_bar_while_it_is_being_typed_in(monkeypatch):
    # Typing "https://www." autocompletes inline to a history entry; that is not a page you opened.
    bar = types.SimpleNamespace(has_keyboard_focus=lambda: True, get_value=lambda: "https://www.figma.com")

    class Window:
        def descendants(self, control_type, **_):
            return [bar] if control_type == "Edit" else []

    class Desktop:
        def __init__(self, backend):
            pass

        def window(self, handle):
            return types.SimpleNamespace(wrapper_object=Window)

    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    monkeypatch.setattr(rec, "top_window", lambda hwnd: ("New Tab - Brave", 1))
    watch = rec.BrowserWatch()
    watch.target = (7, BRAVE)
    watch.closed.set()
    watch.run()
    assert watch.pages.empty() and watch.titles == {}


def test_browser_watch_keeps_looking_for_a_seek_slider_that_appears_late(monkeypatch):
    bar = types.SimpleNamespace(has_keyboard_focus=lambda: False, get_value=lambda: "youtube.com/watch?v=abc")
    slider = types.SimpleNamespace(element_info=types.SimpleNamespace(name="Seek slider"),
                                   iface_range_value=types.SimpleNamespace(CurrentValue=12))
    lookups = []

    class Window:
        def descendants(self, control_type, **_):
            if control_type == "Edit":
                return [bar]
            lookups.append(1)
            return [slider] if len(lookups) > 1 else []  # the player builds its controls after the page

    class Desktop:
        def __init__(self, backend):
            pass

        def window(self, handle):
            return types.SimpleNamespace(wrapper_object=Window)

    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    monkeypatch.setattr(rec, "top_window", lambda hwnd: ("Brave", 1))
    watch = rec.BrowserWatch()
    watch.target = (7, BRAVE)
    ticks = iter([False, True])
    watch.closed = types.SimpleNamespace(wait=lambda timeout: next(ticks))
    watch.run()
    assert watch.seek == {"https://youtube.com/watch?v=abc": 12}


def test_secret_fields_and_own_windows_are_recognised():
    assert rec.SECRET_FIELD.search("Password") and rec.SECRET_FIELD.search("Enter PIN")
    assert rec.SECRET_FIELD.search("One-time code") and rec.SECRET_FIELD.search("CVV")
    assert not rec.SECRET_FIELD.search("Type a message to Pinky") and not rec.SECRET_FIELD.search("Search")
    assert {"DigitalSoul", "DigitalSoul ghost"} <= rec.OWN_TITLES and "digitalsoul-notch.exe" in rec.NEVER_RECORD


def test_watched_video_is_kept_when_moving_on_to_the_next_one():
    from agent.ghost.desktop.session import compress_browsing
    b = r"C:\Apps\brave.exe"
    steps = [{"kind": "open_url", "path": b, "url": "https://youtube.com/", "at": 0},
             {"kind": "open_url", "path": b, "url": "https://youtube.com/watch?v=a&t=300s", "resume": 300, "at": 3},
             {"kind": "open_url", "path": b, "url": "https://youtube.com/watch?v=b", "at": 400}]
    urls = [s["url"] for s in compress_browsing(steps)]
    assert urls == ["https://youtube.com/watch?v=a&t=300s", "https://youtube.com/watch?v=b"]


def test_apps_only_passed_through_are_not_replayed():
    from agent.ghost.desktop.session import drop_empty_visits
    ide, notepad = r"C:\Apps\ide.exe", r"C:\Windows\notepad.exe"
    steps = [{"kind": "open_app", "path": ide}, {"kind": "open_app", "path": notepad},
             {"kind": "set_text", "path": notepad, "value": "hi"}, {"kind": "open_app", "path": ide}]
    assert [(s["kind"], s["path"]) for s in drop_empty_visits(steps)] == [("open_app", notepad), ("set_text", notepad)]


def test_clean_steps_drops_one_off_noise_but_keeps_the_routine():
    from agent.ghost.desktop.session import clean_steps
    claude, brave = r"C:\Apps\claude.exe", r"C:\Apps\brave.exe"
    recorded = [  # shape of a real recording: taskbar click, pane clicks, player clicks around two videos
        {"kind": "open_app", "path": claude},
        {"kind": "click", "path": claude, "label": "Brave - 1 running window pinned", "target": {"control_type": "Button", "automation_id": "Appid: Brave"}},
        {"kind": "open_url", "path": brave, "url": "https://youtube.com/watch?v=a&t=40s"},
        {"kind": "click", "path": brave, "label": "Primary pane", "target": {"control_type": "Group"}},
        {"kind": "open_app", "path": claude},
        {"kind": "open_url", "path": brave, "url": "https://youtube.com/watch?v=b"},
        {"kind": "click", "path": brave, "label": "YouTube Video Player", "target": {"control_type": "Group"}},
    ]
    assert [(s["kind"], s.get("url")) for s in clean_steps(recorded)] == [
        ("open_url", "https://youtube.com/watch?v=a&t=40s"), ("open_url", "https://youtube.com/watch?v=b")]
    assert [s["kind"] for s in clean_steps([{"kind": "open_app", "path": claude}])] == ["open_app"]


def test_store_app_frames_resolve_to_the_app_inside():
    from agent.ghost.desktop.whatsapp import hosted_pid

    class User:  # ApplicationFrameHost (pid 10) hosts Calculator's CoreWindow (pid 42)
        def __init__(self, child):
            self.child = child

        def FindWindowExW(self, parent, after, cls, title):
            return self.child if cls == "Windows.UI.Core.CoreWindow" else 0

        def GetWindowThreadProcessId(self, hwnd, ref):
            ref._obj.value = 42
            return 1

    assert hosted_pid(5, 10, User(child=7)) == 42
    assert hosted_pid(5, 10, User(child=0)) == 10  # an ordinary window
    assert hosted_pid(None, 10, User(child=7)) == 10


def test_clean_steps_drops_dead_end_whatsapp_chats_and_startup_dismissals():
    from agent.ghost.desktop.session import clean_steps
    whatsapp = r"C:\Program Files\WindowsApps\WhatsApp.exe"
    brave = r"C:\Apps\brave.exe"

    # Case 1: WhatsApp opening multiple chats but doing nothing in Bob/Charlie, sending to Alice
    send_steps = [
        {"kind": "open_app", "path": whatsapp, "app": "WhatsApp.exe"},
        {"kind": "click", "path": whatsapp, "label": "Bob", "target": {"control_type": "ListItem"}},
        {"kind": "click", "path": whatsapp, "label": "Charlie", "target": {"control_type": "ListItem"}},
        {"kind": "click", "path": whatsapp, "label": "Alice", "target": {"control_type": "ListItem"}},
        {"kind": "set_text", "path": whatsapp, "value": "Hello Alice", "label": "Type a message"},
        {"kind": "click", "path": whatsapp, "label": "Send", "target": {"control_type": "Button"}},
    ]
    cleaned_send = clean_steps(send_steps)
    labels = [s.get("label") for s in cleaned_send if s.get("label")]
    values = [s.get("value") for s in cleaned_send if s.get("value")]
    assert "Bob" not in labels
    assert "Charlie" not in labels
    assert "Alice" in labels
    assert "Hello Alice" in values
    assert "Send" in labels

    # Case 2: Opening WhatsApp, clicking some chats, but doing nothing with them -> WhatsApp visit ignored
    browse_and_abandon = [
        {"kind": "open_app", "path": whatsapp, "app": "WhatsApp.exe"},
        {"kind": "click", "path": whatsapp, "label": "Bob", "target": {"control_type": "ListItem"}},
        {"kind": "click", "path": whatsapp, "label": "Alice", "target": {"control_type": "ListItem"}},
        {"kind": "open_app", "path": brave, "app": "brave.exe"},
        {"kind": "open_url", "path": brave, "url": "https://news.ycombinator.com"},
    ]
    cleaned_abandon = clean_steps(browse_and_abandon)
    # The fruitless WhatsApp visit should be completely ignored
    assert not any("whatsapp" in (s.get("path") or "").lower() for s in cleaned_abandon)
    assert any(s.get("kind") == "open_url" and s.get("url") == "https://news.ycombinator.com" for s in cleaned_abandon)

    # Case 3: Opening Brave and closing something from the very start without doing anything with it -> close ignored
    close_at_start = [
        {"kind": "open_app", "path": brave, "app": "brave.exe"},
        {"kind": "click", "path": brave, "label": "Close tab", "target": {"control_type": "Button"}},
        {"kind": "open_url", "path": brave, "url": "https://news.ycombinator.com"},
    ]
    cleaned_close = clean_steps(close_at_start)
    assert not any(s.get("label") == "Close tab" for s in cleaned_close)
    assert any(s.get("kind") == "open_url" and s.get("url") == "https://news.ycombinator.com" for s in cleaned_close)



def test_clean_steps_keeps_button_only_routines():
    from agent.ghost.desktop.session import clean_steps
    calc = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_8wekyb3d8bbwe\CalculatorApp.exe"
    steps = [{"kind": "open_app", "path": calc}] + [
        {"kind": "click", "path": calc, "label": name, "target": {"control_type": "Button", "automation_id": auto, "name": name}}
        for name, auto in (("Seven", "num7Button"), ("Plus", "plusButton"), ("Eight", "num8Button"), ("Equals", "equalButton"))]
    assert [s.get("label") for s in clean_steps(steps)] == [None, "Seven", "Plus", "Eight", "Equals"]


def test_session_append_takes_screenshot_with_every_counted_point(tmp_path):
    """Every point and step counted on the recorder must take a screenshot with it."""
    from agent.ghost.desktop.session import Session

    # Construct lightweight fake session without Windows hooks
    class DummyEvents:
        everything = False
        def __enter__(self): pass
        def __exit__(self, *a): pass
    class DummyWatch:
        def start(self): pass

    class FakeCapturer:
        def __init__(self, out_dir):
            self.out_dir = out_dir
            self.count = 0
        def capture_frame(self):
            return "mock_frame"
        def save_screenshot(self, image=None, prefix="step", region=None):
            self.count += 1
            fpath = str(self.out_dir / f"{prefix}_{self.count}.png")
            with open(fpath, "w") as f:
                f.write("fake_png")
            return fpath, "fake_img"

    # Instantiate session with hooks mocked
    s = object.__new__(Session)
    s.started = 1000.0
    s.steps = []
    s.events = DummyEvents()
    s.watch = DummyWatch()
    s.capture = None
    s.visual_memory = None
    s._visual_queue = None
    s._closed = threading.Event()
    s.capturer = FakeCapturer(tmp_path)

    # 1. Point interaction (click with point)
    pt_step = {"kind": "click", "path": r"C:\app.exe", "label": "Save", "point": [100, 200]}
    s.append(pt_step)
    assert len(s.steps) == 1
    assert "screenshot_path" in s.steps[0]
    assert s.steps[0]["screenshot_path"].endswith("step_1.png")

    # 2. Text input point
    text_step = {"kind": "set_text", "path": r"C:\app.exe", "value": "test", "target": {"name": "search"}}
    s.append(text_step)
    assert len(s.steps) == 2
    assert "screenshot_path" in s.steps[1]
    assert s.steps[1]["screenshot_path"].endswith("step_2.png")


def test_clean_steps_drops_system_host_daemons_and_launchers():
    from agent.ghost.desktop.session import clean_steps, NEVER_RECORD, SYSTEM_HOST_EXES
    assert "searchhost.exe" in NEVER_RECORD and "svchost.exe" in NEVER_RECORD
    assert "searchhost.exe" in SYSTEM_HOST_EXES

    raw_steps = [
        {"kind": "open_app", "app": "searchhost.exe", "path": r"C:\Windows\SystemApps\SearchHost.exe"},
        {"kind": "set_text", "app": "searchhost.exe", "path": r"C:\Windows\SystemApps\SearchHost.exe", "value": "spotify"},
        {"kind": "press_enter", "app": "searchhost.exe", "path": r"C:\Windows\SystemApps\SearchHost.exe"},
        {"kind": "open_app", "app": "spotify.exe", "path": r"C:\Users\test\AppData\Roaming\Spotify\Spotify.exe"},
        {"kind": "click", "app": "spotify.exe", "path": r"C:\Users\test\AppData\Roaming\Spotify\Spotify.exe", "label": "Play"},
    ]
    cleaned = clean_steps(raw_steps)
    assert len(cleaned) == 2
    assert [s["app"] for s in cleaned] == ["spotify.exe", "spotify.exe"]
    assert cleaned[0]["kind"] == "open_app"
    assert cleaned[1]["kind"] == "click"


def test_clean_steps_preserves_multiapp_shortcuts_and_actions():
    from agent.ghost.desktop.session import clean_steps
    calc = r"C:\Windows\calc.exe"
    notepad = r"C:\Windows\notepad.exe"
    raw_steps = [
        {"kind": "open_app", "app": "calc.exe", "path": calc},
        {"kind": "hotkey", "app": "calc.exe", "path": calc, "combo": "^c"},
        {"kind": "open_app", "app": "notepad.exe", "path": notepad},
        {"kind": "hotkey", "app": "notepad.exe", "path": notepad, "combo": "^v"},
    ]
    cleaned = clean_steps(raw_steps)
    assert len(cleaned) == 4
    assert [s["app"] for s in cleaned] == ["calc.exe", "calc.exe", "notepad.exe", "notepad.exe"]


def test_finish_records_concrete_apps_in_everything_mode():
    s = bare_session([
        step("open_app", path=r"C:\Apps\editor.exe"),
        step("set_text", path=r"C:\Apps\editor.exe", value="code"),
        step("open_app", path=r"C:\Apps\browser.exe"),
        step("open_url", path=r"C:\Apps\browser.exe", url="https://example.com/"),
    ])
    assert s.apps == ["*"]
    result = s.finish()
    # Finish should populate the actual apps observed rather than leaving [*]
    assert "editor.exe" in result["apps"]
    assert "browser.exe" in result["apps"]
    assert "*" not in result["apps"]



