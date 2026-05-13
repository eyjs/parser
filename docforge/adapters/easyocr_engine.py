"""EasyOCR adapter — cross-platform OCR fallback for Docker environments.

Used when Apple Vision OCR (macOS-only) and PaddleOCR are both unavailable.
EasyOCR supports Korean + English out of the box and runs on CPU.

The ``easyocr`` package is an optional dependency.  When not installed,
``is_available()`` returns ``False`` and the OCR factory skips this backend.
"""

from __future__ import annotations

import logging
from typing import Any

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo

logger = logging.getLogger(__name__)


class EasyOCREngine:
    """OCR engine backed by EasyOCR (Korean + English)."""

    def __init__(self, languages: list[str] | None = None) -> None:
        self._languages = languages or ["ko", "en"]
        self._reader: Any = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        """Check whether easyocr can be imported."""
        if self._available is not None:
            return self._available
        try:
            import easyocr  # noqa: F401

            self._available = True
        except ImportError:
            logger.debug("easyocr not installed — EasyOCR backend unavailable")
            self._available = False
        return self._available

    def recognize(self, image: Any) -> list[TextBlock]:
        """Run OCR on an image and return TextBlock list.

        Args:
            image: A RawImage, numpy ndarray, or PIL Image.

        Returns:
            List of TextBlock with bbox, text, and confidence.
        """
        if not self.is_available():
            return []

        try:
            img_array = self._to_numpy(image)
            reader = self._get_reader()
            results = reader.readtext(img_array)
            return self._convert_results(results)
        except Exception as exc:
            logger.warning("EasyOCR recognition failed: %s", exc, exc_info=True)
            return []

    # -- internal helpers --------------------------------------------------

    def _get_reader(self) -> Any:
        """Lazy-initialize the EasyOCR Reader (model download on first call)."""
        if self._reader is not None:
            return self._reader

        import easyocr

        self._reader = easyocr.Reader(
            self._languages,
            gpu=False,
            verbose=False,
        )
        logger.info(
            "EasyOCR Reader initialized (languages=%s, gpu=False)",
            self._languages,
        )
        return self._reader

    @staticmethod
    def _to_numpy(image: Any) -> Any:
        """Convert various image types to numpy array."""
        import numpy as np

        from docforge.domain.value_objects import RawImage

        if isinstance(image, RawImage):
            return image.data
        if isinstance(image, np.ndarray):
            return image

        # PIL Image
        try:
            from PIL import Image

            if isinstance(image, Image.Image):
                return np.array(image)
        except ImportError:
            pass

        raise TypeError(f"Unsupported image type for EasyOCR: {type(image)}")

    @staticmethod
    def _convert_results(results: list) -> list[TextBlock]:
        """Convert EasyOCR results to TextBlock list.

        EasyOCR returns: list of (bbox_polygon, text, confidence)
        where bbox_polygon is [[x0,y0], [x1,y0], [x1,y1], [x0,y1]].
        """
        blocks: list[TextBlock] = []
        for item in results:
            polygon, text, confidence = item
            if not text or not text.strip():
                continue

            # Convert 4-point polygon to axis-aligned BBox
            xs = [p[0] for p in polygon]
            ys = [p[1] for p in polygon]
            bbox = BBox(
                x0=float(min(xs)),
                y0=float(min(ys)),
                x1=float(max(xs)),
                y1=float(max(ys)),
            )

            blocks.append(TextBlock(
                text=text.strip(),
                bbox=bbox,
                font=FontInfo(name="easyocr", size=0.0, is_bold=False),
                block_type=BlockType.TEXT,
                confidence=float(confidence),
            ))

        return blocks
