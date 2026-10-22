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


def test_browsing_recordings_are_understood_as_navigation():
    u = understand_steps([step("open_url", url=VIDEO + "&t=95s", label="youtube.com/watch?v=abc", resume=95)])
    assert u["intent"] == f"Navigated to {VIDEO}&t=95s"
    assert u["explanations"] == ["Opened youtube.com/watch?v=abc at 1:35"]


# --- Session pieces with fakes ----------------------------------------------------------------

class FakeWatch:
    def __init__(self, seek=None):
        self.target, self.pages, self.seek, self.closed = None, queue.Queue(), seek or {}, threading.Event()

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
    watch.run()
    assert watch.pages.get_nowait() == (7, BRAVE, "https://youtube.com/watch?v=abc")
    assert watch.seek == {"https://youtube.com/watch?v=abc": 95}


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
