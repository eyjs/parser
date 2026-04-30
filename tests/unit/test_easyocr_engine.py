"""Tests for EasyOCR engine adapter."""

from unittest.mock import MagicMock, patch

import numpy as np

from docforge.adapters.easyocr_engine import EasyOCREngine
from docforge.domain.enums import BlockType


def _fake_image() -> np.ndarray:
    """Lightweight stand-in image — adapter accepts ndarray directly."""
    return np.zeros((10, 10, 3), dtype=np.uint8)


class TestEasyOCREngine:
    """Test EasyOCR engine adapter."""

    def test_is_available_when_installed(self) -> None:
        engine = EasyOCREngine()
        assert isinstance(engine.is_available(), bool)

    @patch("docforge.adapters.easyocr_engine._check_availability", return_value=False)
    def test_recognize_returns_empty_when_unavailable(self, mock_check: MagicMock) -> None:
        engine = EasyOCREngine()
        result = engine.recognize(MagicMock())
        assert result == []

    @patch("docforge.adapters.easyocr_engine._get_reader")
    @patch("docforge.adapters.easyocr_engine._check_availability", return_value=True)
    def test_recognize_parses_results(self, mock_check: MagicMock, mock_reader: MagicMock) -> None:
        mock_ocr = MagicMock()
        mock_ocr.readtext.return_value = [
            ([[10, 20], [100, 20], [100, 40], [10, 40]], "보험약관", 0.95),
            ([[10, 50], [200, 50], [200, 70], [10, 70]], "제1조 목적", 0.88),
        ]
        mock_reader.return_value = mock_ocr

        engine = EasyOCREngine()
        blocks = engine.recognize(_fake_image())

        assert len(blocks) == 2
        assert blocks[0].text == "보험약관"
        assert blocks[0].confidence == 0.95
        assert blocks[0].block_type == BlockType.TEXT
        assert blocks[0].bbox.x0 == 10
        assert blocks[0].bbox.y0 == 20
        assert blocks[1].text == "제1조 목적"

    @patch("docforge.adapters.easyocr_engine._get_reader")
    @patch("docforge.adapters.easyocr_engine._check_availability", return_value=True)
    def test_recognize_skips_empty_text(self, mock_check: MagicMock, mock_reader: MagicMock) -> None:
        mock_ocr = MagicMock()
        mock_ocr.readtext.return_value = [
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "", 0.5),
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "  ", 0.5),
            ([[10, 20], [100, 20], [100, 40], [10, 40]], "유효", 0.9),
        ]
        mock_reader.return_value = mock_ocr

        engine = EasyOCREngine()
        blocks = engine.recognize(_fake_image())

        assert len(blocks) == 1
        assert blocks[0].text == "유효"

    @patch("docforge.adapters.easyocr_engine._get_reader")
    @patch("docforge.adapters.easyocr_engine._check_availability", return_value=True)
    def test_recognize_handles_exception(self, mock_check: MagicMock, mock_reader: MagicMock) -> None:
        mock_ocr = MagicMock()
        mock_ocr.readtext.side_effect = RuntimeError("OCR failed")
        mock_reader.return_value = mock_ocr

        engine = EasyOCREngine()
        blocks = engine.recognize(_fake_image())

        assert blocks == []
