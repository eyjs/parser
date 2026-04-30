"""EasyOCR adapter for text recognition on scanned PDFs.

EasyOCR is an optional dependency. This adapter gracefully handles
the case where it is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage

logger = logging.getLogger(__name__)

_easyocr_available: bool | None = None
_ocr_reader: Any = None


def _check_availability() -> bool:
    global _easyocr_available
    if _easyocr_available is not None:
        return _easyocr_available

    try:
        import easyocr  # noqa: F401
        _easyocr_available = True
    except ImportError:
        _easyocr_available = False
        logger.info("EasyOCR not installed - OCR features disabled")

    return _easyocr_available


def _get_reader(langs: tuple[str, ...] = ("ko", "en")) -> Any:
    global _ocr_reader
    if _ocr_reader is not None:
        return _ocr_reader

    if not _check_availability():
        return None

    import easyocr

    _ocr_reader = easyocr.Reader(list(langs), gpu=False)
    return _ocr_reader


class EasyOCREngine:
    """OCR engine using EasyOCR."""

    def is_available(self) -> bool:
        return _check_availability()

    def recognize(self, image: Any) -> list[TextBlock]:
        if not self.is_available():
            logger.warning("EasyOCR not available - returning empty results")
            return []

        reader = _get_reader()
        if reader is None:
            return []

        import numpy as np
        from PIL import Image

        if isinstance(image, RawImage):
            img_array = image.data
        elif isinstance(image, Image.Image):
            img_array = np.array(image)
        elif isinstance(image, np.ndarray):
            img_array = image
        else:
            logger.error("EasyOCR: unsupported image type %s", type(image).__name__)
            return []

        try:
            results = reader.readtext(img_array)
        except Exception:
            logger.error("EasyOCR recognition failed", exc_info=True)
            return []

        blocks: list[TextBlock] = []

        for item in results:
            if len(item) < 3:
                continue

            box_points, text, confidence = item

            if not text or not text.strip():
                continue

            xs = [p[0] for p in box_points]
            ys = [p[1] for p in box_points]

            blocks.append(TextBlock(
                text=text.strip(),
                bbox=BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
                font=FontInfo(name="ocr", size=0.0, is_bold=False),
                block_type=BlockType.TEXT,
                confidence=float(confidence),
            ))

        return blocks
