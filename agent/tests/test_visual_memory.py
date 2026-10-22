"""Tests for VisualMemory, ScreenCapture (dxcam/fallback), WinRT OCR, VisualAnnotator, and Representative Training Data."""
import json
import os
import shutil
import tempfile
from pathlib import Path
import pytest
pytest.importorskip("PIL")
from PIL import Image, ImageDraw

from agent.ghost.collector.ocr_winrt import OcrBoundingBox, OcrResult, OcrWord, WinRtOcrEngine
from agent.ghost.collector.screen_capture import ScreenCapture
from agent.ghost.collector.visual_annotator import VisualAnnotator
from agent.ghost.collector.training_data import RepresentativeDataGenerator, TrainingDatasetExporter, TrainingSample
from agent.ghost.collector.visual_memory import VisualMemory, VisualMemoryItem
from agent.ghost.integrations.omniroute import OmniRouteClient, OmniRouteSettings


def test_screen_capture_fallback(tmp_path):
    capturer = ScreenCapture(output_dir=tmp_path)
    img = capturer.capture_frame(region=(0, 0, 200, 100))
    assert img is not None
    assert img.size[0] >= 100
    assert img.size[1] >= 100

    path, saved_img = capturer.save_screenshot(prefix="test_shot", region=(0, 0, 150, 150))
    assert path is not None
    assert Path(path).exists()
    assert saved_img is not None


def test_ocr_data_structures():
    box = OcrBoundingBox(x=10.0, y=20.0, width=50.0, height=30.0)
    assert box.contains(15.0, 25.0)
    assert not box.contains(5.0, 25.0)
    assert not box.contains(15.0, 60.0)

    word = OcrWord(text="Login", box=box)
    res = OcrResult(text="Login Submit", lines=["Login Submit"], words=[word])
    assert res.word_at_point(15.0, 25.0) == word
    assert res.word_at_point(0, 0) is None
    assert len(res.find_words("log")) == 1


def test_winrt_ocr_on_rendered_image():
    # Render an in-memory image with text
    img = Image.new("RGB", (400, 100), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((20, 30), "DigitalSoul Test Flow", fill=(0, 0, 0))

    engine = WinRtOcrEngine()
    if engine.is_available:
        result = engine.recognize_image(img)
        assert isinstance(result.text, str)
        if result.words:
            assert len(result.words) > 0
            assert result.words[0].box.width > 0


def test_visual_annotator():
    annotator = VisualAnnotator()
    base_img = Image.new("RGB", (800, 600), color=(30, 30, 30))

    # Test visual markings (ripple, box, badge)
    marked = annotator.annotate_action(
        image=base_img,
        coords=(400.0, 300.0),
        bbox=(350.0, 280.0, 450.0, 320.0),
        action_kind="click",
        label="Send Message",
    )
    assert marked.size == (800, 600)

    # Test context crop
    crop = annotator.create_focus_crop(
        image=base_img,
        target_point=(400.0, 300.0),
        bbox=(350.0, 280.0, 450.0, 320.0),
        crop_size=(300, 200),
    )
    assert crop.size == (300, 200)


def test_visual_memory_store_with_markings_and_export(tmp_path):
    db_file = tmp_path / "test_memory.sqlite3"
    shots_dir = tmp_path / "shots"
    dataset_dir = tmp_path / "dataset"

    client = OmniRouteClient(OmniRouteSettings(base_url="http://127.0.0.1:20128/v1", model=""))
    memory = VisualMemory(db_path=db_file, screenshots_dir=shots_dir, omniroute_client=client)

    item1 = memory.capture_and_remember(
        app="WhatsApp.exe",
        window_title="WhatsApp - Saksham Gupta",
        action_kind="click",
        coords=(300.0, 450.0),
        target_name="Send Button",
        session_id="session_123",
        analyze_with_omniroute=True,
    )

    assert item1.id is not None
    assert item1.app == "WhatsApp.exe"
    assert "clicked" in item1.intent_summary.lower()
    assert item1.screenshot_path is not None
    assert item1.marked_screenshot_path is not None
    assert item1.focus_crop_path is not None
    assert Path(item1.marked_screenshot_path).exists()
    assert Path(item1.focus_crop_path).exists()

    # Search by keyword
    results = memory.search_memory("Saksham")
    assert len(results) >= 1
    assert results[0].app == "WhatsApp.exe"

    # Export dataset
    count = memory.export_as_training_dataset(output_dir=dataset_dir)
    assert count == 1
    samples_file = dataset_dir / "samples.jsonl"
    assert samples_file.exists()
    with open(samples_file, "r", encoding="utf-8") as f:
        data = json.loads(f.readline())
        assert data["app"] == "WhatsApp.exe"
        assert data["action_type"] == "click"
        assert data["target_label"] == "Send Button"


def test_representative_training_data_generator(tmp_path):
    gen = RepresentativeDataGenerator(output_dir=tmp_path)
    samples = gen.generate_whatsapp_flow()

    assert len(samples) == 4
    for s in samples:
        assert s.app == "WhatsApp.exe"
        assert Path(s.raw_screenshot_path).exists()
        assert Path(s.marked_screenshot_path).exists()
        assert Path(s.focus_crop_path).exists()
        assert len(s.coords_norm) == 2
        assert len(s.bbox_norm) == 4
        # Normalized coordinates should be between 0 and 1
        assert 0.0 <= s.coords_norm[0] <= 1.0
        assert 0.0 <= s.coords_norm[1] <= 1.0

    samples_file = tmp_path / "samples.jsonl"
    assert samples_file.exists()
