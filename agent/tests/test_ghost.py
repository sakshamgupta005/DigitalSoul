import ctypes
import math

import pytest

from agent.ghost.desktop import ghost
from agent.ghost.desktop.ghost import Ghost, act
from agent.ghost.desktop.whatsapp_send import Refused, WhatsAppSend

# ---- act(): UI Automation only, in a fixed fallback order ----


class Element:
    def __init__(self, available):
        self.available = available

    def GetCurrentPropertyValue(self, prop):
        return prop in self.available


class Pattern:
    def __init__(self, log, name, fails=False):
        self.log, self.name, self.fails = log, name, fails
        self.CurrentExpandCollapseState = 0

    def __getattr__(self, method):
        def call():
            if self.fails:
                raise ValueError("NULL COM pointer access")
            self.log.append(f"{self.name}.{method}")
        return call


class Target:
    def __init__(self, patterns=(), failing=(), legacy=True, control_type="Button", focusable=True):
        self.log = []
        ids = ghost._PATTERN_AVAILABLE
        self.element_info = type("Info", (), {"element": Element({ids[p] for p in patterns}), "control_type": control_type})()
        self.iface_invoke = Pattern(self.log, "Invoke", "Invoke" in failing)
        self.iface_selection_item = Pattern(self.log, "SelectionItem", "SelectionItem" in failing)
        self.iface_toggle = Pattern(self.log, "Toggle", "Toggle" in failing)
        self.iface_expand_collapse = Pattern(self.log, "ExpandCollapse", "ExpandCollapse" in failing)
        self.iface_legacy_iaccessible = Pattern(self.log, "Legacy", not legacy)
        self.focusable = focusable

    def set_focus(self):
        self.log.append("focus")

    def has_keyboard_focus(self):
        return self.focusable

    def click_input(self):
        raise AssertionError("the user's mouse must never move")


def test_act_prefers_invoke_then_falls_through_patterns_in_order():
    assert act(Target(["Invoke", "Toggle"])) == "Invoke"
    target = Target(["Invoke", "SelectionItem"], failing=["Invoke"])  # Chromium's NULL Invoke
    assert act(target) == "SelectionItem" and target.log == ["SelectionItem.Select"]
    assert act(Target(["Toggle"])) == "Toggle"
    assert act(Target(["ExpandCollapse"])) == "ExpandCollapse"


def test_act_collapses_an_expanded_control_like_a_click_would():
    target = Target(["ExpandCollapse"])
    target.iface_expand_collapse.CurrentExpandCollapseState = 1
    act(target)
    assert target.log == ["ExpandCollapse.Collapse"]


def test_act_uses_the_accessible_default_action_when_no_pattern_works():
    target = Target(["Invoke"], failing=["Invoke"])
    assert act(target) == "LegacyIAccessible" and target.log == ["Legacy.DoDefaultAction"]


def test_act_last_resort_is_a_key_press_only_when_focus_landed_on_the_control():
    keys = []
    target = Target(legacy=False)
    assert act(target, send_keys=keys.append) == "Keyboard" and keys == ["{SPACE}"]
    with pytest.raises(ValueError, match="without moving your mouse"):
        act(Target(legacy=False, focusable=False), send_keys=keys.append)
    with pytest.raises(ValueError):
        act(Target(legacy=False, control_type="Pane"), send_keys=keys.append)
    assert keys == ["{SPACE}"]


# ---- Ghost flight: soft arc through start and goal, bounded duration, never the cursor ----


class FakeUser:
    def __init__(self, rect=(-4000, -4000, -3928, -3928)):
        self.rect, self.moves = list(rect), []

    def FindWindowW(self, cls, title):
        return 7 if title == ghost.TITLE else None

    def IsWindow(self, hwnd):
        return True

    def GetWindowRect(self, hwnd, ref):
        ref._obj.left, ref._obj.top, ref._obj.right, ref._obj.bottom = self.rect
        return 1

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.moves.append((x, y, cx, cy))
        if cx:
            self.rect = [x, y, x + cx, y + cy]
        else:
            w, h = self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]
            self.rect = [x, y, x + w, y + h]

    def SetCursorPos(self, *args):
        raise AssertionError("the user's mouse must never move")


class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept += seconds


def flown(rect, x, y):
    user, clock = FakeUser(rect), Clock()
    g = Ghost(user=user, sleep=clock.sleep, clock=clock)
    g.fly_to(x, y)
    return user, clock


