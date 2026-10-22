"""Test cursor capabilities for browser and desktop replay."""
import pytest
from unittest.mock import MagicMock
from agent.ghost.desktop.session import clean_steps
from agent.ghost.desktop.steps import StepRunner


def test_clean_steps_preserves_browser_coordinate_clicks():
    brave = r"C:\Apps\brave.exe"
    steps = [
        {"kind": "open_app", "path": brave},
        {"kind": "open_url", "path": brave, "url": "https://example.com"},
        # Clicks with coordinates inside the browser must be preserved
        {"kind": "click", "path": brave, "label": "Login button", "point": [450, 320], "relative_point": [250, 180]},
        {"kind": "set_text", "path": brave, "label": "Username", "value": "testuser", "point": [450, 360]},
        # Container noise without coordinates is still dropped
        {"kind": "click", "path": brave, "label": "Container Pane", "target": {"control_type": "Pane"}},
    ]
    cleaned = clean_steps(steps)
    assert len(cleaned) == 4
    kinds = [s["kind"] for s in cleaned]
    assert kinds == ["open_app", "open_url", "click", "set_text"]
    assert cleaned[2]["point"] == [450, 320]
    assert cleaned[2]["relative_point"] == [250, 180]


def test_step_runner_falls_back_to_cursor_click_when_uia_fails():
    native = MagicMock()
    mock_window = MagicMock()
    mock_window.rectangle.return_value = MagicMock(left=100, top=50, right=900, bottom=700)
    native.windows.return_value = [mock_window]
    native.foreground.return_value = (mock_window, r"C:\Apps\chrome.exe")
    native.stop_pressed.return_value = False

    runner = StepRunner(native, sleep=lambda s: None, timeout=0.1)
    runner._find = MagicMock(return_value=None)  # UIA cannot find the web element

    step = {
        "kind": "click",
        "path": r"C:\Apps\chrome.exe",
        "label": "Web Submit",
        "point": [300, 250],
        "relative_point": [200, 200],
    }
    runner.click(step)

    # Relative coordinates: window.left (100) + rel_x (200) = 300, window.top (50) + rel_y (200) = 250
    native.cursor_click.assert_called_once_with(300, 250)


def test_step_runner_falls_back_to_cursor_text_input():
    native = MagicMock()
    mock_window = MagicMock()
    mock_window.rectangle.return_value = MagicMock(left=100, top=50, right=900, bottom=700)
    native.windows.return_value = [mock_window]
    native.foreground.return_value = (mock_window, r"C:\Apps\chrome.exe")
    native.stop_pressed.return_value = False

    runner = StepRunner(native, sleep=lambda s: None, timeout=0.1)
    runner._find = MagicMock(return_value=None)  # UIA cannot find the edit field

    step = {
        "kind": "set_text",
        "path": r"C:\Apps\chrome.exe",
        "label": "Search Box",
        "value": "DigitalSoul docs",
        "relative_point": [150, 100],
    }
    runner.set_text(step, committed=True)

    # Clicks at coordinates (100+150=250, 50+100=150) to focus, then sends keys
    native.cursor_click.assert_called_once_with(250, 150)
    native.send_keys.assert_called_once()
