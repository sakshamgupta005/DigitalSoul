"""Native Windows Media OCR (WinRT OCR) for high-speed local text extraction.

Extracts text, word tokens, and bounding boxes directly using Windows.Media.Ocr.
Requires no external cloud requests or heavy OCR binaries.
"""
from __future__ import annotations

import asyncio
import io
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None

_winrt_available = False
if sys.platform == "win32":
    try:
        import winrt.windows.media.ocr as _ocr
        import winrt.windows.graphics.imaging as _imaging
        import winrt.windows.storage.streams as _streams
        _winrt_available = True
    except ImportError:
        _winrt_available = False


@dataclass
class OcrBoundingBox:
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= (self.x + self.width) and self.y <= py <= (self.y + self.height)


@dataclass
class OcrWord:
    text: str
    box: OcrBoundingBox

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "box": self.box.to_dict()}


@dataclass
class OcrResult:
    text: str
    lines: List[str] = field(default_factory=list)
    words: List[OcrWord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "lines": self.lines,
            "words": [w.to_dict() for w in self.words],
        }

    def word_at_point(self, x: float, y: float) -> Optional[OcrWord]:
        """Find the word bounding box covering (x, y)."""
        for word in self.words:
            if word.box.contains(x, y):
                return word
        return None

    def find_words(self, query: str) -> List[OcrWord]:
        """Find words containing the query substring."""
        q = query.lower()
        return [w for w in self.words if q in w.text.lower()]


class WinRtOcrEngine:
    """Wrapper around Windows.Media.Ocr.OcrEngine."""

    def __init__(self):
        self._engine = None
        if _winrt_available:
            try:
                self._engine = _ocr.OcrEngine.try_create_from_user_profile_languages()
            except Exception:
                self._engine = None

    @property
    def is_available(self) -> bool:
        return self._engine is not None

    async def recognize_image_async(self, image: Any) -> OcrResult:
        """Run OCR on a PIL Image asynchronously."""
        if not self.is_available:
            return OcrResult(text="", lines=[], words=[])

        # Convert PIL Image or raw bytes to PNG bytes
        if isinstance(image, bytes):
            png_bytes = image
        elif Image is not None and isinstance(image, Image.Image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        else:
            return OcrResult(text="", lines=[], words=[])

        try:
            # Transfer bytes to WinRT InMemoryRandomAccessStream
            writer = _streams.DataWriter()
            writer.write_bytes(png_bytes)
            stream = _streams.InMemoryRandomAccessStream()
            await stream.write_async(writer.detach_buffer())
            stream.seek(0)

            decoder = await _imaging.BitmapDecoder.create_async(stream)
            software_bitmap = await decoder.get_software_bitmap_async()

            result = await self._engine.recognize_async(software_bitmap)

            recognized_text = result.text or ""
            lines_list: List[str] = []
            words_list: List[OcrWord] = []

            for line in result.lines:
                lines_list.append(line.text)
                for word in line.words:
                    rect = word.bounding_rect
                    words_list.append(
                        OcrWord(
                            text=word.text,
                            box=OcrBoundingBox(
                                x=float(rect.x),
                                y=float(rect.y),
                                width=float(rect.width),
                                height=float(rect.height),
                            ),
                        )
                    )

            return OcrResult(text=recognized_text, lines=lines_list, words=words_list)
        except Exception:
            return OcrResult(text="", lines=[], words=[])

    def recognize_image(self, image: Any) -> OcrResult:
        """Synchronous wrapper for recognize_image_async."""
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(self.recognize_image_async(image))).result(timeout=10)
            return asyncio.run(self.recognize_image_async(image))
        except Exception:
            return OcrResult(text="", lines=[], words=[])