def test_arc_and_easing_pass_through_start_and_goal():
    start, goal = (0, 0), (300, 400)
    control = ghost.bend(start, goal, 1)
    assert ghost.point(start, control, goal, ghost.ease(0)) == start
    end = ghost.point(start, control, goal, ghost.ease(1))
    assert math.isclose(end[0], 300) and math.isclose(end[1], 400)
    assert math.hypot(control[0] - 150, control[1] - 200) == pytest.approx(110)  # 0.22 * distance, capped at 120


def test_flight_lands_above_left_of_the_target_without_covering_it():
    user, clock = flown((100, 100, 172, 172), 900, 700)
    x, y, _, _ = user.moves[-1]
    centre = (x + 36, y + 36)
    assert centre == (pytest.approx(900 - 0.15 * 72, abs=1), pytest.approx(700 - 0.45 * 72, abs=1))
    steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(user.moves, user.moves[1:])]
    assert max(steps) < 40  # a glide, never a jump


@pytest.mark.parametrize("rect,target,low,high", [
    ((100, 100, 172, 172), (130, 140), 0.45, 0.45 + 0.12 + 0.02),        # short hop: at least the shortest flight
    ((0, 0, 72, 72), (3000, 2000), 0.95, 0.95 + 0.12 + 0.02),             # across the screen: capped
    ((-4000, -4000, -3928, -3928), (500, 500), 0.45, 0.45 + 0.12 + 0.02),  # first appearance drifts in nearby
])
def test_flight_duration_is_bounded(rect, target, low, high):
    user, clock = flown(rect, *target)
    assert low <= clock.slept <= high
    first = user.moves[0]
    assert first[0] > -3000  # never streaks in from the parking spot


def test_negative_monitor_coordinates_are_not_mistaken_for_parked():
    user, clock = flown((-1500, 200, -1428, 272), -1000, 400)
    x, y, _, _ = user.moves[0]
    assert x < -1400  # starts where it is, on the left monitor
    assert clock.slept > 0.5  # 0.45 s minimum flight plus the hover


def test_pulse_swells_then_settles_and_park_hides_the_ghost():
    user, clock = FakeUser((100, 100, 172, 172)), Clock()
    g = Ghost(user=user, sleep=clock.sleep, clock=clock)
    g.pulse()
    sizes = [cx for _, _, cx, _ in user.moves]
    assert max(sizes) == pytest.approx(72 * 1.9, abs=2) and sizes[-1] == 72
    assert user.rect == [100, 100, 172, 172]
    g.park()
    assert user.moves[-1][:2] == ghost.PARKED


def test_missing_overlay_is_harmless():
    user = FakeUser()
    user.FindWindowW = lambda cls, title: None
    g = Ghost(user=user)
    g.fly_to(10, 10)
    g.pulse()
    g.park()
    assert user.moves == []


def test_empty_control_rectangle_is_not_flown_to():
    user, clock = FakeUser((100, 100, 172, 172)), Clock()
    rect = type("R", (), {"left": 0, "top": 0, "right": 0, "bottom": 0})()
    Ghost(user=user, sleep=clock.sleep, clock=clock).to_control(type("C", (), {"rectangle": lambda self: rect})())
    assert user.moves == []


# ---- WhatsApp send: identity proven by the composer, drafts never overwritten ----

EXE = r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.0.0.0_x64__cv1g1gvanyjgm\WhatsApp.exe"


class Field:
    def __init__(self, control_type, name, value=""):
        self.element_info = type("Info", (), {"name": name, "control_type": control_type, "runtime_id": (id(self),)})()
        self.value, self.focused = value, False

    def is_visible(self):
        return True

    def rectangle(self):
        return type("R", (), {"left": 0, "top": 0, "right": 10, "bottom": 10})()

    def set_focus(self):
        self.focused = True

    def has_keyboard_focus(self):
        return self.focused

    def set_edit_text(self, text):
        self.value = text

    def get_value(self):
        return self.value

    def click_input(self):
        raise AssertionError("the user's mouse must never move")


class App:
    def __init__(self, controls):
        self.controls = controls

    def descendants(self, control_type):
        return [c for c in self.controls if c.element_info.control_type == control_type]


class WhatsAppNative:
    def __init__(self, app):
        self.app = app

    def open(self, recipe):
        pass

    def active(self, path):
        return self.app, 1, path


class FakeGhost:
    def __init__(self):
        self.visits, self.parked = 0, 0

    def to_control(self, control):
        self.visits += 1

    def pulse(self):
        pass

    def park(self):
        self.parked += 1


def recipe(recipient="Alice"):
    return {"format": "digitalsoul.whatsapp-send.v1", "recipient": recipient, "search": "", "message": "hi",
            "executable": EXE, "app_id": None, "send": {"kind": "enter"}}


