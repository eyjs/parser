"""OCR engine factory — pluggable backend selection.

Creates the appropriate OCR engine based on configuration.
Supports graceful degradation: if preferred backend is unavailable,
falls back to the next available one.

Priority order (auto):
  macOS host:  Apple Vision (local) → EasyOCR → PaddleOCR
  Docker/Linux: Apple Vision (remote via host) → EasyOCR → PaddleOCR
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_BACKENDS = ("apple_vision", "apple_vision_remote", "easyocr", "paddleocr")


def create_ocr_engine(backend: str = "auto") -> Any:
    """Create an OCR engine instance.

    Args:
        backend: Backend name or 'auto'.
                 'auto' tries backends in platform-dependent priority order.

    Returns:
        An OCR engine implementing the OCREngine protocol.
    """
    if backend == "auto":
        return _create_auto()

    factory_map = {
        "apple_vision": _create_apple_vision,
        "apple_vision_remote": _create_apple_vision_remote,
        "easyocr": _create_easyocr,
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

    logger.warning(
        "Requested OCR backend '%s' is unavailable, falling back to auto",
        backend,
    )
    return _create_auto()


def _create_auto() -> Any:
    """Try backends in platform-dependent priority order."""
    import platform

    if platform.system() == "Darwin":
        order = [
            ("apple_vision", _create_apple_vision),
            ("easyocr", _create_easyocr),
            ("paddleocr", _create_paddleocr),
        ]
    else:
        order = [
            ("apple_vision_remote", _create_apple_vision_remote),
            ("easyocr", _create_easyocr),
            ("paddleocr", _create_paddleocr),
        ]

    for name, factory in order:
        engine = factory()
        if engine is not None and engine.is_available():
            logger.info("OCR backend: %s (auto)", name)
            return engine

    logger.warning("No OCR backend available — OCR will be skipped for scanned pages")
    return _NullOCREngine()


def _create_easyocr() -> Any:
    """Create EasyOCR engine (cross-platform CPU fallback)."""
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


def _create_apple_vision_remote() -> Any:
    """Create remote Apple Vision OCR engine (calls host via HTTP)."""
    try:
        from docforge.adapters.apple_vision_remote import AppleVisionRemoteEngine
        return AppleVisionRemoteEngine()
    except Exception:
        return None


class _NullOCREngine:
    """Fallback when no OCR backend is installed. Always returns empty results."""

    def recognize(self, image: object) -> list:
        return []

    def is_available(self) -> bool:
        return False
