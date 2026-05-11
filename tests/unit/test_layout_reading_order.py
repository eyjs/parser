"""Tests for ``reorder_blocks_by_layout()`` -- ML-based reading order restoration."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.column_detector import reorder_blocks_by_layout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tb(
    text: str,
    x0: float, y0: float, x1: float, y1: float,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


def _lb(
    label: str,
    x0: float, y0: float, x1: float, y1: float,
    confidence: float = 0.9,
    page_num: int = 1,
) -> LayoutBlock:
    return LayoutBlock(
        bbox=BBox(x0, y0, x1, y1),
        label=label,
        confidence=confidence,
        page_num=page_num,
    )


# ---------------------------------------------------------------------------
# Basic matching and reordering
# ---------------------------------------------------------------------------


class TestReorderBlocksByLayout:
    """Test ML layout-based reading order restoration."""

    def test_reorders_to_match_layout_sequence(self) -> None:
        """Text blocks should be reordered to follow layout block sequence."""
        # Arrange: text blocks in wrong order
        text_blocks = [
            _tb("second", 0, 100, 200, 150),
            _tb("first", 0, 0, 200, 50),
            _tb("third", 0, 200, 200, 250),
        ]
        # Layout blocks define reading order: first, second, third
        layout_blocks = [
            _lb("Text", 0, 0, 200, 50),
            _lb("Text", 0, 100, 200, 150),
            _lb("Text", 0, 200, 200, 250),
        ]

        # Act
        result = reorder_blocks_by_layout(text_blocks, layout_blocks)

        # Assert
        assert [b.text for b in result] == ["first", "second", "third"]

    def test_two_column_layout_reorder(self) -> None:
        """Simulate a two-column layout where left column should be read first."""
        # Arrange: interleaved left/right (wrong reading order)
        text_blocks = [
            _tb("left-1", 0, 0, 200, 50),
            _tb("right-1", 300, 0, 500, 50),
            _tb("left-2", 0, 60, 200, 110),
            _tb("right-2", 300, 60, 500, 110),
        ]
        # Layout blocks: all left first, then all right
        layout_blocks = [
            _lb("Text", 0, 0, 200, 50),
            _lb("Text", 0, 60, 200, 110),
            _lb("Text", 300, 0, 500, 50),
            _lb("Text", 300, 60, 500, 110),
        ]

        # Act
        result = reorder_blocks_by_layout(text_blocks, layout_blocks)

        # Assert
        texts = [b.text for b in result]
        assert texts == ["left-1", "left-2", "right-1", "right-2"]


# ---------------------------------------------------------------------------
# Unmatched blocks
# ---------------------------------------------------------------------------


class TestUnmatchedBlocks:
    """Unmatched blocks should be appended at the end sorted by y-coordinate."""

    def test_unmatched_blocks_appended_by_y_order(self) -> None:
        """Blocks with no IoU match should be appended sorted by y0."""
        # Arrange
        text_blocks = [
            _tb("matched", 0, 0, 100, 50),
            _tb("orphan-far-down", 500, 400, 600, 450),
            _tb("orphan-up", 500, 100, 600, 150),
        ]
        layout_blocks = [
            _lb("Text", 0, 0, 100, 50),  # matches first text block only
        ]

        # Act
        result = reorder_blocks_by_layout(text_blocks, layout_blocks)

        # Assert
        assert result[0].text == "matched"
        # Remaining unmatched blocks sorted by y0
        assert result[1].text == "orphan-up"        # y0=100
        assert result[2].text == "orphan-far-down"  # y0=400

    def test_all_unmatched_returns_y_sorted(self) -> None:
        """When no layout blocks match, return blocks sorted by y0."""
        # Arrange: layout blocks are far away from text blocks
        text_blocks = [
            _tb("bottom", 0, 200, 100, 250),
            _tb("top", 0, 0, 100, 50),
            _tb("middle", 0, 100, 100, 150),
        ]
        layout_blocks = [
            _lb("Text", 900, 900, 999, 999),  # no overlap at all
        ]

        # Act
        result = reorder_blocks_by_layout(text_blocks, layout_blocks)

        # Assert: sorted by y0
        assert [b.text for b in result] == ["top", "middle", "bottom"]


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for reorder_blocks_by_layout."""

    def test_empty_text_blocks_returns_empty(self) -> None:
        result = reorder_blocks_by_layout([], [_lb("Text", 0, 0, 100, 50)])
        assert result == []

    def test_empty_layout_blocks_returns_input_unchanged(self) -> None:
        text_blocks = [_tb("a", 0, 0, 100, 50)]
        result = reorder_blocks_by_layout(text_blocks, [])
        assert result is text_blocks  # identity: graceful degradation

    def test_both_empty_returns_empty(self) -> None:
        result = reorder_blocks_by_layout([], [])
        assert result == []

    def test_single_block_single_layout(self) -> None:
        """Single block matching single layout should return that block."""
        text_blocks = [_tb("only", 0, 0, 100, 50)]
        layout_blocks = [_lb("Text", 0, 0, 100, 50)]

        result = reorder_blocks_by_layout(text_blocks, layout_blocks)
        assert len(result) == 1
        assert result[0].text == "only"


