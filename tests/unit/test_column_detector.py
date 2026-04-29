"""Tests for multi-column layout detection."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.column_detector import (
    ColumnLayout,
    detect_columns,
    reorder_blocks_by_columns,
)


def _block(text: str, x0: float, x1: float, y0: float, y1: float) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


class TestDetectColumns:
    def test_single_column(self) -> None:
        blocks = [
            _block("line1", x0=50, x1=500, y0=0, y1=10),
            _block("line2", x0=50, x1=500, y0=15, y1=25),
            _block("line3", x0=50, x1=500, y0=30, y1=40),
            _block("line4", x0=50, x1=500, y0=45, y1=55),
        ]
        layout = detect_columns(blocks, page_width=600.0)
        assert layout.num_columns == 1

    def test_two_columns(self) -> None:
        # Left column: x=50-250, Right column: x=350-550
        # Gap at 250-350 (100px, >5% of 600px page)
        blocks = []
        for i in range(5):
            blocks.append(_block(f"left{i}", x0=50, x1=250, y0=i * 15, y1=i * 15 + 10))
            blocks.append(_block(f"right{i}", x0=350, x1=550, y0=i * 15, y1=i * 15 + 10))
        layout = detect_columns(blocks, page_width=600.0)
        assert layout.num_columns == 2

    def test_empty_blocks(self) -> None:
        layout = detect_columns([], page_width=600.0)
        assert layout.num_columns == 1

    def test_too_few_blocks(self) -> None:
        blocks = [
            _block("a", x0=50, x1=200, y0=0, y1=10),
            _block("b", x0=350, x1=500, y0=0, y1=10),
        ]
        layout = detect_columns(blocks, page_width=600.0)
        assert layout.num_columns == 1


class TestReorderBlocks:
    def test_reorders_two_columns(self) -> None:
        # Interleaved left/right blocks (wrong reading order)
        blocks = [
            _block("left1", x0=50, x1=250, y0=0, y1=10),
            _block("right1", x0=350, x1=550, y0=0, y1=10),
            _block("left2", x0=50, x1=250, y0=15, y1=25),
            _block("right2", x0=350, x1=550, y0=15, y1=25),
            _block("left3", x0=50, x1=250, y0=30, y1=40),
            _block("right3", x0=350, x1=550, y0=30, y1=40),
        ]
        # Add more blocks to meet minimum per column
        for i in range(3, 6):
            blocks.append(_block(f"left{i}", x0=50, x1=250, y0=i * 15, y1=i * 15 + 10))
            blocks.append(_block(f"right{i}", x0=350, x1=550, y0=i * 15, y1=i * 15 + 10))

        layout = detect_columns(blocks, page_width=600.0)
        reordered = reorder_blocks_by_columns(blocks, layout)

        # All left column blocks should come before right column blocks
        texts = [b.text for b in reordered]
        left_indices = [texts.index(f"left{i}") for i in range(1, 6)]
        right_indices = [texts.index(f"right{i}") for i in range(1, 6)]
        assert max(left_indices) < min(right_indices)

    def test_single_column_no_reorder(self) -> None:
        blocks = [
            _block("a", x0=50, x1=500, y0=0, y1=10),
            _block("b", x0=50, x1=500, y0=15, y1=25),
        ]
        layout = ColumnLayout(num_columns=1, column_boundaries=((0.0, 600.0),))
        result = reorder_blocks_by_columns(blocks, layout)
        assert result == blocks
