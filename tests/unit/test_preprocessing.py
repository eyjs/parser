"""Tests for preprocessing pipeline — opencv_preprocessor, quality_gate, router."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from docforge.domain.enums import BlockType, SelectionReason
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import (
    BBox,
    FontInfo,
    ImageQualityPolicy,
    PreprocessingDecision,
    QualityGateResult,
    RawImage,
)
from docforge.adapters.opencv_preprocessor import OpenCVPreprocessor
from docforge.processing.quality_gate import quality_gate, _avg_confidence, _total_chars
from docforge.processing.preprocessing_router import process_scanned_page


def _make_raw_image(w: int = 200, h: int = 200, value: int = 200) -> RawImage:
    data = np.full((h, w), value, dtype=np.uint8)
    return RawImage(data=data, width=w, height=h, channels=1)


def _make_block(text: str, confidence: float = 0.9) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=100, y1=20),
        font=FontInfo(name="ocr", size=0.0, is_bold=False),
        block_type=BlockType.TEXT,
        confidence=confidence,
    )


class TestOpenCVPreprocessor:
    def test_preprocess_no_ops(self) -> None:
        """Skip_all decision should return identical-sized image."""
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image()
        decision = PreprocessingDecision()  # all False
        result = preprocessor.preprocess(image, decision)
        assert result.width == image.width
        assert result.height == image.height

    def test_preprocess_contrast(self) -> None:
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image(value=128)
        decision = PreprocessingDecision(apply_contrast=True)
        result = preprocessor.preprocess(image, decision)
        assert isinstance(result, RawImage)

    def test_preprocess_denoise(self) -> None:
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image()
        decision = PreprocessingDecision(apply_denoise=True)
        result = preprocessor.preprocess(image, decision)
        assert isinstance(result, RawImage)

    def test_preprocess_deskew(self) -> None:
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image()
        decision = PreprocessingDecision(apply_deskew=True, skew_angle=2.0)
        result = preprocessor.preprocess(image, decision)
        assert isinstance(result, RawImage)

    def test_preprocess_binarize(self) -> None:
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image(value=128)
        decision = PreprocessingDecision(apply_binarize=True)
        result = preprocessor.preprocess(image, decision)
        assert result.channels == 1

    def test_preprocess_upscale(self) -> None:
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image(w=100, h=100)
        decision = PreprocessingDecision(apply_upscale=True)
        result = preprocessor.preprocess(image, decision)
        assert result.width > image.width
        assert result.height > image.height

    def test_immutability_original_unchanged(self) -> None:
        preprocessor = OpenCVPreprocessor()
        image = _make_raw_image(value=128)
        original_data = image.data.copy()
        decision = PreprocessingDecision(apply_contrast=True, apply_denoise=True)
        preprocessor.preprocess(image, decision)
        np.testing.assert_array_equal(image.data, original_data)


class TestQualityGateHelpers:
    def test_avg_confidence_empty(self) -> None:
        assert _avg_confidence([]) == 0.0

    def test_avg_confidence(self) -> None:
        blocks = [_make_block("a", 0.8), _make_block("b", 0.9)]
        assert abs(_avg_confidence(blocks) - 0.85) < 0.01

    def test_total_chars(self) -> None:
        blocks = [_make_block("hello"), _make_block("world")]
        assert _total_chars(blocks) == 10


class TestQualityGate:
    def _mock_ocr(self, blocks_orig: list[TextBlock], blocks_prep: list[TextBlock]) -> MagicMock:
        engine = MagicMock()
        engine.recognize = MagicMock(side_effect=[blocks_orig, blocks_prep])
        return engine

    def test_original_default_when_similar(self) -> None:
        orig = [_make_block("hello world", 0.9)]
        prep = [_make_block("hello world", 0.91)]  # margin < 0.02
        engine = self._mock_ocr(orig, prep)
        policy = ImageQualityPolicy()
        result = quality_gate(
            _make_raw_image(), _make_raw_image(), engine, policy
        )
        assert result.reason == SelectionReason.ORIGINAL_DEFAULT
        assert result.use_preprocessed is False

    def test_prep_confidence_up(self) -> None:
        orig = [_make_block("hello", 0.7)]
        prep = [_make_block("hello", 0.8)]  # margin > 0.02
        engine = self._mock_ocr(orig, prep)
        policy = ImageQualityPolicy()
        result = quality_gate(
            _make_raw_image(), _make_raw_image(), engine, policy
        )
        assert result.reason == SelectionReason.PREP_CONFIDENCE_UP
        assert result.use_preprocessed is True

    def test_prep_char_loss_rejected(self) -> None:
        orig = [_make_block("hello world long text", 0.9)]
        prep = [_make_block("he", 0.95)]  # much less text
        engine = self._mock_ocr(orig, prep)
        policy = ImageQualityPolicy()
        result = quality_gate(
            _make_raw_image(), _make_raw_image(), engine, policy
        )
        assert result.reason == SelectionReason.PREP_CHAR_LOSS
        assert result.use_preprocessed is False

    def test_prep_rescued_empty(self) -> None:
        orig: list[TextBlock] = []  # no text from original
        prep = [_make_block("recovered text", 0.8)]
        engine = self._mock_ocr(orig, prep)
        policy = ImageQualityPolicy()
        result = quality_gate(
            _make_raw_image(), _make_raw_image(), engine, policy
        )
        assert result.reason == SelectionReason.PREP_RESCUED_EMPTY
        assert result.use_preprocessed is True


class TestPreprocessingRouter:
    def test_clean_image_skips_preprocessing(self) -> None:
        """Clean image should OCR once, no preprocessing."""
        # Create image with good contrast (dark text on white background)
        data = np.full((600, 400), 240, dtype=np.uint8)
        # Add dark text regions for contrast
        for i in range(8):
            y = 50 + i * 60
            data[y:y + 40, 50:350] = 30
        image = RawImage(data=data, width=400, height=600, channels=1)

        ocr_engine = MagicMock()
        ocr_engine.recognize.return_value = [_make_block("test", 0.95)]
        preprocessor = MagicMock()
        # Use relaxed policy so our synthetic image passes all checks
        policy = ImageQualityPolicy(max_bg_nonuniformity=0.9)

        blocks, decision, gate = process_scanned_page(
            image, ocr_engine, preprocessor, policy
        )
        # Should have called OCR once
        assert ocr_engine.recognize.call_count == 1
        assert gate is None  # no gate comparison
        assert len(blocks) == 1
        # Preprocessor should not have been called
        preprocessor.preprocess.assert_not_called()

    def test_preprocessing_failure_falls_back(self) -> None:
        """Preprocessing exception should fallback to original."""
        # Create image that triggers preprocessing (low contrast)
        data = np.full((200, 200), 128, dtype=np.uint8)
        data[:100, :] = 125  # very low contrast
        image = RawImage(data=data, width=200, height=200, channels=1)

        ocr_engine = MagicMock()
        ocr_engine.recognize.return_value = [_make_block("fallback", 0.8)]
        preprocessor = MagicMock()
        preprocessor.preprocess.side_effect = RuntimeError("OpenCV crash")

        policy = ImageQualityPolicy(
            min_contrast_ratio=0.9,  # Force contrast preprocessing
        )

        blocks, decision, gate = process_scanned_page(
            image, ocr_engine, preprocessor, policy
        )
        assert gate is not None
        assert gate.reason == SelectionReason.PREPROCESSING_FAILED
        assert len(blocks) == 1
