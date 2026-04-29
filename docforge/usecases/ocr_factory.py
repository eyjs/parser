"""OCR engine factory — pluggable backend selection.

Creates the appropriate OCR engine based on configuration.
Supports graceful degradation: if preferred backend is unavailable,
falls back to the next available one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Supported backend names in priority order
SUPPORTED_BACKENDS = ("apple_vision", "easyocr", "paddleocr")


def create_ocr_engine(backend: str = "auto") -> Any:
    """Create an OCR engine instance.

    Args:
        backend: Backend name ('easyocr', 'apple_vision', 'paddleocr', 'auto').
                 'auto' tries backends in priority order.

    Returns:
        An OCR engine implementing the OCREngine protocol.

    Raises:
        RuntimeError: If no OCR backend is available.
    """
    if backend == "auto":
        return _create_auto()

    factory_map = {
        "easyocr": _create_easyocr,
        "apple_vision": _create_apple_vision,
        "paddleocr": _create_paddleocr,
    }

    factory = factory_map.get(backend)
    if factory is None:
        raise ValueError(
            f"Unknown OCR backend: '{backend}'. "
            f"Supported: {', '.join(SUPPORTED_BACKENDS)}"
        )

    engine = factory()
    if engine is not None and engine.is_available():
        logger.info("OCR backend: %s", backend)
        return engine

    # Fallback to auto if specified backend is unavailable
    logger.warning(
        "Requested OCR backend '%s' is unavailable, falling back to auto",
        backend,
    )
    return _create_auto()


def _create_auto() -> Any:
    """Try backends in platform-dependent priority order.

    macOS: Apple Vision -> EasyOCR -> PaddleOCR
    Others: EasyOCR -> PaddleOCR -> Apple Vision
    """
    import platform

    if platform.system() == "Darwin":
        order = [
            ("apple_vision", _create_apple_vision),
            ("easyocr", _create_easyocr),
            ("paddleocr", _create_paddleocr),
        ]
    else:
        order = [
            ("easyocr", _create_easyocr),
            ("paddleocr", _create_paddleocr),
            ("apple_vision", _create_apple_vision),
        ]

    for name, factory in order:
        engine = factory()
        if engine is not None and engine.is_available():
            logger.info("OCR backend: %s (auto)", name)
            return engine

    raise RuntimeError(
        "No OCR backend available: all backends failed to initialize"
    )


def _create_easyocr() -> Any:
    """Create EasyOCR engine."""
    try:
        from docforge.adapters.easyocr_engine import EasyOCREngine
        return EasyOCREngine()
    except Exception:
        return None


def _create_paddleocr() -> Any:
    """Create PaddleOCR engine."""
    try:
        from docforge.adapters.paddle_ocr import PaddleOCREngine
        return PaddleOCREngine()
    except Exception:
        return None


def _create_apple_vision() -> Any:
    """Create Apple Vision OCR engine (macOS only)."""
    try:
        from docforge.adapters.apple_vision_engine import AppleVisionOCREngine
        return AppleVisionOCREngine()
    except Exception:
        return None
