"""Tests for DocumentIntelligence -- document pre-parse analysis."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from docforge.domain.value_objects import DocumentStrategyReport, PageStrategy
from docforge.processing.document_intelligence import (
    DocumentIntelligence,
    _build_fallback_chain,
    _estimate_complexity,
    _estimate_table_count,
    _quick_classify,
)

# PUA characters for garbled text simulation
_PUA_50 = "".join(chr(0xE000 + i) for i in range(50))


# ---------------------------------------------------------------------------
# Helpers: fitz mock builders
# ---------------------------------------------------------------------------


def _make_fitz_page(
    raw_text: str = "정상 텍스트 내용입니다.",
    width: float = 595.0,
    height: float = 842.0,
    image_count: int = 0,
    blocks: list[dict] | None = None,
) -> MagicMock:
    """Build a mock fitz.Page where get_text('dict') returns custom blocks."""
    page = MagicMock()
    text_dict = {"blocks": blocks or []}

    def get_text_side_effect(fmt: str = "") -> Any:
        if fmt == "dict":
            return text_dict
        return raw_text

    page.get_text = MagicMock(side_effect=get_text_side_effect)
    page.rect = MagicMock()
    page.rect.width = width
    page.rect.height = height
    page.get_images = MagicMock(return_value=[MagicMock()] * image_count)
    return page


def _make_fitz_doc(pages: list[MagicMock]) -> MagicMock:
    """Build a mock fitz.Document with given pages."""
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=len(pages))
    doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
    return doc


# ---------------------------------------------------------------------------
# Tests: DocumentIntelligence.analyze
# ---------------------------------------------------------------------------


class TestDocumentIntelligenceAnalyze:
    def setup_method(self) -> None:
        self.intel = DocumentIntelligence()

    def test_digital_page_returns_pymupdf_text_strategy(self) -> None:
        # Arrange -- high text density, no garbling, no images
        long_text = "이 문서는 디지털 텍스트입니다. " * 100
        page = _make_fitz_page(raw_text=long_text, image_count=0)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].primary_method == "pymupdf_text"

    def test_scanned_page_returns_apple_vision_ocr_strategy(self) -> None:
        # Arrange -- very little text + has images (text_density < 0.05)
        # 595 * 842 = ~500,990 area; need text_density < 0.05 -> < 25049 chars
        page = _make_fitz_page(
            raw_text="",  # empty text -> density = 0.0 < 0.05
            image_count=2,
        )
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].primary_method == "apple_vision_ocr"

    def test_garbled_page_returns_apple_vision_ocr_strategy(self) -> None:
        # Arrange -- garbled_ratio > 0.15 threshold (all PUA chars)
        garbled_text = _PUA_50  # 50 PUA chars -- garbled_ratio = 1.0
        page = _make_fitz_page(raw_text=garbled_text, image_count=0)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].primary_method == "apple_vision_ocr"

    def test_noise_page_returns_skip_strategy(self) -> None:
        # Arrange -- empty text and no images -> "skip" by _quick_classify
        page = _make_fitz_page(raw_text="", image_count=0)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].primary_method == "skip"

    def test_toc_page_returns_skip_strategy(self) -> None:
        # Arrange -- TOC keyword triggers skip
        toc_text = "목차\n제1조 .............1\n제2조 .............2\n제3조 .............3"
        page = _make_fitz_page(raw_text=toc_text, image_count=0)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].primary_method == "skip"

    def test_report_is_document_strategy_report(self) -> None:
        # Arrange
        page = _make_fitz_page(raw_text="정상적인 디지털 문서 텍스트입니다. " * 50)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert isinstance(report, DocumentStrategyReport)

    def test_report_total_pages_matches_document(self) -> None:
        # Arrange
        pages = [_make_fitz_page(raw_text="텍스트 " * 50) for _ in range(3)]
        doc = _make_fitz_doc(pages)
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.total_pages == 3
        assert len(report.pages) == 3

    def test_report_generated_at_is_iso_string(self) -> None:
        # Arrange
        page = _make_fitz_page(raw_text="텍스트")
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert isinstance(report.generated_at, str)
        assert "T" in report.generated_at or "-" in report.generated_at

    def test_strategy_counts_sum_equals_total_pages(self) -> None:
        # Arrange
        pages = [
            _make_fitz_page(raw_text="정상 텍스트 내용입니다. " * 50),
            _make_fitz_page(raw_text="", image_count=2),
        ]
        doc = _make_fitz_doc(pages)
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert sum(report.strategy_counts.values()) == report.total_pages

    def test_page_strategy_has_correct_page_index(self) -> None:
        # Arrange
        pages = [
            _make_fitz_page(raw_text="텍스트 " * 50),
            _make_fitz_page(raw_text="텍스트 " * 50),
            _make_fitz_page(raw_text="텍스트 " * 50),
        ]
        doc = _make_fitz_doc(pages)
        # Act
        report = self.intel.analyze(doc)
        # Assert
        for i, strategy in enumerate(report.pages):
            assert strategy.page_index == i

    def test_surya_needed_when_many_images(self) -> None:
        # Arrange -- > 3 images triggers surya_needed
        page = _make_fitz_page(
            raw_text="텍스트 " * 50,
            image_count=4,
        )
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].surya_needed is True
        assert report.surya_page_count == 1

    def test_surya_not_needed_for_simple_page(self) -> None:
        # Arrange -- no images, no tables
        page = _make_fitz_page(
            raw_text="단순한 텍스트만 있는 페이지입니다. " * 30,
            image_count=0,
        )
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].surya_needed is False

    def test_fallback_chain_for_pymupdf_text(self) -> None:
        # Arrange
        page = _make_fitz_page(raw_text="디지털 텍스트 " * 50)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        strategy = report.pages[0]
        if strategy.primary_method == "pymupdf_text":
            assert "apple_vision_ocr" in strategy.fallback_chain
            assert "vlm_full" in strategy.fallback_chain

    def test_fallback_chain_for_apple_vision_ocr(self) -> None:
        # Arrange -- scanned page (empty text + images)
        page = _make_fitz_page(raw_text="", image_count=2)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        strategy = report.pages[0]
        if strategy.primary_method == "apple_vision_ocr":
            assert "vlm_full" in strategy.fallback_chain
            assert "apple_vision_ocr" not in strategy.fallback_chain

    def test_skip_page_has_empty_fallback_chain(self) -> None:
        # Arrange
        page = _make_fitz_page(raw_text="", image_count=0)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert
        strategy = report.pages[0]
        if strategy.primary_method == "skip":
            assert strategy.fallback_chain == ()

    def test_page_exception_defaults_to_pymupdf_text(self) -> None:
        # Arrange -- page.get_text() raises exception
        page = MagicMock()
        page.get_text.side_effect = RuntimeError("fitz error")
        page.rect = MagicMock()
        page.rect.width = 595.0
        page.rect.height = 842.0
        page.get_images.side_effect = RuntimeError("fitz error")
        doc = _make_fitz_doc([page])
        # Act -- should not raise
        report = self.intel.analyze(doc)
        # Assert
        assert report.pages[0].primary_method == "pymupdf_text"

    def test_page_strategy_block_quality_threshold_default(self) -> None:
        # Arrange
        page = _make_fitz_page(raw_text="정상 텍스트 " * 50)
        doc = _make_fitz_doc([page])
        # Act
        report = self.intel.analyze(doc)
        # Assert -- default block_quality_threshold is 0.60
        assert report.pages[0].block_quality_threshold == 0.60


# ---------------------------------------------------------------------------
# Tests: _build_fallback_chain
# ---------------------------------------------------------------------------


class TestBuildFallbackChain:
    def test_pymupdf_text_has_apple_and_vlm(self) -> None:
        chain = _build_fallback_chain("pymupdf_text")
        assert chain == ("apple_vision_ocr", "vlm_full")

    def test_apple_vision_ocr_has_vlm_only(self) -> None:
        chain = _build_fallback_chain("apple_vision_ocr")
        assert chain == ("vlm_full",)

    def test_skip_has_empty_chain(self) -> None:
        chain = _build_fallback_chain("skip")
        assert chain == ()

    def test_vlm_full_has_empty_chain(self) -> None:
        chain = _build_fallback_chain("vlm_full")
        assert chain == ()

    def test_returns_tuple(self) -> None:
        chain = _build_fallback_chain("pymupdf_text")
        assert isinstance(chain, tuple)


# ---------------------------------------------------------------------------
# Tests: _estimate_complexity
# ---------------------------------------------------------------------------


class TestEstimateComplexity:
    def test_no_tables_no_images_is_simple(self) -> None:
        result = _estimate_complexity(table_count=0, image_count=0)
        assert result == "simple"

    def test_many_tables_only_is_table_heavy(self) -> None:
        # > 2 tables (threshold=2) and <= 3 images
        result = _estimate_complexity(table_count=3, image_count=0)
        assert result == "table_heavy"

    def test_many_images_only_is_image_heavy(self) -> None:
        # <= 2 tables and > 3 images (threshold=3)
        result = _estimate_complexity(table_count=0, image_count=4)
        assert result == "image_heavy"

    def test_both_tables_and_images_is_mixed(self) -> None:
        result = _estimate_complexity(table_count=3, image_count=4)
        assert result == "mixed"

    def test_boundary_tables_exactly_threshold_is_simple(self) -> None:
        # table_count=2 is NOT > 2 -> not table_heavy
        result = _estimate_complexity(table_count=2, image_count=0)
        assert result == "simple"

    def test_boundary_images_exactly_threshold_is_simple(self) -> None:
        # image_count=3 is NOT > 3 -> not image_heavy
        result = _estimate_complexity(table_count=0, image_count=3)
        assert result == "simple"


# ---------------------------------------------------------------------------
# Tests: _quick_classify
# ---------------------------------------------------------------------------


class TestQuickClassify:
    def test_empty_text_no_images_returns_skip(self) -> None:
        result = _quick_classify(raw_text="", text_density=0.0, image_count=0)
        assert result == "skip"

    def test_short_text_no_images_returns_skip(self) -> None:
        # less than 10 chars and no images -> skip
        result = _quick_classify(raw_text="ABC", text_density=0.1, image_count=0)
        assert result == "skip"

    def test_toc_keyword_returns_skip(self) -> None:
        result = _quick_classify(
            raw_text="목차\n제1장 .............1",
            text_density=0.1,
            image_count=0,
        )
        assert result == "skip"

    def test_contents_keyword_returns_skip(self) -> None:
        result = _quick_classify(
            raw_text="Contents\nChapter 1 ......... 1",
            text_density=0.1,
            image_count=0,
        )
        assert result == "skip"

    def test_normal_text_returns_normal(self) -> None:
        result = _quick_classify(
            raw_text="이 문서는 정상적인 내용입니다. 계약서 조항 1번입니다.",
            text_density=0.5,
            image_count=0,
        )
        assert result == "normal"

    def test_dot_pattern_toc_returns_skip(self) -> None:
        # Many lines with leader dots -- typical TOC
        toc_lines = "\n".join([
            f"제{i}조 내용 ......... {i}"
            for i in range(1, 8)
        ])
        result = _quick_classify(
            raw_text=toc_lines,
            text_density=0.1,
            image_count=0,
        )
        assert result == "skip"

    def test_short_text_with_images_is_normal(self) -> None:
        # < 10 chars but has images -> not skip
        result = _quick_classify(raw_text="ABC", text_density=0.01, image_count=1)
        assert result == "normal"


# ---------------------------------------------------------------------------
# Tests: _estimate_table_count
# ---------------------------------------------------------------------------


class TestEstimateTableCount:
    def test_empty_dict_returns_zero(self) -> None:
        result = _estimate_table_count({"blocks": []})
        assert result == 0

    def test_no_blocks_key_returns_zero(self) -> None:
        result = _estimate_table_count({})
        assert result == 0

    def test_image_block_type_skipped(self) -> None:
        # type=1 is image, should be skipped
        text_dict = {
            "blocks": [
                {"type": 1, "lines": []},
            ]
        }
        result = _estimate_table_count(text_dict)
        assert result == 0

    def test_multi_span_lines_indicate_table(self) -> None:
        # A block with 2+ lines, each having >= 3 spans -> table indicator
        block = {
            "type": 0,
            "lines": [
                {"spans": [{"text": "A"}, {"text": "B"}, {"text": "C"}]},
                {"spans": [{"text": "D"}, {"text": "E"}, {"text": "F"}]},
            ],
        }
        result = _estimate_table_count({"blocks": [block]})
        assert result == 1

    def test_single_span_lines_not_table(self) -> None:
        # Lines with < 3 spans -> not a table indicator
        block = {
            "type": 0,
            "lines": [
                {"spans": [{"text": "A"}]},
                {"spans": [{"text": "B"}]},
                {"spans": [{"text": "C"}]},
            ],
        }
        result = _estimate_table_count({"blocks": [block]})
        assert result == 0

    def test_single_line_block_not_table(self) -> None:
        # Only 1 line -> not a table (need >= 2 lines)
        block = {
            "type": 0,
            "lines": [
                {"spans": [{"text": "A"}, {"text": "B"}, {"text": "C"}]},
            ],
        }
        result = _estimate_table_count({"blocks": [block]})
        assert result == 0

    def test_multiple_table_blocks_counted(self) -> None:
        # Two blocks, each indicating a table
        table_block = {
            "type": 0,
            "lines": [
                {"spans": [{"text": "A"}, {"text": "B"}, {"text": "C"}]},
                {"spans": [{"text": "D"}, {"text": "E"}, {"text": "F"}]},
            ],
        }
        result = _estimate_table_count({"blocks": [table_block, table_block]})
        assert result == 2
