"""Tests for improved line merger — insurance/legal document patterns."""

from __future__ import annotations

import pytest

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.line_merger import merge_lines


def _block(text: str, y0: float = 0.0, y1: float = 10.0, x0: float = 0.0) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=500.0, y1=y1),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


class TestBracketContinuation:
    """Brackets split across lines should merge."""

    def test_open_paren_merges(self) -> None:
        blocks = [
            _block("보험금 지급사유가 발생한 때(", y0=0, y1=10),
            _block("이하 \"사고\"라 한다)", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 1
        assert "발생한 때(" in result[0].text
        assert "한다)" in result[0].text

    def test_close_paren_start_merges(self) -> None:
        blocks = [
            _block("보험계약 체결시 약관 사본 제공", y0=0, y1=10),
            _block(")에 의한 보험금 지급", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 1


class TestCommaContinuation:
    """Lines ending with comma should merge with next."""

    def test_comma_end_merges(self) -> None:
        blocks = [
            _block("보험금, 해지환급금,", y0=0, y1=10),
            _block("만기환급금을 지급합니다.", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 1
        assert "보험금, 해지환급금," in result[0].text


class TestDashContinuation:
    """Lines ending with dash should merge."""

    def test_dash_end_merges(self) -> None:
        blocks = [
            _block("보험금 지급 -", y0=0, y1=10),
            _block("해당 사항 없음", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 1


class TestAmountDateContinuation:
    """Lines ending with amounts or dates should not split prematurely."""

    def test_amount_merges(self) -> None:
        blocks = [
            _block("보험가입금액 100,000원", y0=0, y1=10),
            _block("이상인 경우에 한합니다", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 1


class TestStructureSplitStillWorks:
    """Structural patterns should still split despite merge improvements."""

    def test_clause_still_splits(self) -> None:
        blocks = [
            _block("보험금을 지급합니다.", y0=0, y1=10),
            _block("① 피보험자가 사망한 경우", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 2

    def test_heading_still_splits(self) -> None:
        blocks = [
            _block("보험금을 지급합니다.", y0=0, y1=10),
            _block("제3조 보험금의 지급사유", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 2


class TestKoreanEnglishMixed:
    """Korean-English mixed text spacing."""

    def test_english_after_korean_gets_space(self) -> None:
        blocks = [
            _block("보험약관의 PDF", y0=0, y1=10),
            _block("version 확인", y0=12, y1=22),
        ]
        result = merge_lines(blocks, 10.0, 5.0, ParserConfig())
        assert len(result) == 1
        # Should have space between PDF and version
        assert "PDF version" in result[0].text
