"""Apple Vision Framework OCR adapter (macOS only).

Uses pyobjc-framework-Vision for text recognition.
Gracefully reports unavailable on non-macOS platforms.
"""

from __future__ import annotations

import logging
import platform
from typing import Any

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo

logger = logging.getLogger(__name__)

_vision_available: bool | None = None


def _check_availability() -> bool:
    """Check if Apple Vision Framework is available."""
    global _vision_available
    if _vision_available is not None:
        return _vision_available

    if platform.system() != "Darwin":
        _vision_available = False
        logger.debug("Apple Vision not available: not macOS")
        return False

    try:
        import Vision  # noqa: F401
        _vision_available = True
    except ImportError:
        _vision_available = False
        logger.info(
            "Apple Vision not available: pyobjc-framework-Vision not installed"
        )

    return _vision_available


class AppleVisionOCREngine:
    """OCR engine using Apple Vision Framework (macOS only)."""

    def is_available(self) -> bool:
        """Check if Apple Vision is available on this system."""
        return _check_availability()

    def recognize(self, image: Any) -> list[TextBlock]:
        """Run OCR using Apple Vision Framework.

        Args:
            image: PIL Image or numpy array.

        Returns:
            List of recognized TextBlock objects.
        """
        if not self.is_available():
            logger.warning("Apple Vision not available - returning empty results")
            return []

        # Full implementation for macOS
        try:
            return self._recognize_with_vision(image)
        except Exception as exc:
            logger.warning("Apple Vision OCR failed: %s", exc, exc_info=True)
            return []

    def _recognize_with_vision(self, image: Any) -> list[TextBlock]:
        """Actual Vision framework recognition (macOS only)."""
        import Vision
        from Foundation import NSData
        from PIL import Image
        import numpy as np
        import io

        # Convert to PIL Image if needed
        if isinstance(image, np.ndarray):
            image = Image.fromarray(image)

        # Convert PIL to NSData
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        ns_data = NSData.dataWithBytes_length_(
            buffer.getvalue(), len(buffer.getvalue())
        )

        # Create request handler
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            ns_data, None
        )

        # Create text recognition request
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(
            Vision.VNRequestTextRecognitionLevelAccurate
        )
        request.setRecognitionLanguages_(["ko-KR", "en-US"])
        request.setUsesLanguageCorrection_(True)

        # Execute
        success = handler.performRequests_error_([request], None)
        if not success[0]:
            logger.error("Vision request failed")
            return []

        results = request.results()
        if not results:
            return []

        # Convert results to TextBlocks
        img_width, img_height = image.size
        blocks: list[TextBlock] = []

        for observation in results:
            text = observation.text()
            confidence = float(observation.confidence())

            # Vision uses normalized coordinates (0-1), convert to pixel
            bbox = observation.boundingBox()
            x0 = bbox.origin.x * img_width
            y0 = (1 - bbox.origin.y - bbox.size.height) * img_height
            x1 = (bbox.origin.x + bbox.size.width) * img_width
            y1 = (1 - bbox.origin.y) * img_height

            blocks.append(TextBlock(
                text=text.strip(),
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                font=FontInfo(name="apple_vision", size=0.0, is_bold=False),
                block_type=BlockType.TEXT,
                confidence=confidence,
            ))

        return blocks