# ---------------------------------------------------------------------------
# IoU threshold behavior
# ---------------------------------------------------------------------------


class TestIoUThreshold:
    """Test that iou_threshold parameter controls matching."""

    def test_below_threshold_not_matched(self) -> None:
        """Blocks with IoU < threshold should not be matched."""
        # Arrange: text and layout overlap only slightly
        text_blocks = [_tb("barely", 0, 0, 100, 50)]
        layout_blocks = [_lb("Text", 80, 40, 200, 100)]
        # IoU is quite small (20*10 intersection)

        # Act: with high threshold, should NOT match
        result = reorder_blocks_by_layout(
            text_blocks, layout_blocks, iou_threshold=0.9,
        )

        # Assert: block is unmatched, so still returned (appended at end)
        assert len(result) == 1
        assert result[0].text == "barely"

    def test_custom_threshold_zero_matches_any_overlap(self) -> None:
        """IoU threshold 0 should match any overlapping blocks."""
        # Arrange: minimal overlap
        text_blocks = [_tb("tiny-overlap", 0, 0, 100, 50)]
        layout_blocks = [_lb("Text", 99, 49, 200, 100)]
        # Overlap: 1*1 = 1, union is large -> very small IoU

        # Act
        result = reorder_blocks_by_layout(
            text_blocks, layout_blocks, iou_threshold=0.0,
        )

        # Assert: should match since threshold is 0
        assert len(result) == 1

    def test_default_threshold_is_0_3(self) -> None:
        """Verify the function works with default iou_threshold=0.3."""
        # Arrange: blocks with exactly 100% overlap
        text_blocks = [_tb("exact", 0, 0, 100, 100)]
        layout_blocks = [_lb("Text", 0, 0, 100, 100)]

        # Act: no explicit threshold -> default 0.3
        result = reorder_blocks_by_layout(text_blocks, layout_blocks)

        # Assert
        assert len(result) == 1
        assert result[0].text == "exact"


# ---------------------------------------------------------------------------
# Duplicate match prevention
# ---------------------------------------------------------------------------


class TestDuplicateMatchPrevention:
    """Each text block should only be matched once (greedy, not reused)."""

    def test_text_block_matched_only_once(self) -> None:
        """If two layout blocks could match the same text block, only first wins."""
        # Arrange: one text block, two overlapping layout blocks
        text_blocks = [_tb("shared", 0, 0, 100, 50)]
        layout_blocks = [
            _lb("Title", 0, 0, 100, 50),   # first: matches
            _lb("Text", 0, 0, 100, 50),     # second: same area, but already consumed
        ]

        # Act
        result = reorder_blocks_by_layout(text_blocks, layout_blocks)

        # Assert: text block appears exactly once
        assert len(result) == 1
        assert result[0].text == "shared"

    def test_many_layout_few_text(self) -> None:
        """More layout blocks than text blocks should not cause duplication."""
        text_blocks = [_tb("only-one", 0, 0, 100, 50)]
        layout_blocks = [
            _lb("Text", 0, 0, 100, 50),
            _lb("Title", 0, 0, 100, 50),
            _lb("Caption", 0, 0, 100, 50),
        ]

        result = reorder_blocks_by_layout(text_blocks, layout_blocks)
        assert len(result) == 1
