import pytest

from agent.ghost.desktop import steps as replay
from agent.ghost.desktop.steps import StepRunner, Stopped, digest, keys, validate

APP = r"c:\program files\brave\brave.exe"


class Info:
    def __init__(self, control_type, name="", automation_id="", process_id=1):
        self.control_type, self.name, self.automation_id, self.process_id = control_type, name, automation_id, process_id


class Control:
    def __init__(self, control_type, name="", automation_id=""):
        self.element_info = Info(control_type, name, automation_id)
        self.clicked = 0
        self.text = None

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def parent(self):
        return None

    def click_input(self):
        raise AssertionError("replay must never move the user's mouse")

    def set_focus(self):
        pass

    def set_edit_text(self, value):
        self.text = value


class Window(Control):
    def __init__(self, children):
        super().__init__("Window")
        self.children = children

    def descendants(self, control_type, title=None):
        return [c for c in self.children if c.element_info.control_type == control_type
                and (title is None or c.element_info.name == title)]


class FakeDesktop:
    def __init__(self, window, running=True):
        self.window, self.running = window, running
        self.launched, self.typed, self.stop = [], [], False
        self.focus_target = None

    def stop_pressed(self):
        return self.stop

    def windows(self, path):
        return [self.window] if self.running and path == APP else []

    def foreground(self):
        return (self.window, APP) if self.running else (None, None)

    def focus(self, window):
        pass

    def launch(self, step):
        self.launched.append(step["path"])
        self.running = True

    def focused(self):
        return self.focus_target

    def send_keys(self, text, **kwargs):
        self.typed.append(text)

    def act(self, control):
        control.clicked += 1


def spec(control_type, name="", automation_id=""):
    return {"control_type": control_type, "name": name, "automation_id": automation_id, "ancestors": []}


def runner(desktop):
    return StepRunner(desktop, sleep=lambda _: None, timeout=0)


def test_replays_open_click_type_and_enter_in_order():
    address = Control("Edit", "Address and search bar", "omnibox")
    tab = Control("TabItem", "New Tab")
    desktop = FakeDesktop(Window([address, tab]), running=False)
    count = runner(desktop).run([
        {"kind": "open_app", "app": "brave.exe", "path": APP, "at": 0},
        {"kind": "click", "app": "brave.exe", "path": APP, "label": "New Tab", "target": spec("TabItem", "New Tab"), "at": 1},
        {"kind": "set_text", "app": "brave.exe", "path": APP, "target": spec("Edit", "", "omnibox"), "value": "chatgpt.com", "at": 2},
        {"kind": "press_enter", "app": "brave.exe", "path": APP, "at": 3},
    ])
    assert count == 4
    assert desktop.launched == [APP]
    assert tab.clicked == 1
    assert address.text == "chatgpt.com"
    assert desktop.typed == ["{ENTER}"]


def test_web_field_without_id_uses_the_focused_field_and_types():
    editor = Control("Document")
    desktop = FakeDesktop(Window([Control("Document"), Control("Document")]))
    desktop.focus_target = editor
    runner(desktop).set_text({"kind": "set_text", "path": APP, "target": spec("Document"), "value": "hi (there)"})
    assert desktop.typed == ["^a{BACKSPACE}hi {(}there{)}"]


def test_ambiguous_or_missing_control_stops_instead_of_guessing():
    desktop = FakeDesktop(Window([Control("Button", "OK"), Control("Button", "OK")]))
    with pytest.raises(ValueError, match="Could not find"):
        runner(desktop).click({"kind": "click", "path": APP, "label": "OK", "target": spec("Button", "OK")})


def test_f8_stops_before_the_next_step():
    desktop = FakeDesktop(Window([]))
    desktop.stop = True
    with pytest.raises(Stopped):
        runner(desktop).run([{"kind": "press_enter", "path": APP}])
    assert desktop.typed == []


