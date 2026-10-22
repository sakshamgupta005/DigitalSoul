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
        self.clicked += 1

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
