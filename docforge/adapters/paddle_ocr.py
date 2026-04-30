"""PaddleOCR adapter for text recognition on scanned PDFs.

PaddleOCR is an optional dependency. This adapter gracefully handles
the case where it is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage

logger = logging.getLogger(__name__)

# Lazy import to handle missing dependency
_paddleocr_available: bool | None = None
_ocr_instance: Any = None


def _check_availability() -> bool:
    """Check if PaddleOCR is installed and importable."""
    global _paddleocr_available
    if _paddleocr_available is not None:
        return _paddleocr_available

    try:
        import paddleocr  # noqa: F401
        _paddleocr_available = True
    except ImportError:
        _paddleocr_available = False
        logger.info("PaddleOCR not installed - OCR features disabled")

    return _paddleocr_available


def _get_ocr_instance() -> Any:
    """Get or create PaddleOCR instance (singleton)."""
    global _ocr_instance
    if _ocr_instance is not None:
        return _ocr_instance

    if not _check_availability():
        return None

    from paddleocr import PaddleOCR

    _ocr_instance = PaddleOCR(
        use_textline_orientation=True,
        lang="korean",
    )
    return _ocr_instance


class PaddleOCREngine:
    """OCR engine using PaddleOCR."""

    def is_available(self) -> bool:
        """Check if PaddleOCR is installed and ready."""
        return _check_availability()

    def recognize(self, image: Any) -> list[TextBlock]:
        """Run OCR on a PIL Image and return text blocks.

        Args:
            image: PIL.Image.Image object.

        Returns:
            List of TextBlock with recognized text and confidence scores.
        """
        if not self.is_available():
            logger.warning("PaddleOCR not available - returning empty results")
            return []

        ocr = _get_ocr_instance()
        if ocr is None:
            return []

        import numpy as np
        from PIL import Image

        # Coerce input to numpy array — RawImage / PIL.Image / ndarray supported
        if isinstance(image, RawImage):
            img_array = image.data
        elif isinstance(image, Image.Image):
            img_array = np.array(image)
        elif isinstance(image, np.ndarray):
            img_array = image
        else:
            logger.error("PaddleOCR: unsupported image type %s", type(image).__name__)
            return []

        try:
            results = ocr.ocr(img_array, cls=True)
        except Exception:
            logger.error("PaddleOCR recognition failed", exc_info=True)
            return []

        blocks: list[TextBlock] = []

        if not results or not results[0]:
            return blocks

        for line in results[0]:
            if not line or len(line) < 2:
                continue

            box_points = line[0]
            text_info = line[1]

            if not text_info or len(text_info) < 2:
                continue

            text = str(text_info[0])
            confidence = float(text_info[1])

            # Convert 4-point box to BBox
            xs = [p[0] for p in box_points]
            ys = [p[1] for p in box_points]

            blocks.append(TextBlock(
                text=text,
                bbox=BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
                font=FontInfo(name="ocr", size=0.0, is_bold=False),
                block_type=BlockType.TEXT,
                confidence=confidence,
            ))

        return blocks
