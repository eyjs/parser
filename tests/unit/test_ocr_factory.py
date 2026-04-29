"""Tests for OCR engine factory."""

from __future__ import annotations

import pytest

from docforge.usecases.ocr_factory import create_ocr_engine, SUPPORTED_BACKENDS


class TestOCRFactory:
    def test_auto_returns_engine(self) -> None:
        engine = create_ocr_engine("auto")
        assert engine is not None
        assert hasattr(engine, "is_available")
        assert hasattr(engine, "recognize")

    def test_easyocr_returns_engine(self) -> None:
        engine = create_ocr_engine("easyocr")
        assert engine is not None

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown OCR backend"):
            create_ocr_engine("nonexistent")

    def test_supported_backends_list(self) -> None:
        assert "easyocr" in SUPPORTED_BACKENDS
        assert "apple_vision" in SUPPORTED_BACKENDS
        assert "paddleocr" in SUPPORTED_BACKENDS

    def test_apple_vision_graceful_on_non_macos(self) -> None:
        """Apple Vision should not crash on non-macOS."""
        engine = create_ocr_engine("apple_vision")
        # Should fallback to auto/easyocr on Windows
        assert engine is not None