def test_validate_rejects_unknown_actions_and_digest_tracks_changes():
    good = [{"kind": "press_enter", "path": APP}]
    validate(good)
    for bad in ([], [{"kind": "shell", "path": APP}], [{"kind": "set_text", "path": APP}], [{"kind": "press_enter", "path": "brave.exe"}]):
        with pytest.raises(ValueError):
            validate(bad)
    changed = [{"kind": "press_enter", "path": APP, "at": 1}]
    assert digest(good) == digest([dict(good[0])]) != digest(changed)


def test_keys_escapes_send_keys_syntax():
    assert keys("50% +1 ^x ~{a}[b]") == "50{%} {+}1 {^}x {~}{{}a{}}{[}b{]}"


def test_old_recording_with_placeholder_and_stray_focus_text_still_replays():
    # Shape of a real Brave recording: placeholder echoes before the real omnibox search.
    address = Control("Edit", "Address and search bar", "view_1012")
    new_tab = Control("Button", "New Tab")
    chat = Control("Edit", "Ask ChatGPT")
    desktop = FakeDesktop(Window([address, new_tab, chat]))
    run = runner(desktop)
    run.run([
        {"kind": "open_app", "path": APP, "at": 1.47},
        {"kind": "set_text", "path": APP, "target": spec("Edit"), "value": "Ask ChatGPT", "at": 1.53},
        {"kind": "click", "path": APP, "label": "New Tab", "target": spec("Button", "New Tab"), "at": 2.59},
        {"kind": "set_text", "path": APP, "target": spec("Edit", "", "gone"), "value": "Ask ChatGPT", "at": 2.61},
        {"kind": "set_text", "path": APP, "target": spec("Edit", "", "view_1012"), "value": "shopify", "at": 5.81},
        {"kind": "press_enter", "path": APP, "at": 6.8},
    ])
    assert chat.text is None and address.text == "shopify"
    assert new_tab.clicked == 1 and desktop.typed == ["{ENTER}"]
    assert run.skipped == 2


def test_missing_field_before_enter_still_stops():
    desktop = FakeDesktop(Window([]))
    with pytest.raises(ValueError, match="text field"):
        runner(desktop).run([{"kind": "set_text", "path": APP, "target": spec("Edit", "", "x"), "value": "hi"},
                             {"kind": "press_enter", "path": APP}])


def test_stable_name_ignores_unread_counts_and_previews():
    from agent.ghost.desktop.steps import stable_name
    assert stable_name("(33) WhatsApp") == "whatsapp"
    assert stable_name("Vansh Arora, birthday postponed, 8:05 PM") == "vansh arora"
    assert stable_name("New Tab") == "new tab"


def test_click_falls_back_to_stable_name_when_preview_text_changed():
    contact = Control("DataItem", "Vansh Arora, see you soon, 9:10 PM")
    other = Control("DataItem", "Saksham, ok, 9:00 PM")
    desktop = FakeDesktop(Window([contact, other]))
    runner(desktop).click({"kind": "click", "path": APP, "label": "Vansh", "target": spec("DataItem", "Vansh Arora, birthday postponed, 8:05 PM")})
    assert contact.clicked == 1 and other.clicked == 0


def test_clean_control_name_strips_metadata_and_internal_ids():
    from agent.ghost.desktop.session import clean_control_name
    c = Control("ListItem", "Downloads, 10/22/2026 2:15 PM, File folder")
    assert clean_control_name(c, c.element_info.name, "ListItem") == "Downloads"

    c_file = Control("DataItem", "photo.png, 10/15/2026 10:30 AM, PNG image, 2.4 MB")
    assert clean_control_name(c_file, c_file.element_info.name, "DataItem") == "photo.png"

    c_unread = Control("Text", "(5) Team Chat")
    assert clean_control_name(c_unread, c_unread.element_info.name, "Text") == "Team Chat"


