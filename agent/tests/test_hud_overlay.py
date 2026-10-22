import pytest
from agent.ghost.desktop.steps import StepRunner

def spec(control_type, name="", automation_id=""):
    return {"control_type": control_type, "name": name, "automation_id": automation_id, "ancestors": []}

APP = r"c:\program files\brave\brave.exe"


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class ControlWithRect:
    def __init__(self, control_type, name="", rect=(100, 200, 300, 250)):
        self.element_info = type("Info", (), {
            "control_type": control_type, "name": name, "automation_id": "id1", "process_id": 1
        })()
        self._rect = Rect(*rect)
        self.clicked = 0
        self.text = None

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def parent(self):
        return None

    def rectangle(self):
        return self._rect

    def set_focus(self):
        pass

    def set_edit_text(self, value):
        self.text = value


class Window:
    def __init__(self, children):
        self.element_info = type("Info", (), {"control_type": "Window", "name": "Brave", "process_id": 1})()
        self.children = children
        self._rect = Rect(50, 50, 1200, 800)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def rectangle(self):
        return self._rect

    def descendants(self, control_type, title=None):
        return [c for c in self.children if c.element_info.control_type == control_type
                and (title is None or c.element_info.name == title)]


class FakeDesktop:
    def __init__(self, window):
        self.window = window
        self.launched, self.typed = [], []

    def stop_pressed(self):
        return False

    def windows(self, path):
        return [self.window]

    def foreground(self):
        return (self.window, APP)

    def focus(self, window):
        pass

    def launch(self, step):
        self.launched.append(step["path"])

    def focused(self):
        return None

    def send_keys(self, text, **kwargs):
        self.typed.append(text)

    def act(self, control):
        control.clicked += 1

    def open_url(self, path, url):
        pass


def test_step_runner_emits_hud_targeting_and_acting_events():
    button = ControlWithRect("Button", "Submit Button", rect=(200, 300, 350, 340))
    field = ControlWithRect("Edit", "Product Title", rect=(200, 150, 500, 190))
    window = Window([button, field])
    desktop = FakeDesktop(window)

    events = []
    runner = StepRunner(desktop, sleep=lambda _: None, timeout=0, event_sink=events.append)

    steps = [
        {"kind": "open_app", "app": "brave.exe", "path": APP, "at": 0},
        {"kind": "set_text", "app": "brave.exe", "path": APP, "label": "Product Title", "target": spec("Edit", "Product Title"), "value": "Sony Headphones", "at": 1},
        {"kind": "click", "app": "brave.exe", "path": APP, "label": "Submit Button", "target": spec("Button", "Submit Button"), "at": 2},
        {"kind": "press_enter", "app": "brave.exe", "path": APP, "at": 3},
    ]

    count = runner.run(steps)
    assert count == 4

    # Verify HUD targeting events were emitted
    targeting_events = [e for e in events if e.get("event") == "hud_targeting"]
    assert len(targeting_events) >= 3

    # Check button targeting has valid physical bounding rect
    btn_targeting = [e for e in targeting_events if e.get("label") == "Submit Button" and e.get("rect")]
    assert len(btn_targeting) == 1
    rect = btn_targeting[0]["rect"]
    assert rect["left"] == 200
    assert rect["top"] == 300
    assert rect["width"] == 150
    assert rect["height"] == 40
    assert btn_targeting[0]["step_index"] == 3
    assert btn_targeting[0]["total_steps"] == 4

    # Check acting events
    acting_events = [e for e in events if e.get("event") == "hud_acting"]
    assert any(e.get("action") == "click" and e.get("label") == "Submit Button" for e in acting_events)
    assert any(e.get("action") == "set_text" and "Sony" in e.get("status", "") for e in acting_events)
    assert any(e.get("action") == "press_enter" for e in acting_events)


def test_open_url_emits_truthful_browser_level_status():
    window = Window([])
    desktop = FakeDesktop(window)

    events = []
    runner = StepRunner(desktop, sleep=lambda _: None, timeout=0, event_sink=events.append)

    steps = [
        {"kind": "open_url", "path": APP, "url": "https://admin.shopify.com/products", "at": 0}
    ]
    runner.run(steps)

    url_targeting = [e for e in events if e.get("event") == "hud_targeting" and e.get("is_browser_level")]
    assert len(url_targeting) == 1
    assert "https://admin.shopify.com" in url_targeting[0]["label"]
