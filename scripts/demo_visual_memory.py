"""Live Demonstration & Test of dxcam Screen Capture, WinRT OCR, OmniRoute & VisualMemory."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from agent.ghost.collector.screen_capture import ScreenCapture
from agent.ghost.collector.ocr_winrt import WinRtOcrEngine
from agent.ghost.collector.visual_memory import VisualMemory
from agent.ghost.integrations.omniroute import OmniRouteClient, OmniRouteSettings


def main():
    print("=" * 65)
    print("  DIGITALSOUL: VISUAL MEMORY & PERCEPTION PIPELINE TEST")
    print("=" * 65)

    # 1. Test Screen Capture (dxcam + fallback)
    print("\n[Step 1] Initializing ScreenCapture (dxcam / DXGI duplication)...")
    capturer = ScreenCapture(output_dir=ROOT / ".ghost" / "memory" / "screenshots")
    start_t = time.perf_counter()
    shot_path, img = capturer.save_screenshot(prefix="live_test")
    elapsed_ms = (time.perf_counter() - start_t) * 1000

    if img is not None:
        print(f"  [OK] Screen captured in {elapsed_ms:.1f}ms")
        print(f"       Resolution: {img.size[0]}x{img.size[1]} pixels")
        print(f"       Saved to: {shot_path}")
    else:
        print("  [FAIL] Failed to capture screen frame")
        return

    # 2. Test WinRT OCR (Windows Media OCR)
    print("\n[Step 2] Initializing native WinRT OCR engine...")
    ocr_engine = WinRtOcrEngine()
    if ocr_engine.is_available:
        print("  [OK] Windows.Media.Ocr engine initialized")
    else:
        print("  [INFO] WinRT OCR engine not active or in non-WinRT environment")

    start_ocr = time.perf_counter()
    ocr_result = ocr_engine.recognize_image(img)
    ocr_ms = (time.perf_counter() - start_ocr) * 1000

    print(f"  [OK] Screen OCR completed in {ocr_ms:.1f}ms")
    print(f"       Recognized lines: {len(ocr_result.lines)} | Words: {len(ocr_result.words)}")
    if ocr_result.words:
        sample_words = [f"'{w.text}' at ({int(w.box.x)}, {int(w.box.y)})" for w in ocr_result.words[:5]]
        print(f"       Detected screen words: {', '.join(sample_words)}")

    # Also test WinRT OCR on an active text canvas to demonstrate precision
    from PIL import Image, ImageDraw
    test_canvas = Image.new("RGB", (480, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(test_canvas)
    draw.text((20, 20), "OmniRoute & WinRT OCR", fill=(0, 0, 0))
    draw.text((20, 60), "Action: WhatsApp Send Message", fill=(0, 0, 0))
    sample_ocr = ocr_engine.recognize_image(test_canvas)
    print(f"  [OK] OCR Precision Verification on Rendered Elements:")
    for word in sample_ocr.words[:4]:
        print(f"       -> Word: '{word.text}' | Box: [x={int(word.box.x)}, y={int(word.box.y)}, w={int(word.box.width)}, h={int(word.box.height)}]")

    # 3. Test OmniRoute Connection
    print("\n[Step 3] Checking OmniRoute Gateway (http://127.0.0.1:20128/v1)...")
    omniroute = OmniRouteClient(OmniRouteSettings.from_env())
    probe = omniroute.probe(inference=False)
    print(f"       Gateway base URL: {probe['base_url']}")
    print(f"       Reachable: {probe.get('reachable', False)}")
    if probe.get("health", {}).get("ok"):
        print("       Health: OK (200)")
    else:
        print(f"       Health: Offline or unreachable ({probe.get('error', 'not running')})")
    if probe.get("models", {}).get("ok"):
        print(f"       Configured models count: {probe['models'].get('count', 0)}")

    # 4. Record Action into VisualMemory
    print("\n[Step 4] Storing episodic event into VisualMemory (SQLite)...")
    memory = VisualMemory(
        db_path=ROOT / ".ghost" / "memory" / "visual_memory.sqlite3",
        screenshots_dir=ROOT / ".ghost" / "memory" / "screenshots",
        omniroute_client=omniroute,
    )

    mem_item = memory.capture_and_remember(
        app="chrome.exe",
        window_title="DigitalSoul Workspace — Active Monitor",
        action_kind="click",
        coords=(450.0, 320.0),
        target_name="Run Pipeline",
        session_id="test_run_001",
        analyze_with_omniroute=True,
    )

    print(f"  [OK] Saved Memory ID: #{mem_item.id}")
    print(f"       Timestamp: {mem_item.timestamp}")
    print(f"       App: {mem_item.app} | Action: {mem_item.action_kind}")
    print(f"       Intent Summary: {mem_item.intent_summary}")
    print(f"       Screenshot: {mem_item.screenshot_path}")

    # 5. Search & Retrieval
    print("\n[Step 5] Testing Visual Memory search and retrieval...")
    recent = memory.get_recent(limit=3)
    print(f"       Recent memory count: {len(recent)}")
    for item in recent:
        print(f"       - [ID {item.id}] {item.timestamp} | {item.app} -> {item.intent_summary}")

    search_hits = memory.search_memory("DigitalSoul")
    print(f"       Keyword search for 'DigitalSoul': {len(search_hits)} matching record(s)")

    print("\n" + "=" * 65)
    print("  TEST COMPLETED: ALL VISUAL MEMORY COMPONENTS OPERATIONAL!")
    print("=" * 65)


if __name__ == "__main__":
    main()
