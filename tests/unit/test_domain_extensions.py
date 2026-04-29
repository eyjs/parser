"""Tests for Phase 1 domain model extensions."""

from __future__ import annotations

import pytest

from docforge.domain.enums import SelectionReason
from docforge.domain.models import PageConfidence
from docforge.domain.value_objects import (
    ImageQualityPolicy,
    ImageQualityReport,
    PreprocessingDecision,
    QualityGateResult,
)


class TestImageQualityPolicy:
    def test_frozen(self) -> None:
        policy = ImageQualityPolicy()
        with pytest.raises(AttributeError):
            policy.min_dpi = 300.0  # type: ignore[misc]

    def test_defaults(self) -> None:
        policy = ImageQualityPolicy()
        assert policy.min_dpi == 200.0
        assert policy.max_skew_degrees == 0.5
        assert policy.confidence_margin == 0.02


class TestImageQualityReport:
    def test_frozen(self) -> None:
        report = ImageQualityReport(
            dpi_estimated=300.0,
            skew_angle=0.1,
            contrast_ratio=0.8,
            noise_score=0.1,
            background_uniformity=0.1,
        )
        with pytest.raises(AttributeError):
            report.dpi_estimated = 200.0  # type: ignore[misc]

    def test_is_clean_when_all_good(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=300.0,
            skew_angle=0.1,
            contrast_ratio=0.8,
            noise_score=0.1,
            background_uniformity=0.1,
        )
        assert report.is_clean(policy) is True

    def test_needs_upscale_low_dpi(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=150.0,
            skew_angle=0.0,
            contrast_ratio=0.8,
            noise_score=0.1,
            background_uniformity=0.1,
        )
        assert report.needs_upscale(policy) is True
        assert report.is_clean(policy) is False

    def test_dpi_negative_one_skips_upscale(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=-1.0,
            skew_angle=0.0,
            contrast_ratio=0.8,
            noise_score=0.1,
            background_uniformity=0.1,
        )
        assert report.needs_upscale(policy) is False

    def test_needs_deskew(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=300.0,
            skew_angle=1.5,
            contrast_ratio=0.8,
            noise_score=0.1,
            background_uniformity=0.1,
        )
        assert report.needs_deskew(policy) is True

    def test_needs_contrast(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=300.0,
            skew_angle=0.0,
            contrast_ratio=0.1,
            noise_score=0.1,
            background_uniformity=0.1,
        )
        assert report.needs_contrast(policy) is True

    def test_needs_denoise(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=300.0,
            skew_angle=0.0,
            contrast_ratio=0.8,
            noise_score=0.8,
            background_uniformity=0.1,
        )
        assert report.needs_denoise(policy) is True

    def test_needs_binarize(self) -> None:
        policy = ImageQualityPolicy()
        report = ImageQualityReport(
            dpi_estimated=300.0,
            skew_angle=0.0,
            contrast_ratio=0.8,
            noise_score=0.1,
            background_uniformity=0.8,
        )
        assert report.needs_binarize(policy) is True


class TestPreprocessingDecision:
    def test_skip_all_when_nothing_needed(self) -> None:
        decision = PreprocessingDecision()
        assert decision.skip_all is True

    def test_not_skip_when_upscale_needed(self) -> None:
        decision = PreprocessingDecision(apply_upscale=True)
        assert decision.skip_all is False

    def test_frozen(self) -> None:
        decision = PreprocessingDecision()
        with pytest.raises(AttributeError):
            decision.apply_upscale = True  # type: ignore[misc]


class TestQualityGateResult:
    def test_frozen(self) -> None:
        result = QualityGateResult(
            use_preprocessed=False,
            original_confidence=0.9,
            preprocessed_confidence=0.85,
            original_char_count=100,
            preprocessed_char_count=95,
            reason=SelectionReason.ORIGINAL_DEFAULT,
            reason_detail="test",
        )
        with pytest.raises(AttributeError):
            result.use_preprocessed = True  # type: ignore[misc]


class TestPageConfidence:
    def test_frozen(self) -> None:
        conf = PageConfidence(overall=0.95)
        with pytest.raises(AttributeError):
            conf.overall = 0.5  # type: ignore[misc]

    def test_defaults(self) -> None:
        conf = PageConfidence(overall=0.9)
        assert conf.ocr_confidence == 1.0
        assert conf.text_density == 1.0
        assert conf.structure_ratio == 1.0
        assert conf.preprocessing_applied is False


class TestSelectionReason:
    def test_all_reasons_exist(self) -> None:
        assert len(SelectionReason) == 6
        assert SelectionReason.ORIGINAL_DEFAULT is not None
        assert SelectionReason.PREP_CHAR_LOSS is not None
        assert SelectionReason.PREP_CONFIDENCE_UP is not None
        assert SelectionReason.PREP_CHAR_GAIN is not None
        assert SelectionReason.PREP_RESCUED_EMPTY is not None
        assert SelectionReason.PREPROCESSING_FAILED is not None
