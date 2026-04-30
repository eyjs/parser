"""Tests for ``layout_router`` — IoU matching, label override, frozen safety."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.layout_router import (
    bbox_iou,
    build_layout_label_map,
    merge_layout_with_text,
)


def _tb(
    text: str,
    x0: float, y0: float, x1: float, y1: float,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
    block_id: str | None = None,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="N", size=10, is_bold=False),
        block_type=block_type,
        heading_level=heading_level,
        block_id=block_id,
    )


def _lb(label: str, x0: float, y0: float, x1: float, y1: float) -> LayoutBlock:
    return LayoutBlock(
        bbox=BBox(x0, y0, x1, y1),
        label=label,
        confidence=0.9,
        page_num=1,
    )


class TestBBoxIoU:
    def test_full_overlap_is_one(self) -> None:
        a = BBox(0, 0, 100, 100)
        assert bbox_iou(a, a) == 1.0

    def test_disjoint_is_zero(self) -> None:
        a = BBox(0, 0, 10, 10)
        b = BBox(100, 100, 200, 200)
        assert bbox_iou(a, b) == 0.0

    def test_partial_overlap(self) -> None:
        a = BBox(0, 0, 100, 100)
        b = BBox(50, 50, 150, 150)
        # intersection 50x50 = 2500, union 100*100*2 - 2500 = 17500
        assert bbox_iou(a, b) == 2500 / 17500


class TestMergeLayoutWithText:
    def test_empty_layout_returns_input_unchanged(self) -> None:
        blocks = [_tb("hello", 0, 0, 100, 20)]
        result = merge_layout_with_text(blocks, [])
        assert result is blocks  # graceful degradation: no copy

    def test_title_overrides_block_type_to_heading(self) -> None:
        blocks = [_tb("제1장 총칙", 0, 0, 100, 30)]
        layouts = [_lb("Title", 0, 0, 100, 30)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level == 2

    def test_existing_heading_level_preserved(self) -> None:
        blocks = [
            _tb("제1편", 0, 0, 100, 30, block_type=BlockType.HEADING, heading_level=1),
        ]
        layouts = [_lb("Title", 0, 0, 100, 30)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].heading_level == 1

    def test_caption_label_sets_item(self) -> None:
        blocks = [_tb("그림 1: 흐름도", 0, 0, 100, 20)]
        layouts = [_lb("Caption", 0, 0, 100, 20)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].block_type == BlockType.ITEM

    def test_low_iou_does_not_override(self) -> None:
        blocks = [_tb("text", 0, 0, 10, 10)]
        layouts = [_lb("Title", 100, 100, 200, 200)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].block_type == BlockType.TEXT

    def test_text_label_passthrough(self) -> None:
        blocks = [_tb("paragraph", 0, 0, 100, 20)]
        layouts = [_lb("Text", 0, 0, 100, 20)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].block_type == BlockType.TEXT

    def test_block_id_preserved_after_override(self) -> None:
        blocks = [_tb("title", 0, 0, 100, 30, block_id="abc123")]
        layouts = [_lb("Title", 0, 0, 100, 30)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].block_id == "abc123"


class TestBuildLayoutLabelMap:
    def test_returns_label_for_matched_block(self) -> None:
        blocks = [_tb("cap", 0, 0, 100, 20, block_id="img1")]
        layouts = [_lb("Caption", 0, 0, 100, 20)]
        result = build_layout_label_map(blocks, layouts)
        assert result == {"img1": "Caption"}

    def test_skips_blocks_without_id(self) -> None:
        blocks = [_tb("cap", 0, 0, 100, 20)]
        layouts = [_lb("Caption", 0, 0, 100, 20)]
        assert build_layout_label_map(blocks, layouts) == {}

    def test_empty_layout_returns_empty(self) -> None:
        blocks = [_tb("cap", 0, 0, 100, 20, block_id="x")]
        assert build_layout_label_map(blocks, []) == {}
