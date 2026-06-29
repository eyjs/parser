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
import os
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_BACKENDS = ("apple_vision", "apple_vision_remote", "easyocr", "paddleocr")

# 강등 WARNING 중복 억제(엔진이 페이지마다 재생성되어도 로그 도배 방지).
# 같은 (선호→폴백) 조합은 프로세스당 한 번만 시끄럽게 경고한다.
_warned_degradations: set[tuple[str, str]] = set()


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
    """Try backends in platform-dependent priority order.

    선호(빠른) 백엔드가 미가용이라 느린 폴백(EasyOCR/PaddleOCR CPU)으로 떨어지면
    조용히 넘어가지 않고 WARNING 으로 강등을 알린다(silent degradation 방지).
    DOCFORGE_OCR_REQUIRE_PRIMARY=1 이면 폴백 대신 즉시 실패(fail-fast)한다.
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
            ("apple_vision_remote", _create_apple_vision_remote),
            ("easyocr", _create_easyocr),
            ("paddleocr", _create_paddleocr),
        ]

    primary_name, primary_factory = order[0]

    # 1) 선호 백엔드 우선 시도 (정상 경로)
    primary_engine = primary_factory()
    if primary_engine is not None and primary_engine.is_available():
        logger.info("OCR backend: %s (auto)", primary_name)
        return primary_engine

    # 2) 선호 백엔드 미가용 — 강등 경로
    hint = _degradation_hint(primary_name)
    require_primary = os.environ.get("DOCFORGE_OCR_REQUIRE_PRIMARY", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if require_primary:
        raise RuntimeError(
            f"OCR primary backend '{primary_name}' is unavailable and "
            f"DOCFORGE_OCR_REQUIRE_PRIMARY is set — refusing to silently degrade. {hint}"
        )

    for name, factory in order[1:]:
        engine = factory()
        if engine is not None and engine.is_available():
            key = (primary_name, name)
            if key not in _warned_degradations:
                _warned_degradations.add(key)
                logger.warning(
                    "OCR DEGRADED: preferred backend '%s' unavailable — using slow fallback "
                    "'%s' (typically 10x+ slower / lower quality). %s "
                    "Set DOCFORGE_OCR_REQUIRE_PRIMARY=1 to fail fast instead.",
                    primary_name, name, hint,
                )
            return engine

    logger.warning("No OCR backend available — OCR will be skipped for scanned pages")
    return _NullOCREngine()


def _degradation_hint(primary_name: str) -> str:
    """강등 원인 추정 + 조치 안내 (운영자가 바로 고칠 수 있도록)."""
    if primary_name == "apple_vision_remote":
        url = os.environ.get("DOCFORGE_OCR_SERVICE_URL", "http://host.docker.internal:5052")
        return (
            f"host Apple Vision OCR service ({url}) appears unreachable — "
            f"start it on the macOS host (LaunchAgent com.docforge.ocr-service)."
        )
    if primary_name == "apple_vision":
        return "Apple Vision framework is unavailable on this host (macOS + pyobjc required)."
    return ""


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
