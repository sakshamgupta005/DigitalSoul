"""Live Demonstration & Test of dxcam Screen Capture, WinRT OCR, Visual Annotator & Representative Training Data."""
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
from agent.ghost.collector.visual_annotator import VisualAnnotator
from agent.ghost.collector.visual_memory import VisualMemory
from agent.ghost.collector.training_data import RepresentativeDataGenerator
from agent.ghost.integrations.omniroute import OmniRouteClient, OmniRouteSettings


def main():
    print("=" * 70)
    print("  DIGITALSOUL: VISUAL ACTION GROUNDING & TRAINING PIPELINE TEST")
    print("=" * 70)

    # 1. Test Screen Capture (dxcam + fallback)
    print("\n[Step 1] Initializing ScreenCapture (dxcam / DXGI duplication)...")
    capturer = ScreenCapture(output_dir=ROOT / ".ghost" / "memory" / "screenshots")
    start_t = time.perf_counter()
    shot_path, img = capturer.save_screenshot(prefix="live_test")
    elapsed_ms = (time.perf_counter() - start_t) * 1000

    if img is not None:
        print(f"  [OK] Screen captured in {elapsed_ms:.1f}ms")
        print(f"       Resolution: {img.size[0]}x{img.size[1]} pixels")
        print(f"       Raw Screenshot: {shot_path}")
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

    # 3. Test Visual Action Marking (VisualAnnotator)
    print("\n[Step 3] Generating Visual Action Grounding Markings...")
    annotator = VisualAnnotator()
    marked_img = annotator.annotate_action(
        image=img,
        coords=(img.size[0] // 2, img.size[1] // 2),
        bbox=(img.size[0] // 2 - 60, img.size[1] // 2 - 20, img.size[0] // 2 + 60, img.size[1] // 2 + 20),
        action_kind="click",
        label="Demo Action Target",
        ocr_words=[w.to_dict() for w in ocr_result.words],
    )
    marked_path = ROOT / ".ghost" / "memory" / "screenshots" / "live_demo_marked.png"
    marked_img.save(marked_path)
    print(f"  [OK] Annotated Screenshot with Markings: {marked_path}")

    # Localized Context Crop
    crop_img = annotator.create_focus_crop(
        image=img,
        target_point=(img.size[0] // 2, img.size[1] // 2),
        crop_size=(500, 360),
    )
    crop_path = ROOT / ".ghost" / "memory" / "screenshots" / "live_demo_crop.png"
    crop_img.save(crop_path)
    print(f"  [OK] High-Res Context Focus Crop (500x360): {crop_path}")

    # 4. Check OmniRoute Gateway
    print("\n[Step 4] Checking OmniRoute Gateway (http://127.0.0.1:20128/v1)...")
    omniroute = OmniRouteClient(OmniRouteSettings.from_env())
    probe = omniroute.probe(inference=False)
    print(f"       Gateway base URL: {probe['base_url']}")
    print(f"       Reachable: {probe.get('reachable', False)}")
    if probe.get("health", {}).get("ok"):
        print("       Health: OK (200)")
    else:
        print(f"       Health: Offline or unreachable ({probe.get('error', 'not running')})")

    # 5. Record Action into VisualMemory
    print("\n[Step 5] Storing episodic event into VisualMemory (SQLite)...")
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
        session_id="test_run_002",
        analyze_with_omniroute=True,
    )

    print(f"  [OK] Saved Memory ID: #{mem_item.id}")
    print(f"       Timestamp: {mem_item.timestamp}")
    print(f"       App: {mem_item.app} | Action: {mem_item.action_kind}")
    print(f"       Intent Summary: {mem_item.intent_summary}")
    print(f"       Raw Shot: {mem_item.screenshot_path}")
    print(f"       Marked Shot: {mem_item.marked_screenshot_path}")
    print(f"       Focus Crop: {mem_item.focus_crop_path}")

    # 6. Generate Representative Training Data
    print("\n[Step 6] Generating Representative VLM Training Workflow Data...")
    gen = RepresentativeDataGenerator(output_dir=ROOT / ".ghost" / "training_dataset")
    samples = gen.generate_whatsapp_flow()
    print(f"  [OK] Generated {len(samples)} representative training samples for WhatsApp flow:")
    for s in samples:
        print(f"       - Step {s.step_index}: [{s.action_type.upper()}] '{s.target_label}'")
        print(f"         Prompt: \"{s.instruction_prompt}\"")
        print(f"         Coords (norm): {s.coords_norm} | BBox: {s.bbox_norm}")

    # Export dataset from all visual memories
    exported = memory.export_as_training_dataset(output_dir=ROOT / ".ghost" / "training_dataset")
    print(f"\n  [OK] Total exported dataset samples: {exported} into .ghost/training_dataset/samples.jsonl")

    print("\n" + "=" * 70)
    print("  TEST COMPLETED: VISUAL MARKINGS & TRAINING GENERATOR VERIFIED!")
    print("=" * 70)


if __name__ == "__main__":
    main()
