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

A self-contained HTML replay report (debug_report.html) is written after each session,
showing every step with annotated screenshots, focus crops, and Set-of-Marks overlays —
matching the visual debug approach used in Microsoft Record-and-Run.
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
from agent.ghost.collector.debug_report import WorkflowDebugReport, StepRecord


class VisualGrounder:
    """Visual Grounding engine for resilient desktop action execution.
    
    Maintains a WorkflowDebugReport across a workflow session and exposes
    `save_report()` to flush the self-contained HTML at the end of replay.
    """

    def __init__(self, workflow_name: str = "default"):
        safe_name = re.sub(r"[^\w.-]+", "_", workflow_name or "default").strip("_") or "default"
        self.workflow_dir = Path(".ghost/memory/screenshots") / safe_name
        self.workflow_dir.mkdir(parents=True, exist_ok=True)
        self.capturer = ScreenCapture(output_dir=self.workflow_dir)
        self.ocr_engine = WinRtOcrEngine()
        self.annotator = VisualAnnotator()
        self._report = WorkflowDebugReport(workflow_name=workflow_name, output_dir=self.workflow_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            save_debug: If True, saves annotated screenshot, focus crop, and SoM image.
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
            self._report.add_step(StepRecord(
                index=step_index, action=action_kind, label=target_name,
                status="error", error="Screen capture returned None",
            ))
            return None

        # 2. Run fast native WinRT OCR
        ocr_res: OcrResult = self.ocr_engine.recognize_image(frame)
        if not ocr_res.words and not ocr_res.lines:
            self._report.add_step(StepRecord(
                index=step_index, action=action_kind, label=target_name,
                status="error", error="OCR returned no results",
            ))
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
            self._report.add_step(StepRecord(
                index=step_index, action=action_kind, label=target_name,
                status="error", error=f"Target '{target_name}' not found in OCR results",
                full_image=self._build_full(frame, None, None, action_kind, target_name, ocr_res, show_som=True),
            ))
            return None

        # Compute center point in frame coordinates
        bx1, by1, bx2, by2 = best_box
        cx_local = int((bx1 + bx2) / 2)
        cy_local = int((by1 + by2) / 2)

        # Absolute screen coordinates
        abs_x = reg_x + cx_local
        abs_y = reg_y + cy_local

        # 4. Build annotated images and save debug artifacts
        full_img = None
        crop_img = None
        som_img  = None

        if save_debug:
            ocr_word_dicts = [w.to_dict() for w in ocr_res.words[:60]]

            # Annotated full screenshot
            full_img = self._build_full(frame, (cx_local, cy_local), best_box, action_kind, target_name, ocr_res)

            # Focus crop (500×360 centred on target)
            crop_img = self._build_crop(frame, (cx_local, cy_local), best_box)

            # Set-of-Marks overlay
            som_img = self._build_som(frame, ocr_word_dicts)

            try:
                ts = int(time.time())
                base = f"step_{step_index:03d}_{action_kind}_{ts}"
                if full_img:
                    full_img.save(self.workflow_dir / f"{base}_full.png", format="PNG")
                if crop_img:
                    crop_img.save(self.workflow_dir / f"{base}_crop.png", format="PNG")
                if som_img:
                    som_img.save(self.workflow_dir / f"{base}_som.png", format="PNG")
            except Exception:
                pass

        # Record into the HTML report
        self._report.add_step(StepRecord(
            index=step_index,
            action=action_kind,
            label=target_name,
            coords=(abs_x, abs_y),
            bbox=best_box,
            status="ok",
            timestamp=time.time(),
            full_image=full_img,
            crop_image=crop_img,
            som_image=som_img,
        ))

        return (abs_x, abs_y)

    def record_non_visual_step(
        self,
        step_index: int,
        action: str,
        label: str,
        value: Optional[str] = None,
        coords: Optional[Tuple[int, int]] = None,
        status: str = "ok",
        error: Optional[str] = None,
        capture_screenshot: bool = True,
    ) -> None:
        """Record a step that succeeded via UIA (not visual grounding) into the debug report.
        
        Call this from StepRunner after a successful UIA click/type so the HTML
        report shows ALL steps, not only the fallback ones.
        """
        full_img = None
        if capture_screenshot:
            try:
                frame = self.capturer.capture_frame()
                if frame is not None and coords:
                    full_img = self.annotator.annotate_action(
                        image=frame,
                        coords=coords,
                        action_kind=action,
                        label=label,
                    )
            except Exception:
                pass

        self._report.add_step(StepRecord(
            index=step_index,
            action=action,
            label=label,
            value=value,
            coords=coords,
            status=status,
            error=error,
            timestamp=time.time(),
            full_image=full_img,
        ))

    def save_report(self) -> Path:
        """Write the HTML debug report and return its path."""
        return self._report.save()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_full(self, frame, coords, bbox, action_kind, label, ocr_res, show_som=False):
        if Image is None:
            return None
        try:
            ocr_words = [w.to_dict() for w in ocr_res.words[:60]]
            return self.annotator.annotate_action(
                image=frame,
                coords=coords,
                bbox=bbox,
                action_kind=action_kind,
                label=label,
                ocr_words=ocr_words,
                show_som=show_som,
            )
        except Exception:
            return None

    def _build_crop(self, frame, coords, bbox):
        if Image is None:
            return None
        try:
            return self.annotator.create_focus_crop(
                image=frame,
                target_point=coords,
                bbox=bbox,
                crop_size=(500, 360),
            )
        except Exception:
            return None

    def _build_som(self, frame, ocr_word_dicts):
        if Image is None:
            return None
        try:
            return self.annotator.annotate_action(
                image=frame,
                coords=None,
                bbox=None,
                action_kind="som",
                label="",
                ocr_words=ocr_word_dicts,
                show_som=True,
            )
        except Exception:
            return None
