"""Tests for COVER and TOC page classification (Phase B-3)."""

from __future__ import annotations

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.page_classifier import (
    classify_page_with_blocks,
    is_cover_page,
    is_toc_page,
)


def _block(text: str, x0: float, y0: float, x1: float, y1: float, size: float = 12.0) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        font=FontInfo(name="NanumGothic", size=size, is_bold=False),
        block_type=BlockType.TEXT,
    )


class TestCoverHeuristic:
    def test_cover_page_detected_with_centered_large_font(self) -> None:
        # Page width 600pt, blocks centered around x=300, large font
        blocks = [
            _block("보험약관", 200, 100, 400, 130, size=24.0),
            _block("OO생명", 220, 200, 380, 220, size=18.0),
            _block("2026년 4월", 240, 700, 360, 720, size=16.0),
        ]
        assert is_cover_page(page_idx=0, blocks=blocks, page_width=600, page_height=800)

    def test_cover_rejected_when_too_many_blocks(self) -> None:
        blocks = [
            _block(f"line {i}", 200, 100 + i * 20, 400, 120 + i * 20, size=16.0)
            for i in range(15)
        ]
        assert not is_cover_page(0, blocks, 600, 800)

    def test_cover_rejected_when_small_font(self) -> None:
        blocks = [
            _block("title", 200, 100, 400, 120, size=10.0),
            _block("subtitle", 220, 200, 380, 220, size=10.0),
        ]
        assert not is_cover_page(0, blocks, 600, 800)

    def test_cover_rejected_when_late_page(self) -> None:
        blocks = [
            _block("title", 200, 100, 400, 130, size=24.0),
        ]
        assert not is_cover_page(page_idx=10, blocks=blocks, page_width=600, page_height=800)

    def test_cover_rejected_when_off_center(self) -> None:
        # Blocks all left-aligned
        blocks = [
            _block("title", 0, 100, 100, 130, size=24.0),
            _block("subtitle", 0, 200, 100, 220, size=18.0),
        ]
        assert not is_cover_page(0, blocks, 600, 800)


class TestTOCHeuristic:
    def test_toc_keyword_korean(self) -> None:
        blocks = [_block("목 차", 100, 50, 200, 80)]
        assert is_toc_page(blocks, "목 차\n")

    def test_toc_keyword_english(self) -> None:
        blocks = [_block("Contents", 100, 50, 200, 80)]
        assert is_toc_page(blocks, "Contents\n제1장 ... 3\n")

    def test_toc_short_rows_with_right_numbers(self) -> None:
        blocks = [
            _block("제1장 총칙 3", 50, 100, 300, 120),
            _block("제2장 보험금 10", 50, 130, 300, 150),
            _block("제3장 보험료 20", 50, 160, 300, 180),
            _block("제4장 해약 30", 50, 190, 300, 210),
            _block("제5장 분쟁 40", 50, 220, 300, 240),
            _block("제6장 기타 50", 50, 250, 300, 270),
        ]
        assert is_toc_page(blocks, "")

    def test_toc_rejected_for_long_paragraphs(self) -> None:
        long_text = "본문 " * 40
        blocks = [
            _block(long_text, 50, 100 + i * 20, 500, 120 + i * 20)
            for i in range(8)
        ]
        assert not is_toc_page(blocks, "")

    def test_toc_nav_link_not_false_positive(self) -> None:
        long_text = "보험금 지급 사유 및 제한 사항에 대한 상세 내용입니다"
        blocks = [
            _block(long_text, 50, 100 + i * 20, 500, 120 + i * 20)
            for i in range(8)
        ]
        raw = "☞ 목차로 돌아가기\n" + long_text
        assert not is_toc_page(blocks, raw)


class TestClassifyPageWithBlocks:
    def test_cover_routes_to_cover(self) -> None:
        config = ParserConfig()
        blocks = [
            _block("보험약관", 200, 100, 400, 130, size=24.0),
            _block("2026", 250, 700, 350, 720, size=16.0),
        ]
        assert classify_page_with_blocks(
            page_idx=0,
            char_count=20,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="보험약관\n2026\n",
            blocks=blocks,
            page_width=600,
            page_height=800,
            config=config,
        ) == PageType.COVER

    def test_toc_routes_to_toc(self) -> None:
        config = ParserConfig()
        blocks = [_block("목 차", 100, 50, 200, 80)]
        assert classify_page_with_blocks(
            page_idx=3,
            char_count=200,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="목 차\n제1장 ... 3\n",
            blocks=blocks,
            page_width=600,
            page_height=800,
            config=config,
        ) == PageType.TOC

    def test_body_falls_back_to_legacy(self) -> None:
        config = ParserConfig()
        result = classify_page_with_blocks(
            page_idx=10,
            char_count=500,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="일반 본문 내용",
            blocks=[],
            page_width=600,
            page_height=800,
            config=config,
        )
        assert result == PageType.DIGITAL

    def test_legacy_noise_still_noise(self) -> None:
        config = ParserConfig()
        result = classify_page_with_blocks(
            page_idx=2,
            char_count=2,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="",
            blocks=[],
            page_width=600,
            page_height=800,
            config=config,
        )
        assert result == PageType.NOISE
