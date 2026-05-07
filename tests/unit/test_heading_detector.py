"""Tests for ``heading_detector`` -- OCR page heading classification."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.heading_detector import detect_headings_ocr


def _ocr_block(
    text: str,
    x0: float = 50, y0: float = 100, x1: float = 500, y1: float = 120,
    block_type: BlockType = BlockType.TEXT,
) -> TextBlock:
    """Create a TextBlock with font_size=0.0 (OCR page)."""
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="AppleVision", size=0.0, is_bold=False),
        block_type=block_type,
    )


def _real_font_block(
    text: str,
    size: float = 12.0,
    block_type: BlockType = BlockType.TEXT,
) -> TextBlock:
    """Create a TextBlock with real font size (digital page)."""
    return TextBlock(
        text=text,
        bbox=BBox(50, 100, 500, 120),
        font=FontInfo(name="NanumGothic", size=size, is_bold=False),
        block_type=block_type,
    )


def _lb(label: str, x0: float, y0: float, x1: float, y1: float) -> LayoutBlock:
    return LayoutBlock(
        bbox=BBox(x0, y0, x1, y1),
        label=label,
        confidence=0.9,
        page_num=1,
    )


class TestDetectHeadingsOCR:
    def test_empty_blocks(self) -> None:
        assert detect_headings_ocr([], None, 800) == []

    def test_regex_je_jo(self) -> None:
        """'제N조' pattern -> HEADING, level 4."""
        blocks = [_ocr_block("제1조 보험금의 지급")]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 4

    def test_regex_je_jang(self) -> None:
        """'제N장' pattern -> HEADING, level 2."""
        blocks = [_ocr_block("제2장 보험금")]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 2

    def test_regex_je_pyeon(self) -> None:
        """'제N편' pattern -> HEADING, level 1."""
        blocks = [_ocr_block("제1편 일반사항")]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 1

    def test_regex_je_jeol(self) -> None:
        """'제N절' pattern -> HEADING, level 3."""
        blocks = [_ocr_block("제3절 보험금 청구")]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 3

    def test_long_text_non_heading(self) -> None:
        """Long body text should not be classified as heading."""
        long_text = (
            "피보험자가 보험기간 중에 질병으로 인하여 그 직접적인 치료를 "
            "목적으로 병원에 입원하여 치료를 받은 경우에는 입원의료비를 "
            "다음과 같이 하나의 질병당 5,000만원을 한도로 보상합니다."
        )
        blocks = [_ocr_block(long_text)]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.TEXT

    def test_surya_title_with_short_text(self) -> None:
        """Surya Title label + short text -> heading even without regex."""
        blocks = [_ocr_block("보험약관", x0=50, y0=50, x1=300, y1=80)]
        layouts = [_lb("Title", 50, 50, 300, 80)]
        result = detect_headings_ocr(blocks, layouts, 800)
        assert result[0].block_type == BlockType.HEADING

    def test_real_font_block_unchanged(self) -> None:
        """Blocks with font_size > 0.0 should pass through unchanged."""
        blocks = [_real_font_block("제1조 보험금")]
        result = detect_headings_ocr(blocks, None, 800)
        # Should NOT be changed to heading -- it has a real font size
        assert result[0].block_type == BlockType.TEXT
        assert result[0] is blocks[0]

    def test_arabic_numbering(self) -> None:
        """'1.' at start -> heading candidate."""
        blocks = [_ocr_block("1. 보험의 목적", y0=50)]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 3

    def test_korean_consonant_numbering(self) -> None:
        """'가.' at start -> heading candidate."""
        blocks = [_ocr_block("가. 계약의 체결", y0=50)]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 3

    def test_immutability_preserved(self) -> None:
        """Original blocks must not be mutated (frozen dataclass)."""
        blocks = [_ocr_block("제1조 보험금")]
        result = detect_headings_ocr(blocks, None, 800)
        # result should be new instances, originals unchanged
        assert result[0] is not blocks[0]
        assert blocks[0].block_type == BlockType.TEXT

    def test_multiple_blocks_mixed(self) -> None:
        """Mix of heading candidates and body text."""
        blocks = [
            _ocr_block("제1장 총칙", y0=50),
            _ocr_block("이 약관에서 사용하는 용어의 정의는 다음과 같습니다.", y0=200),
            _ocr_block("제1조 보험금의 지급", y0=300),
        ]
        result = detect_headings_ocr(blocks, None, 800)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 2  # 장
        assert result[1].block_type == BlockType.TEXT
        assert result[2].block_type == BlockType.HEADING
        assert result[2].heading_level == 4  # 조

    def test_no_layout_blocks(self) -> None:
        """Passing None for layout_blocks should not raise."""
        blocks = [_ocr_block("보통 텍스트")]
        result = detect_headings_ocr(blocks, None, 800)
        assert len(result) == 1
