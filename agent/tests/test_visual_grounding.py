import pytest
from pathlib import Path
from PIL import Image, ImageDraw
from agent.ghost.desktop.visual_grounding import VisualGrounder
from agent.ghost.desktop.steps import StepRunner


def test_visual_grounder_directory_structure(tmp_path):
    grounder = VisualGrounder(workflow_name="Send Invoice WhatsApp")
    assert "Send_Invoice_WhatsApp" in str(grounder.workflow_dir)
    assert grounder.workflow_dir.is_dir()


def test_visual_grounder_find_target_mock(monkeypatch, tmp_path):
    grounder = VisualGrounder(workflow_name="Unit_Test_Workflow")
    grounder.workflow_dir = tmp_path / "screenshots"
    grounder.workflow_dir.mkdir(parents=True, exist_ok=True)
    grounder.capturer.output_dir = grounder.workflow_dir

    # Create synthetic screen image with drawn text "Submit Order"
    img = Image.new("RGB", (800, 600), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Draw simulated button box
    draw.rectangle([200, 300, 350, 340], fill=(50, 150, 250))

    class MockOcrWord:
        def __init__(self, text, x, y, w, h):
            self.text = text
            self.box = type("Box", (), {"x": x, "y": y, "width": w, "height": h})()
        def to_dict(self):
            return {"text": self.text, "box": {"x": self.box.x, "y": self.box.y, "width": self.box.width, "height": self.box.height}}

    class MockOcrLine:
        def __init__(self, text, x, y, w, h):
            self.text = text
            self.box = type("Box", (), {"x": x, "y": y, "width": w, "height": h})()

    class MockOcrResult:
        def __init__(self):
            self.lines = [MockOcrLine("Submit Order", 210, 305, 120, 25)]
            self.words = [MockOcrWord("Submit", 210, 305, 55, 25), MockOcrWord("Order", 270, 305, 60, 25)]

    monkeypatch.setattr(grounder.capturer, "capture_frame", lambda region=None: img)
    monkeypatch.setattr(grounder.ocr_engine, "recognize_image", lambda image: MockOcrResult())

    pt = grounder.find_target_point("Submit Order", save_debug=True, step_index=1, action_kind="click")
    assert pt is not None
    # Center of [210, 305, 330, 330] -> approx (270, 317)
    assert 250 <= pt[0] <= 290
    assert 305 <= pt[1] <= 330

    # Ensure debug screenshot with highlights was saved to the workflow subfolder
    saved_files = list(grounder.workflow_dir.glob("step_001_click_*.png"))
    assert len(saved_files) == 1


def test_step_runner_visual_grounder_init():
    runner = StepRunner(native=None, workflow_name="Billing Task")
    assert runner.workflow_name == "Billing Task"
    assert runner.grounder is not None
    assert "Billing_Task" in str(runner.grounder.workflow_dir)
