"""Tests for PageProcessor (P1-3).

Focus on the dependency-injection contract and the internal classify/merge
helper, which is the bulk of the per-page logic that benefits from
isolation. The full ``process()`` method is exercised via the existing
end-to-end tests; here we verify the seams that make the new class
substitutable and configurable.
"""

from __future__ import annotations

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.domain_profiles import (
    EnglishAcademicProfile,
    KoreanLegalProfile,
)
from docforge.processing.noise_detector import LearnedPatterns
from docforge.usecases.page_processor import PageProcessor, PageResult


def _bbox() -> BBox:
    return BBox(0, 0, 100, 20)


def _block(text: str, size: float = 10.0, bold: bool = False) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=_bbox(),
        font=FontInfo(name="x", size=size, is_bold=bold),
    )


def _make_processor(profile=None, config: ParserConfig | None = None) -> PageProcessor:
    return PageProcessor(
        config=config or ParserConfig(),
        llm_engine=None,
        morpheme_analyzer=None,
        preprocessing_available=False,
        domain_profile=profile or KoreanLegalProfile(),
        avg_font_size=10.0,
        avg_line_gap=5.0,
        patterns=LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        ),
        use_ocr=False,
    )


class TestPageProcessorInit:
    """Construction must accept all required dependencies."""

    def test_construct_default(self) -> None:
        proc = _make_processor()
        assert isinstance(proc, PageProcessor)

    def test_stores_domain_profile(self) -> None:
        profile = EnglishAcademicProfile()
        proc = _make_processor(profile=profile)
        assert proc._domain_profile is profile  # type: ignore[attr-defined]


class TestClassifyAndMerge:
    """``_classify_and_merge`` must use the injected domain profile."""

    def test_korean_profile_recognizes_jang(self) -> None:
        proc = _make_processor(profile=KoreanLegalProfile())
        blocks = [_block("제2장 보험금")]
        result = proc._classify_and_merge(blocks, PageType.DIGITAL)
        assert any(
            b.block_type == BlockType.HEADING and b.heading_level == 2
            for b in result
        )

    def test_english_profile_recognizes_chapter(self) -> None:
        proc = _make_processor(profile=EnglishAcademicProfile())
        blocks = [_block("Chapter 1 Introduction")]
        result = proc._classify_and_merge(blocks, PageType.DIGITAL)
        assert any(
            b.block_type == BlockType.HEADING and b.heading_level == 1
            for b in result
        )

    def test_korean_profile_does_not_match_chapter(self) -> None:
        proc = _make_processor(profile=KoreanLegalProfile())
        blocks = [_block("Chapter 1 Introduction")]
        result = proc._classify_and_merge(blocks, PageType.DIGITAL)
        assert all(b.block_type == BlockType.TEXT for b in result)

    def test_plain_text_unchanged(self) -> None:
        proc = _make_processor()
        blocks = [_block("일반 본문")]
        result = proc._classify_and_merge(blocks, PageType.DIGITAL)
        assert result[0].text == "일반 본문"
        assert result[0].block_type == BlockType.TEXT


class TestPageResult:
    """PageResult is a frozen dataclass — confirm immutability."""

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        result = PageResult(
            page_content=None, tables_info=None,
            noise=__import__("docforge.domain.models", fromlist=["NoiseStats"]).NoiseStats(),
            is_toc=False, ocr_used=False,
        )
        try:
            result.is_toc = True  # type: ignore[misc]
        except FrozenInstanceError:
            return
        raise AssertionError("PageResult should be frozen")


# ---- Phase 2: New helper method tests ----


class TestApplyHeadingDetector:
    """_apply_heading_detector should gracefully enhance OCR blocks."""

    def test_detects_heading_in_ocr_blocks(self) -> None:
        proc = _make_processor()
        # OCR blocks have font_size=0.0
        ocr_block = TextBlock(
            text="제1조 보험금의 지급",
            bbox=BBox(50, 50, 400, 70),
            font=FontInfo(name="AppleVision", size=0.0, is_bold=False),
            block_type=BlockType.TEXT,
        )
        result = proc._apply_heading_detector([ocr_block], [], 800)
        assert result[0].block_type == BlockType.HEADING

    def test_preserves_digital_blocks(self) -> None:
        proc = _make_processor()
        digital_block = TextBlock(
            text="제1조 보험금의 지급",
            bbox=BBox(50, 50, 400, 70),
            font=FontInfo(name="Nanum", size=12.0, is_bold=False),
            block_type=BlockType.TEXT,
        )
        result = proc._apply_heading_detector([digital_block], [], 800)
        # Should pass through unchanged (font_size > 0)
        assert result[0].block_type == BlockType.TEXT

    def test_graceful_on_empty(self) -> None:
        proc = _make_processor()
        result = proc._apply_heading_detector([], [], 800)
        assert result == []


class TestApplyOCRMultipass:
    """_apply_ocr_multipass should split by confidence and fall back gracefully."""

    def test_all_high_confidence_unchanged(self) -> None:
        proc = _make_processor()
        blocks = [
            TextBlock(
                text="good text",
                bbox=BBox(0, 0, 100, 20),
                font=FontInfo(name="AV", size=0.0, is_bold=False),
                confidence=0.95,
            ),
        ]
        # No VLM engine -> should return blocks unchanged
        from unittest.mock import MagicMock
        reader = MagicMock()
        result = proc._apply_ocr_multipass(blocks, reader, None, 0, 100, 100)
        assert len(result) == 1
        assert result[0].text == "good text"

    def test_empty_blocks_unchanged(self) -> None:
        proc = _make_processor()
        from unittest.mock import MagicMock
        reader = MagicMock()
        result = proc._apply_ocr_multipass([], reader, None, 0, 100, 100)
        assert result == []


class TestBuildCaptionerContext:
    """_build_captioner_context should extract hints from routing decisions."""

    def test_empty_records(self) -> None:
        bt, ctx = PageProcessor._build_captioner_context([])
        assert bt == {}
        assert ctx == {}

    def test_chart_decision_extracts_hints(self) -> None:
        from docforge.domain.models import NormalizedBlock
        from docforge.processing.layout_router import RoutingDecision

        block = NormalizedBlock(
            block_id="chart1",
            bbox=BBox(0, 0, 100, 100),
            block_type=BlockType.CHART,
            confidence=0.9,
            text="Sales Revenue 2024",
            source="test",
            page_num=1,
        )
        decision = RoutingDecision(
            block=block,
            action="vlm_chart",
            confidence=0.9,
            rule_matched="chart->vlm_chart",
        )
        bt, ctx = PageProcessor._build_captioner_context([decision])
        assert bt == {"chart1": "chart"}
        assert ctx == {"chart1": "Sales Revenue 2024"}

    def test_figure_decision(self) -> None:
        from docforge.domain.models import NormalizedBlock
        from docforge.processing.layout_router import RoutingDecision

        block = NormalizedBlock(
            block_id="fig1",
            bbox=BBox(0, 0, 100, 100),
            block_type=BlockType.FIGURE,
            confidence=0.8,
            source="test",
            page_num=1,
        )
        decision = RoutingDecision(
            block=block,
            action="vlm_caption",
            confidence=0.8,
            rule_matched="figure->vlm_caption",
        )
        bt, ctx = PageProcessor._build_captioner_context([decision])
        assert bt == {"fig1": "figure"}
        assert ctx == {}  # No context text for figures
