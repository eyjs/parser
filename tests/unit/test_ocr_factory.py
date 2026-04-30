"""Tests for OCR engine factory."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from docforge.usecases.ocr_factory import create_ocr_engine, SUPPORTED_BACKENDS


class TestOCRFactory:
    def test_auto_returns_engine(self) -> None:
        """Auto should always return an engine (possibly null)."""
        engine = create_ocr_engine("auto")
        assert engine is not None
        assert hasattr(engine, "is_available")
        assert hasattr(engine, "recognize")

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown OCR backend"):
            create_ocr_engine("nonexistent")

    def test_supported_backends_list(self) -> None:
        assert "apple_vision" in SUPPORTED_BACKENDS
        assert "paddleocr" in SUPPORTED_BACKENDS

    def test_apple_vision_graceful_on_non_macos(self) -> None:
        """Apple Vision should not crash on non-macOS."""
        engine = create_ocr_engine("apple_vision")
        assert engine is not None

    def test_all_backends_fail_returns_null_engine(self) -> None:
        """When all backends fail, a null engine with is_available=False is returned."""
        with patch(
            "docforge.usecases.ocr_factory._create_paddleocr", return_value=None
        ), patch(
            "docforge.usecases.ocr_factory._create_apple_vision", return_value=None
        ):
            engine = create_ocr_engine("auto")
            assert engine.is_available() is False
            assert engine.recognize(None) == []

    def test_auto_macos_prefers_apple_vision(self) -> None:
        """On macOS, Apple Vision should be tried first."""
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True

        with patch("platform.system", return_value="Darwin"), patch(
            "docforge.usecases.ocr_factory._create_apple_vision",
            return_value=mock_engine,
        ):
            engine = create_ocr_engine("auto")
            assert engine is mock_engine

    def test_auto_windows_prefers_paddleocr(self) -> None:
        """On Windows, PaddleOCR should be tried first."""
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True

        with patch("platform.system", return_value="Windows"), patch(
            "docforge.usecases.ocr_factory._create_paddleocr",
            return_value=mock_engine,
        ):
            engine = create_ocr_engine("auto")
            assert engine is mock_engine