def sender(app, press, ghost_):
    return WhatsAppSend(native=WhatsAppNative(app), ghost=ghost_, press=press, sleep=lambda _: None, timeout=0)


def test_opens_the_matching_chat_and_types_after_proving_identity():
    search, row = Field("Edit", "Search input textbox"), Field("ListItem", "Alice, see you, 9:10 PM")
    app = App([search, row, Field("ListItem", "Bob, ok")])
    pressed, g = [], FakeGhost()
    composer = Field("Edit", "Type a message to Alice")

    def press(control):
        pressed.append(control)
        app.controls.append(composer)
    sender(app, press, g).prepare(recipe(), "hello there")
    assert pressed == [row] and search.value == "Alice" and composer.value == "hello there"
    assert g.visits >= 3 and g.parked == 0  # hovers at the composer until the send is approved


def test_refuses_to_type_when_the_composer_belongs_to_another_chat():
    row = Field("ListItem", "Alice")
    app = App([Field("Edit", "Search"), row])
    wrong = Field("Edit", "Type a message to Bob")
    g = FakeGhost()
    with pytest.raises(ValueError, match="message box for 'Alice'"):
        sender(app, lambda control: app.controls.append(wrong), g).prepare(recipe(), "hello")
    assert wrong.value == "" and g.parked == 1


def test_refuses_to_overwrite_an_existing_draft():
    composer = Field("Edit", "Type a message to Alice", value="half-written note")
    g = FakeGhost()
    with pytest.raises(Refused, match="draft"):
        sender(App([composer]), None, g).prepare(recipe(), "hello")
    assert composer.value == "half-written note" and g.parked == 1


def test_refuses_to_guess_between_similar_chats():
    app = App([Field("Edit", "Search"), Field("ListItem", "Alice Brown"), Field("ListItem", "Alice Chen")])
    pressed = []
    with pytest.raises(Refused, match="More than one chat"):
        sender(app, pressed.append, FakeGhost()).prepare(recipe(), "hello")
    assert pressed == []


def test_default_press_is_ui_automation_not_the_mouse(monkeypatch):
    acted = []
    monkeypatch.setattr(ghost, "act", acted.append)
    row = Field("ListItem", "Alice")
    WhatsAppSend(ghost=None)._press(row)
    assert acted == [row]


def test_whatsapp_composer_supports_webview2_window_text_and_unlabelled_field():
    # In WhatsApp Desktop WebView2, get_value() raises COMError, but window_text() returns the text.
    class WebView2Composer(Field):
        def get_value(self):
            raise RuntimeError("ValuePattern not supported on WebView2 contenteditable")

        def window_text(self):
            return self.value

    # WhatsApp composer named just "Type a message" without "to <chat>"
    composer = WebView2Composer("Edit", "Type a message")
    app = App([composer])
    g = FakeGhost()
    s = sender(app, None, g)
    s.chat = "Alice"
    s.prepare(recipe(), "hello there")
    assert composer.value == "hello there"
    sent = []
    import pytest
    pytest.importorskip("pywinauto")
    import pywinauto.keyboard
    monkeypatch_send = lambda key: sent.append(key)
    old_send = getattr(pywinauto.keyboard, "send_keys", None)
    pywinauto.keyboard.send_keys = monkeypatch_send
    try:
        s.send(recipe(), "hello there")
    finally:
        pywinauto.keyboard.send_keys = old_send
    assert sent == ["{ENTER}"]


def test_chat_name_strips_whatsapp_time_date_preview():
    from agent.ghost.desktop.flow import chat_name
    assert chat_name("Vansh 12:47 am Video") == "Vansh"
    assert chat_name("Vansh 12:47 am") == "Vansh"
    assert chat_name("VANSH ARORA ID CARD Wednesday You : Sticker Muted chat") == "VANSH ARORA ID CARD"
    assert chat_name("Alice Yesterday Hello") == "Alice"
    assert chat_name("Bob 10/22/2026 Hi") == "Bob"


def test_exact_chat_match_selected_despite_duplicate_or_similar_rows():
    # If a chat list contains duplicate UIA rows for the exact contact plus another contact starting with the same name
    row1 = Field("ListItem", "Alice 10:15 am Message")
    row2 = Field("ListItem", "Alice 10:15 am")
    other = Field("ListItem", "Alice Cooper 9:00 am")
    app = App([Field("Edit", "Search"), row1, row2, other])
    pressed, g = [], FakeGhost()
    composer = Field("Edit", "Type a message to Alice")

    def press(control):
        pressed.append(control)
        app.controls.append(composer)

    sender(app, press, g).prepare(recipe(recipient="Alice"), "hi")
    assert pressed == [row1] and composer.value == "hi"