def test_explorer_windows_filters_shell_tray_and_desktop():
    from agent.ghost.desktop.steps import Desktop, SHELL_CLASSES
    taskbar = Control("Window")
    taskbar.element_info.class_name = "Shell_TrayWnd"
    taskbar.element_info.process_id = 100

    explorer_win = Control("Window", name="Downloads")
    explorer_win.element_info.class_name = "CabinetWClass"
    explorer_win.element_info.process_id = 100

    d = Desktop.__new__(Desktop)
    class FakeUia:
        def windows(self):
            return [taskbar, explorer_win]
    d.desktop = FakeUia()

    import agent.ghost.desktop.steps as steps_mod
    orig_exec = steps_mod.executable
    try:
        steps_mod.executable = lambda pid: r"c:\windows\explorer.exe" if pid == 100 else None
        res = d.windows(r"c:\windows\explorer.exe")
        assert res == [explorer_win]
    finally:
        steps_mod.executable = orig_exec



def test_open_url_steps_only_accept_web_addresses_in_browsers():
    good = [{"kind": "open_url", "path": APP, "url": "https://www.youtube.com/watch?v=abc&t=95s"}]
    validate(good)
    for url in ("file:///C:/secret.txt", "javascript:alert(1)", "https://bad url"):
        with pytest.raises(ValueError):
            validate([{"kind": "open_url", "path": APP, "url": url}])
    with pytest.raises(ValueError):
        validate([{"kind": "open_url", "path": r"C:\Windows\notepad.exe", "url": "https://x.com"}])


def test_replay_opens_the_exact_page_without_touching_the_mouse():
    opened = []
    desktop = FakeDesktop(Window([]))
    desktop.open_url = lambda path, url: opened.append((path, url))
    runner(desktop).run([{"kind": "open_url", "path": APP, "url": "https://youtube.com/watch?v=abc&t=95s"}])
    assert opened == [(APP, "https://youtube.com/watch?v=abc&t=95s")]


def test_clicks_go_through_the_ghost_never_the_pointer():
    acted = []
    tab = Control("TabItem", "New Tab")
    desktop = FakeDesktop(Window([tab]))
    desktop.act = acted.append
    runner(desktop).click({"kind": "click", "path": APP, "label": "New Tab", "target": spec("TabItem", "New Tab")})
    assert acted == [tab]  # Control.click_input would have raised


def test_stale_controls_are_retried_instead_of_failing_the_replay():
    class Flaky(Window):
        calls = 0

        def descendants(self, control_type, title=None):
            self.calls += 1
            if self.calls == 1:
                raise OSError("element not available")  # e.g. a COMError from a rebuilt web tree
            return super().descendants(control_type, title)
    tab = Control("TabItem", "New Tab")
    desktop = FakeDesktop(Flaky([tab]))
    clock = iter(range(100))
    StepRunner(desktop, sleep=lambda _: None, clock=lambda: next(clock), timeout=5).click(
        {"kind": "click", "path": APP, "label": "New Tab", "target": spec("TabItem", "New Tab")})
    assert tab.clicked == 1


def test_unexpected_failures_name_the_step_but_not_the_ui_text():
    class Broken(FakeDesktop):
        def act(self, control):
            raise RuntimeError("private chat preview text")
    desktop = Broken(Window([Control("Button", "Send")]))
    with pytest.raises(ValueError) as error:
        runner(desktop).run([{"kind": "click", "path": APP, "label": "Send", "target": spec("Button", "Send")}])
    assert str(error.value) == "click 'Send' failed (RuntimeError)"


def test_pacing_stays_calm_but_bounded():
    pauses = []
    desktop = FakeDesktop(Window([]))
    StepRunner(desktop, sleep=pauses.append, timeout=0).run([
        {"kind": "press_enter", "path": APP, "at": 0},
        {"kind": "press_enter", "path": APP, "at": 0.1},
        {"kind": "press_enter", "path": APP, "at": 30},
        {"kind": "press_enter", "path": APP},
    ])
    assert [p for p in pauses if p != 0.2] == [0.5, 1.6, 0.5]
