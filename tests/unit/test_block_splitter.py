"""Unit tests for block_splitter module."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.block_splitter import split_heading_body, _try_split


def _make_block(text: str) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=200, y1=20),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


class TestTrySplit:
    def test_korean_letter_heading_with_body(self) -> None:
        result = _try_split(
            "나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른"
        )
        assert result is not None
        heading, body = result
        assert heading.startswith("나.")
        assert "보험업" in body

    def test_numbered_heading_with_body(self) -> None:
        result = _try_split(
            "7. 기 타보험업 감독규정에 따라 할인할증을 적용하지 아니한다"
        )
        assert result is not None
        heading, body = result
        assert heading.startswith("7.")

    def test_jo_heading_with_body(self) -> None:
        result = _try_split(
            "제1조 보험의 목적보험계약에서 정하는 보험사고가 발생한 경우"
        )
        assert result is not None
        heading, body = result
        assert "제1조" in heading

    def test_paren_heading_with_body(self) -> None:
        result = _try_split(
            "(1) 계약의 취소보험계약자가 계약 체결 후 청약의 철회를 요청한 경우"
        )
        assert result is not None

    def test_no_split_for_plain_text(self) -> None:
        result = _try_split("보험업 감독규정에 따른 일반적인 문장입니다.")
        assert result is None

    def test_no_split_for_short_body(self) -> None:
        result = _try_split("나. 보험기간짧음")
        assert result is None

    def test_no_split_for_heading_only(self) -> None:
        result = _try_split("나. 보험기간")
        assert result is None


class TestSplitHeadingBody:
    def test_splits_concatenated_block(self) -> None:
        blocks = [
            _make_block(
                "나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른"
            )
        ]
        result = split_heading_body(blocks)
        assert len(result) == 2
        assert result[0].text.startswith("나.")
        assert "보험업" in result[1].text

    def test_preserves_normal_blocks(self) -> None:
        blocks = [
            _make_block("일반적인 텍스트 블록입니다."),
            _make_block("또 다른 일반 블록."),
        ]
        result = split_heading_body(blocks)
        assert len(result) == 2

    def test_split_bbox_divides_vertically(self) -> None:
        block = TextBlock(
            text="나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른",
            bbox=BBox(x0=10, y0=100, x1=500, y1=200),
            font=FontInfo(name="test", size=10.0, is_bold=True),
            block_type=BlockType.TEXT,
        )
        result = split_heading_body([block])
        assert len(result) == 2
        assert result[0].bbox.y1 < result[1].bbox.y0 or result[0].bbox.y1 == result[1].bbox.y0

    def test_mixed_blocks(self) -> None:
        blocks = [
            _make_block("일반 텍스트"),
            _make_block(
                "가. 판매채널보험회사의 판매채널은 다음과 같이 구분된다"
            ),
            _make_block("또 다른 일반 텍스트"),
        ]
        result = split_heading_body(blocks)
        assert len(result) == 4
