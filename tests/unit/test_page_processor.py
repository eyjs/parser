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
