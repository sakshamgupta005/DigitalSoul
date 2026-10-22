"""Visual Action Annotator & Grounding Overlay Generator.

Provides rich visual markings on desktop screenshots:
1. Target bounding boxes with translucent highlights.
2. Click indicators (radial ripples, targeting crosshair).
3. Action badges ([Click] "Send Button", [Type] "Hello").
4. High-resolution localized focus crops for VLM training without downscaling.
5. Set-of-Marks (SoM) indexed visual grounding tags.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None


class VisualAnnotator:
    """Renders professional grounding annotations and context crops on screenshots."""

    # Curated color palette
    COLOR_CLICK = (255, 75, 75)        # Vibrant Red-Orange for clicks
    COLOR_TYPE = (52, 152, 219)        # Blue for text input
    COLOR_FOCUS = (46, 204, 113)       # Green for active focus
    COLOR_SOM_TAG = (241, 196, 15)     # Gold for Set-of-Marks tags
    COLOR_BOX_FILL = (0, 200, 120, 45) # Translucent highlight
    COLOR_BADGE_BG = (20, 24, 28, 230) # Dark pill badge background

    def __init__(self):
        self._font = None
        self._small_font = None
        self._init_fonts()

    def _init_fonts(self):
        if Image is None:
            return
        try:
            # Try to load standard Windows system font
            self._font = ImageFont.truetype("segoeui.ttf", 13)
            self._small_font = ImageFont.truetype("segoeui.ttf", 10)
        except Exception:
            try:
                self._font = ImageFont.truetype("arial.ttf", 13)
                self._small_font = ImageFont.truetype("arial.ttf", 10)
            except Exception:
                self._font = ImageFont.load_default()
                self._small_font = ImageFont.load_default()

    def annotate_action(
        self,
        image: Image.Image,
        coords: Optional[Tuple[float, float]] = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        action_kind: str = "click",
        label: Optional[str] = None,
        ocr_words: Optional[List[Dict[str, Any]]] = None,
        show_som: bool = False,
    ) -> Image.Image:
        """Draws visual action markings on a copy of the screenshot.
        
        Args:
            image: Base PIL Image.
            coords: (x, y) coordinates of the click or interaction.
            bbox: (left, top, right, bottom) bounding box of target element.
            action_kind: "click", "set_text", "press_enter", etc.
            label: Text label describing the target (e.g. "Send", "Search").
            ocr_words: Optional list of OCR detected words with boxes.
            show_som: If True, draws numbered Set-of-Marks tags over OCR words.
            
        Returns:
            New PIL Image with composite markings.
        """
        if Image is None or image is None:
            return image

        width, height = image.size
        # Work in RGBA for translucent layers
        annotated = image.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # 1. Optional Set-of-Marks (SoM) visual tags
        if show_som and ocr_words:
            for idx, item in enumerate(ocr_words[:60], start=1):
                box = item.get("box", {})
                bx = box.get("x", 0)
                by = box.get("y", 0)
                bw = box.get("width", 0)
                bh = box.get("height", 0)
                if bw > 5 and bh > 5:
                    draw.rectangle(
                        [bx, by, bx + bw, by + bh],
                        outline=(241, 196, 15, 140),
                        width=1,
                    )
                    # Small numbered tag
                    tag_text = str(idx)
                    tag_w, tag_h = 14, 12
                    draw.rectangle(
                        [bx, max(0, by - tag_h), bx + tag_w, max(0, by - tag_h) + tag_h],
                        fill=(241, 196, 15, 230),
                    )
                    draw.text(
                        (bx + 2, max(0, by - tag_h) - 1),
                        tag_text,
                        fill=(20, 20, 20, 255),
                        font=self._small_font,
                    )

        # Determine theme color based on action kind
        theme_color = self.COLOR_CLICK
        if action_kind == "set_text":
            theme_color = self.COLOR_TYPE
        elif action_kind in ("focus", "open_app"):
            theme_color = self.COLOR_FOCUS

        # 2. Draw Target Bounding Box
        if bbox is not None:
            l, t, r, b = bbox
            l, t = max(0, l), max(0, t)
            r, b = min(width, r), min(height, b)
            # Translucent fill
            fill_color = (theme_color[0], theme_color[1], theme_color[2], 40)
            outline_color = (theme_color[0], theme_color[1], theme_color[2], 230)
            draw.rectangle([l, t, r, b], fill=fill_color, outline=outline_color, width=2)

        # 3. Draw Click Indicator (Radial Ripple + Center Dot)
        if coords is not None:
            cx, cy = coords
            # Outer ripple
            draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], outline=(theme_color[0], theme_color[1], theme_color[2], 100), width=2)
            # Inner ripple
            draw.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], outline=(theme_color[0], theme_color[1], theme_color[2], 180), width=2)
            # Solid center dot
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(theme_color[0], theme_color[1], theme_color[2], 255))
            # Crosshair tick marks
            draw.line([(cx - 16, cy), (cx - 6, cy)], fill=(255, 255, 255, 220), width=1)
            draw.line([(cx + 6, cy), (cx + 16, cy)], fill=(255, 255, 255, 220), width=1)
            draw.line([(cx, cy - 16), (cx, cy - 6)], fill=(255, 255, 255, 220), width=1)
            draw.line([(cx, cy + 6), (cx, cy + 16)], fill=(255, 255, 255, 220), width=1)

        # 4. Draw Action Badge Pill
        badge_text = f"[{action_kind.upper()}]"
        if label:
            clean_label = label.replace("\n", " ")[:32]
            badge_text += f" {clean_label}"

        # Position badge near bbox or coords
        if bbox is not None:
            bx = bbox[0]
            by = max(4, bbox[1] - 24)
        elif coords is not None:
            bx = coords[0] + 16
            by = max(4, coords[1] - 24)
        else:
            bx, by = 16, 16

        # Draw badge pill
        badge_pad_x = 8
        badge_pad_y = 4
        # Estimate text size
        text_len = len(badge_text) * 7 + 16
        draw.rounded_rectangle(
            [bx, by, bx + text_len, by + 20],
            radius=4,
            fill=self.COLOR_BADGE_BG,
            outline=(theme_color[0], theme_color[1], theme_color[2], 220),
            width=1,
        )
        draw.text((bx + badge_pad_x, by + 2), badge_text, fill=(245, 245, 245, 255), font=self._font)

        # Composite overlay on top of base image
        result = Image.alpha_composite(annotated, overlay)
        return result.convert("RGB")

    def create_focus_crop(
        self,
        image: Image.Image,
        target_point: Optional[Tuple[float, float]] = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        crop_size: Tuple[int, int] = (500, 360),
    ) -> Image.Image:
        """Generates a high-resolution localized context crop centered on the action target.
        
        Preserves original pixel density so small text, icons, and buttons are crystal clear
        for vision models without needing to resize full 1080p/4K frames.
        """
        if Image is None or image is None:
            return image

        width, height = image.size
        crop_w, crop_h = crop_size

        # Determine center point
        if bbox is not None:
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
        elif target_point is not None:
            cx, cy = target_point
        else:
            cx, cy = width / 2.0, height / 2.0

        # Calculate bounding coordinates clamped to image borders
        left = max(0, int(cx - crop_w / 2.0))
        top = max(0, int(cy - crop_h / 2.0))
        right = min(width, left + crop_w)
        bottom = min(height, top + crop_h)

        # Adjust left/top if clamped at right/bottom
        if right - left < crop_w and left > 0:
            left = max(0, right - crop_w)
        if bottom - top < crop_h and top > 0:
            top = max(0, bottom - crop_h)

        return image.crop((left, top, right, bottom))
