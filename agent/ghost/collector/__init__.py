"""Collector package for DigitalSoul desktop and extension telemetry."""

from agent.ghost.collector.window_tracker import (
    DesktopCollector,
    is_workstation_locked,
    get_foreground_window_info,
)
from agent.ghost.collector.screen_capture import ScreenCapture
from agent.ghost.collector.ocr_winrt import WinRtOcrEngine, OcrResult, OcrWord, OcrBoundingBox
from agent.ghost.collector.visual_memory import VisualMemory, VisualMemoryItem

__all__ = [
    "DesktopCollector",
    "is_workstation_locked",
    "get_foreground_window_info",
    "ScreenCapture",
    "WinRtOcrEngine",
    "OcrResult",
    "OcrWord",
    "OcrBoundingBox",
    "VisualMemory",
    "VisualMemoryItem",
]
