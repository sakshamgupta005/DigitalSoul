"""Visual Grounding & Target Locator for DigitalSoul.

Inspired by Microsoft Record-and-Run (https://github.com/microsoft/Record-and-Run):
When deterministic UI Automation (UIA) tree traversal cannot resolve an element
in dynamic, web-embedded, or custom desktop applications (such as WhatsApp WebView2,
Slack, electron apps, or Canvas games), VisualGrounder captures a live screen frame,
runs native WinRT OCR and fuzzy visual text/icon matching, and pinpoints exact
screen coordinates so execution can continue seamlessly without stalling.

Debug screenshots with visual highlights (bounding boxes, click ripples, action tags)
are saved directly into per-workflow subfolders:
    .ghost/memory/screenshots/<workflow_name>/
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image
except ImportError:
    Image = None

from agent.ghost.collector.ocr_winrt import OcrResult, WinRtOcrEngine
from agent.ghost.collector.screen_capture import ScreenCapture
from agent.ghost.collector.visual_annotator import VisualAnnotator


class VisualGrounder:
    """Visual Grounding engine for resilient desktop action execution."""

    def __init__(self, workflow_name: str = "default"):
        safe_name = re.sub(r"[^\w.-]+", "_", workflow_name or "default").strip("_") or "default"
        self.workflow_dir = Path(".ghost/memory/screenshots") / safe_name
        self.workflow_dir.mkdir(parents=True, exist_ok=True)
        self.capturer = ScreenCapture(output_dir=self.workflow_dir)
        self.ocr_engine = WinRtOcrEngine()
        self.annotator = VisualAnnotator()

    def find_target_point(
        self,
        target_name: str,
        window_rect: Optional[Dict[str, int]] = None,
        expected_type: Optional[str] = None,
        save_debug: bool = True,
        step_index: int = 0,
        action_kind: str = "click",
    ) -> Optional[Tuple[int, int]]:
        """Visually locates the center coordinates (cx, cy) of a target on the live desktop.

        Args:
            target_name: The expected label, text, or button name (e.g., 'Search', 'Send', contact name).
            window_rect: Optional dict with keys 'left', 'top', 'width', 'height' to restrict screen capture.
            expected_type: Optional control hint ('button', 'edit', 'text').
            save_debug: If True, saves annotated screenshot with bounding box and click ripple.
            step_index: Step number for debug filename ordering.
            action_kind: 'click', 'set_text', etc. for themed markings.

        Returns:
            (x, y) absolute desktop pixel coordinates, or None if not visually located.
        """
        if not target_name:
            return None

        clean_target = target_name.strip().lower()
        if not clean_target:
            return None

        # Determine capture region if window_rect is provided
        region = None
        if window_rect:
            left = window_rect.get("left", 0)
            top = window_rect.get("top", 0)
            width = window_rect.get("width", 0)
            height = window_rect.get("height", 0)
            if width > 50 and height > 50:
                region = (left, top, left + width, top + height)

        # 1. Grab live frame
        frame = self.capturer.capture_frame(region=region)
        if frame is None:
            return None

        # 2. Run fast native WinRT OCR
        ocr_res: OcrResult = self.ocr_engine.recognize_image(frame)
        if not ocr_res.words and not ocr_res.lines:
            return None

        # Offset if capture was in window region
        reg_x = region[0] if region else 0
        reg_y = region[1] if region else 0

        # 3. Match candidate words or lines
        best_box = None
        best_score = 0.0

        # Exact / Substring search across detected text lines
        for line in ocr_res.lines:
            line_text = line.text.strip().lower()
            if clean_target == line_text:
                score = 1.0
            elif clean_target in line_text or line_text in clean_target:
                score = 0.8
            else:
                # Word-token overlap score
                target_words = set(clean_target.split())
                line_words = set(line_text.split())
                overlap = len(target_words & line_words)
                score = (overlap / max(len(target_words), 1)) * 0.7 if overlap else 0.0

            if score > best_score and score >= 0.5:
                best_score = score
                b = line.box
                best_box = (b.x, b.y, b.x + b.width, b.y + b.height)

        # Fallback to single words if multi-line didn't score high enough
        if best_score < 0.8 and ocr_res.words:
            for w in ocr_res.words:
                w_text = w.text.strip().lower()
                if clean_target == w_text:
                    b = w.box
                    best_box = (b.x, b.y, b.x + b.width, b.y + b.height)
                    best_score = 0.95
                    break
                elif clean_target in w_text or w_text in clean_target:
                    if best_score < 0.75:
                        b = w.box
                        best_box = (b.x, b.y, b.x + b.width, b.y + b.height)
                        best_score = 0.75

        if best_box is None:
            return None

        # Compute center point in frame coordinates
        bx1, by1, bx2, by2 = best_box
        cx_local = int((bx1 + bx2) / 2)
        cy_local = int((by1 + by2) / 2)

        # Absolute screen coordinates
        abs_x = reg_x + cx_local
        abs_y = reg_y + cy_local

        # 4. Save debug screenshot with visual markings into workflow subfolder
        if save_debug:
            try:
                marked = self.annotator.annotate_action(
                    image=frame,
                    coords=(cx_local, cy_local),
                    bbox=best_box,
                    action_kind=action_kind,
                    label=target_name,
                    ocr_words=[w.to_dict() for w in ocr_res.words[:50]],
                )
                filename = f"step_{step_index:03d}_{action_kind}_{int(time.time())}.png"
                marked.save(self.workflow_dir / filename, format="PNG")
            except Exception:
                pass

        return (abs_x, abs_y)
