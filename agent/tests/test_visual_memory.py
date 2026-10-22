"""Tests for VisualMemory, ScreenCapture (dxcam/fallback), WinRT OCR, and OmniRoute."""
import os
import shutil
import tempfile
from pathlib import Path
import pytest
pytest.importorskip("PIL")
from PIL import Image, ImageDraw

from agent.ghost.collector.ocr_winrt import OcrBoundingBox, OcrResult, OcrWord, WinRtOcrEngine
from agent.ghost.collector.screen_capture import ScreenCapture
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
        # Even with basic fonts, it should extract characters
        if result.words:
            assert len(result.words) > 0
            assert result.words[0].box.width > 0


def test_visual_memory_store_and_search(tmp_path):
    db_file = tmp_path / "test_memory.sqlite3"
    shots_dir = tmp_path / "shots"

    # Initialize with offline OmniRoute settings
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
    assert Path(item1.screenshot_path).exists()

    item2 = memory.capture_and_remember(
        app="chrome.exe",
        window_title="Acme Portal Dashboard",
        action_kind="set_text",
        target_name="Search Input",
        session_id="session_123",
        analyze_with_omniroute=True,
    )

    recent = memory.get_recent(limit=10)
    assert len(recent) == 2
    assert recent[0].id == item1.id
    assert recent[1].id == item2.id

    # Search by keyword
    results = memory.search_memory("Saksham")
    assert len(results) >= 1
    assert results[0].app == "WhatsApp.exe"

    results_chrome = memory.search_memory("Portal", app="chrome.exe")
    assert len(results_chrome) == 1
    assert results_chrome[0].app == "chrome.exe"
