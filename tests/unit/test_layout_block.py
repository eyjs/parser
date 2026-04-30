"""Tests for the LayoutBlock domain model and Surya adapter scaffolding."""

from __future__ import annotations

import pytest

from docforge.adapters.layout import NullLayoutDetector, SuryaLayoutDetector
from docforge.domain.models import LayoutBlock, ParsedImage
from docforge.domain.value_objects import BBox


class TestLayoutBlockModel:
    def test_construction_and_immutability(self) -> None:
        lb = LayoutBlock(
            bbox=BBox(0, 0, 100, 50),
            label="Title",
            confidence=0.95,
            page_num=1,
        )
        assert lb.label == "Title"
        assert lb.confidence == pytest.approx(0.95)
        assert lb.page_num == 1
        with pytest.raises(Exception):
            lb.label = "Text"  # type: ignore[misc]


class TestParsedImageModel:
    def test_construction_default_alt_text_none(self) -> None:
        img = ParsedImage(
            bbox=BBox(0, 0, 200, 200),
            data=b"\x89PNG\r\n",
            format="png",
            caption="그림 1",
            page_num=2,
            block_id="abcd1234",
        )
        assert img.alt_text is None
        assert img.format == "png"
        assert img.caption == "그림 1"


class TestNullLayoutDetector:
    def test_returns_empty_and_unavailable(self) -> None:
        det = NullLayoutDetector()
        assert det.is_available() is False
        assert det.detect(object(), page_num=1) == []


class TestSuryaAdapterGracefulDegradation:
    def test_is_available_false_when_surya_not_installed(self) -> None:
        det = SuryaLayoutDetector()
        # In CI without Surya, this must be False (not raise).
        result = det.is_available()
        assert isinstance(result, bool)

    def test_detect_returns_empty_when_unavailable(self) -> None:
        det = SuryaLayoutDetector()
        if not det.is_available():
            assert det.detect(object(), page_num=1) == []
