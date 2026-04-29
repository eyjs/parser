"""Tests for OCR engine factory."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from docforge.usecases.ocr_factory import create_ocr_engine, SUPPORTED_BACKENDS


class TestOCRFactory:
    def test_auto_returns_engine_or_raises(self) -> None:
        """Auto should return a working engine or raise RuntimeError."""
        try:
            engine = create_ocr_engine("auto")
            assert engine is not None
            assert hasattr(engine, "is_available")
            assert hasattr(engine, "recognize")
        except RuntimeError as exc:
            assert "No OCR backend available" in str(exc)

    def test_easyocr_returns_engine_or_falls_back(self) -> None:
        """EasyOCR request returns engine or falls back to auto."""
        try:
            engine = create_ocr_engine("easyocr")
            assert engine is not None
        except RuntimeError:
            pass  # All backends unavailable is acceptable

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown OCR backend"):
            create_ocr_engine("nonexistent")

    def test_supported_backends_list(self) -> None:
        assert "easyocr" in SUPPORTED_BACKENDS
        assert "apple_vision" in SUPPORTED_BACKENDS
        assert "paddleocr" in SUPPORTED_BACKENDS

    def test_apple_vision_graceful_on_non_macos(self) -> None:
        """Apple Vision should not crash on non-macOS."""
        try:
            engine = create_ocr_engine("apple_vision")
            assert engine is not None
        except RuntimeError:
            pass  # All backends unavailable is acceptable

    def test_all_backends_fail_raises_runtime_error(self) -> None:
        """When all backends fail, RuntimeError should be raised."""
        with patch(
            "docforge.usecases.ocr_factory._create_easyocr", return_value=None
        ), patch(
            "docforge.usecases.ocr_factory._create_paddleocr", return_value=None
        ), patch(
            "docforge.usecases.ocr_factory._create_apple_vision", return_value=None
        ):
            with pytest.raises(RuntimeError, match="No OCR backend available"):
                create_ocr_engine("auto")

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

    def test_auto_windows_prefers_easyocr(self) -> None:
        """On Windows, EasyOCR should be tried first."""
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True

        with patch("platform.system", return_value="Windows"), patch(
            "docforge.usecases.ocr_factory._create_easyocr",
            return_value=mock_engine,
        ):
            engine = create_ocr_engine("auto")
            assert engine is mock_engine
