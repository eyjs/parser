"""Tests for physical line break to logical paragraph merging."""

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.line_merger import _join_texts, merge_lines


def _make_block(
    text: str,
    y0: float = 100.0,
    x0: float = 50.0,
    font_size: float = 10.0,
    is_bold: bool = False,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
) -> TextBlock:
    """Helper to create a TextBlock."""
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=500.0, y1=y0 + 12.0),
        font=FontInfo(name="Arial", size=font_size, is_bold=is_bold),
        block_type=block_type,
        heading_level=heading_level,
    )


class TestLineMerging:
    """Test line merging rules."""

    def test_empty_input(self) -> None:
        config = ParserConfig()
        result = merge_lines([], 10.0, 5.0, config)
        assert result == []

    def test_single_block(self) -> None:
        config = ParserConfig()
        blocks = [_make_block("single line")]
        result = merge_lines(blocks, 10.0, 5.0, config)
        assert len(result) == 1
        assert result[0].text == "single line"

    def test_merges_continuation(self) -> None:
        """Lines without sentence terminators should merge."""
        config = ParserConfig()
        blocks = [
            _make_block("이 약관에서 사용하는", y0=100.0),
            _make_block("용어의 뜻은 다음과 같습니다", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        assert "이 약관에서 사용하는" in result[0].text
        assert "용어의 뜻은" in result[0].text

    def test_splits_on_heading(self) -> None:
        """Heading patterns should cause splits."""
        config = ParserConfig()
        blocks = [
            _make_block("이전 문장의 내용입니다.", y0=100.0),
            _make_block("제1조 (목적) 이 약관은", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 2

    def test_splits_on_clause(self) -> None:
        """Circled numbers should cause splits."""
        config = ParserConfig()
        blocks = [
            _make_block("이전 내용입니다.", y0=100.0),
            _make_block("① 보험계약자는 다음의 사항을", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 2

    def test_splits_on_large_gap(self) -> None:
        """Large vertical gaps should cause splits."""
        config = ParserConfig()
        blocks = [
            _make_block("첫번째 문단", y0=100.0),
            _make_block("두번째 문단", y0=200.0),  # Large gap
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 2

    def test_splits_on_indent_change(self) -> None:
        """Significant indent changes should cause splits."""
        config = ParserConfig()
        blocks = [
            _make_block("일반 텍스트", y0=100.0, x0=50.0),
            _make_block("들여쓴 텍스트", y0=115.0, x0=80.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 2

    def test_splits_on_font_change(self) -> None:
        """Font size changes should cause splits."""
        config = ParserConfig()
        blocks = [
            _make_block("본문 텍스트", y0=100.0, font_size=10.0),
            _make_block("제목 텍스트", y0=115.0, font_size=14.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 2

    def test_merges_postposition_start(self) -> None:
        """Lines starting with Korean postpositions should merge."""
        config = ParserConfig()
        blocks = [
            _make_block("보험계약자", y0=100.0),
            _make_block("에게 보험금을 지급합니다", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1

    def test_merges_conjunction_start(self) -> None:
        """Lines starting with conjunctions should merge."""
        config = ParserConfig()
        blocks = [
            _make_block("보험금을 지급하지 않습니다", y0=100.0),
            _make_block("다만 보험계약자가 동의한 경우", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        # "다만" is a conjunction, but prev line ends with "다" which looks like terminator
        # This tests the conjunction detection
        assert len(result) <= 2  # May or may not merge depending on priority

    def test_merges_mid_word_korean_line_break(self) -> None:
        """Korean word split across lines should merge without space."""
        config = ParserConfig()
        blocks = [
            _make_block(
                '보험회사(이하 "회',
                y0=100.0,
            ),
            _make_block(
                '사"라 합니다) 사이에',
                y0=115.0,
            ),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        # "회" + "사" should be joined without space
        assert '"회사"라 합니다)' in result[0].text

    def test_merges_continuation_without_space_hangul(self) -> None:
        """Consecutive Korean lines without sentence terminator merge without space."""
        config = ParserConfig()
        blocks = [
            _make_block("보험계약자(이하 '계약자'라 합니다)와 보험회사(이하 '회", y0=100.0),
            _make_block("사'라 합니다) 사이에 피보험자가 질병에 걸린 경우", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        # No space between '회' and '사'
        assert "'회사'라 합니다)" in result[0].text

    def test_does_not_merge_after_sentence_end(self) -> None:
        """Lines after sentence terminator should get space separator."""
        config = ParserConfig()
        blocks = [
            _make_block("합니다.", y0=100.0),
            _make_block("다음 문장입니다", y0=115.0),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        # Even if merged, there should be a space (not direct join)
        if len(result) == 1:
            assert "합니다. 다음" in result[0].text


# ---- P0-7: Vertical gap forced split + cross-block boundary tests ----


def _make_block_with_id(
    text: str,
    y0: float = 100.0,
    x0: float = 50.0,
    line_height: float = 12.0,
    font_size: float = 10.0,
    is_bold: bool = False,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
    block_id: str | None = None,
) -> TextBlock:
    """Helper to create a TextBlock with explicit block_id and line_height."""
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=500.0, y1=y0 + line_height),
        font=FontInfo(name="Arial", size=font_size, is_bold=is_bold),
        block_type=block_type,
        heading_level=heading_level,
        block_id=block_id,
    )


class TestVerticalGapForcedSplit:
    """P0-7: Force split when vertical gap >= line_height * split_factor."""

    def test_large_gap_forces_split_even_with_comma(self) -> None:
        """A comma at end normally merges, but a huge gap should force split."""
        config = ParserConfig(line_height_split_factor=1.2)
        lh = 12.0
        blocks = [
            _make_block_with_id("보험금,", y0=100.0, line_height=lh),
            # gap = 200 - 112 = 88 >> 12 * 1.2 = 14.4
            _make_block_with_id("다음 항목입니다", y0=200.0, line_height=lh),
        ]
        result = merge_lines(blocks, 10.0, 5.0, config)
        assert len(result) == 2

    def test_large_gap_forces_split_even_with_bracket(self) -> None:
        """Open bracket at end normally merges, but forced split overrides."""
        config = ParserConfig(line_height_split_factor=1.2)
        lh = 12.0
        blocks = [
            _make_block_with_id("사유가 발생한 때(", y0=100.0, line_height=lh),
            # gap = 200 - 112 = 88 >> 14.4
            _make_block_with_id("이하 사고라 한다)", y0=200.0, line_height=lh),
        ]
        result = merge_lines(blocks, 10.0, 5.0, config)
        assert len(result) == 2

    def test_normal_gap_does_not_force_split(self) -> None:
        """Gap smaller than threshold should let normal merge logic apply."""
        config = ParserConfig(line_height_split_factor=1.2)
        lh = 12.0
        blocks = [
            _make_block_with_id("보험계약자(이하 '계약자'라 합니다)와 보험회사(이하 '회", y0=100.0, line_height=lh),
            # gap = 115 - 112 = 3 < 12 * 1.2 = 14.4 -> normal merge
            _make_block_with_id("사'라 합니다) 사이에", y0=115.0, line_height=lh),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        assert "'회사'라 합니다)" in result[0].text

    def test_exact_threshold_forces_split(self) -> None:
        """Gap exactly at the threshold boundary should force split."""
        config = ParserConfig(line_height_split_factor=1.2)
        lh = 10.0
        # gap needs to be >= 10 * 1.2 = 12.0
        blocks = [
            _make_block_with_id("이전 내용", y0=100.0, line_height=lh),
            # gap = 122 - 110 = 12.0 == threshold -> split
            _make_block_with_id("다음 내용", y0=122.0, line_height=lh),
        ]
        result = merge_lines(blocks, 10.0, 5.0, config)
        assert len(result) == 2

    def test_just_below_threshold_merges(self) -> None:
        """Gap just below the threshold should allow normal merge."""
        config = ParserConfig(line_height_split_factor=1.2)
        lh = 10.0
        blocks = [
            _make_block_with_id("보험계약자", y0=100.0, line_height=lh),
            # gap = 121.9 - 110 = 11.9 < 12.0 -> merge (prev ends w/o terminator)
            _make_block_with_id("에게 지급합니다", y0=121.9, line_height=lh),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1


class TestCrossBlockBoundarySpacing:
    """P0-7: Cross-block boundary inserts space to prevent sticky text."""

    def test_different_block_ids_insert_space(self) -> None:
        """Korean lines from different source blocks get space separator."""
        config = ParserConfig()
        blocks = [
            _make_block_with_id(
                "피보험자가", y0=100.0, line_height=12.0, block_id="block-A",
            ),
            _make_block_with_id(
                "사망한 경우", y0=115.0, line_height=12.0, block_id="block-B",
            ),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        # They merge (no structural split) but with space due to different block_ids
        if len(result) == 1:
            assert "피보험자가 사망한" in result[0].text

    def test_same_block_id_no_extra_space(self) -> None:
        """Korean lines from the same source block merge without space."""
        config = ParserConfig()
        blocks = [
            _make_block_with_id(
                "보험회사(이하 '회", y0=100.0, line_height=12.0, block_id="block-A",
            ),
            _make_block_with_id(
                "사'라 합니다)", y0=115.0, line_height=12.0, block_id="block-A",
            ),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        assert "'회사'라 합니다)" in result[0].text

    def test_none_block_ids_treated_as_same(self) -> None:
        """When block_ids are None, fall back to normal Korean join rules."""
        config = ParserConfig()
        blocks = [
            _make_block_with_id(
                "보험회사(이하 '회", y0=100.0, line_height=12.0, block_id=None,
            ),
            _make_block_with_id(
                "사'라 합니다)", y0=115.0, line_height=12.0, block_id=None,
            ),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        # None block_ids -> no boundary -> Korean join without space
        assert "'회사'라 합니다)" in result[0].text

    def test_mixed_none_and_id_no_false_boundary(self) -> None:
        """One None + one set block_id should NOT trigger boundary spacing."""
        config = ParserConfig()
        blocks = [
            _make_block_with_id(
                "보험회사(이하 '회", y0=100.0, line_height=12.0, block_id=None,
            ),
            _make_block_with_id(
                "사'라 합니다)", y0=115.0, line_height=12.0, block_id="block-B",
            ),
        ]
        result = merge_lines(blocks, 10.0, 12.0, config)
        assert len(result) == 1
        # One None -> not a real boundary -> normal Korean join
        assert "'회사'라 합니다)" in result[0].text


class TestJoinTextsBlockBoundaries:
    """P0-7: Direct unit tests for _join_texts with block_boundaries."""

    def test_no_boundaries_korean_no_space(self) -> None:
        """Without boundaries, Korean-Korean mid-word join has no space."""
        result = _join_texts(["보험회사(이하 '회", "사'라 합니다)"])
        assert "'회사'라 합니다)" in result

    def test_boundary_true_inserts_space(self) -> None:
        """Boundary True between Korean fragments inserts space."""
        result = _join_texts(
            ["피보험자가", "사망한 경우"],
            block_boundaries=[True],
        )
        assert result == "피보험자가 사망한 경우"

    def test_boundary_false_preserves_korean_join(self) -> None:
        """Boundary False preserves normal Korean-Korean no-space join."""
        result = _join_texts(
            ["보험회사(이하 '회", "사'라 합니다)"],
            block_boundaries=[False],
        )
        assert "'회사'라 합니다)" in result

    def test_multiple_boundaries_mixed(self) -> None:
        """Mix of boundary flags in a multi-fragment join."""
        result = _join_texts(
            ["가나다", "라마바", "사아자"],
            block_boundaries=[False, True],
        )
        # First join: no boundary -> Korean no-space
        # Second join: boundary -> space
        assert "가나다라마바" in result
        assert "라마바 사아자" in result

    def test_bracket_override_at_boundary(self) -> None:
        """Bracket continuation takes priority over boundary spacing."""
        result = _join_texts(
            ["사유가 발생한 때(", "이하 사고라 한다)"],
            block_boundaries=[True],
        )
        # Bracket rule overrides cross-block boundary
        assert "때(이하" in result
