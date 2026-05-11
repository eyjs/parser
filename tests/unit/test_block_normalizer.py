"""Tests for ``block_normalizer`` -- LayoutBlock/TextBlock -> NormalizedBlock."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, NormalizedBlock, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.block_normalizer import (
    LAYOUT_LABEL_MAP,
    SURYA_LABEL_MAP,
    merge_normalized,
    normalize_layout_block,
    normalize_text_block,
)


def _lb(label: str, x0: float, y0: float, x1: float, y1: float,
        conf: float = 0.9) -> LayoutBlock:
    return LayoutBlock(
        bbox=BBox(x0, y0, x1, y1),
        label=label,
        confidence=conf,
        page_num=1,
    )


def _tb(text: str, x0: float, y0: float, x1: float, y1: float,
        block_type: BlockType = BlockType.TEXT,
        conf: float = 0.8,
        block_id: str | None = None) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="N", size=10, is_bold=False),
        block_type=block_type,
        confidence=conf,
        block_id=block_id,
    )


class TestNormalizeLayoutBlock:
    def test_table_label(self) -> None:
        lb = _lb("Table", 0, 0, 100, 50)
        nb = normalize_layout_block(lb)
        assert nb.block_type == BlockType.TABLE
        assert nb.source == "surya"
        assert nb.original_label == "Table"
        assert nb.confidence == 0.9

    def test_figure_label(self) -> None:
        nb = normalize_layout_block(_lb("Figure", 10, 10, 200, 200))
        assert nb.block_type == BlockType.FIGURE

    def test_picture_maps_to_figure(self) -> None:
        nb = normalize_layout_block(_lb("Picture", 0, 0, 50, 50))
        assert nb.block_type == BlockType.FIGURE

    def test_unknown_label_maps_to_unknown(self) -> None:
        nb = normalize_layout_block(_lb("SomeWeirdLabel", 0, 0, 30, 30))
        assert nb.block_type == BlockType.UNKNOWN

    def test_title_maps_to_heading(self) -> None:
        nb = normalize_layout_block(_lb("Title", 0, 0, 100, 20))
        assert nb.block_type == BlockType.HEADING

    def test_block_id_is_deterministic(self) -> None:
        lb = _lb("Text", 10.0, 20.0, 100.0, 40.0)
        nb1 = normalize_layout_block(lb)
        nb2 = normalize_layout_block(lb)
        assert nb1.block_id == nb2.block_id
        assert len(nb1.block_id) == 12


class TestNormalizeTextBlock:
    def test_basic_conversion(self) -> None:
        tb = _tb("hello", 0, 0, 100, 20, block_id="abc123")
        nb = normalize_text_block(tb, page_num=3)
        assert nb.block_id == "abc123"
        assert nb.text == "hello"
        assert nb.block_type == BlockType.TEXT
        assert nb.confidence == 0.8
        assert nb.source == "vision"
        assert nb.page_num == 3

    def test_no_block_id_generates_one(self) -> None:
        tb = _tb("test", 0, 0, 50, 10)
        nb = normalize_text_block(tb, page_num=1)
        assert nb.block_id is not None
        assert len(nb.block_id) == 12

    def test_preserves_block_type(self) -> None:
        tb = _tb("heading", 0, 0, 100, 30, block_type=BlockType.HEADING)
        nb = normalize_text_block(tb)
        assert nb.block_type == BlockType.HEADING


class TestMergeNormalized:
    def test_empty_inputs(self) -> None:
        assert merge_normalized([], []) == []

    def test_only_text_blocks(self) -> None:
        tb = _tb("text", 0, 0, 100, 20)
        norms = [normalize_text_block(tb, page_num=1)]
        result = merge_normalized(norms, [])
        assert len(result) == 1
        assert result[0].text == "text"

    def test_only_layout_blocks(self) -> None:
        lb = _lb("Table", 0, 0, 100, 50)
        norms = [normalize_layout_block(lb)]
        result = merge_normalized([], norms)
        assert len(result) == 1
        assert result[0].block_type == BlockType.TABLE

    def test_iou_matching_merges_blocks(self) -> None:
        """Overlapping text and layout blocks are merged."""
        tb = _tb("cell text", 0, 0, 100, 50, conf=0.7)
        lb = _lb("Table", 0, 0, 100, 50, conf=0.9)

        norm_text = [normalize_text_block(tb, page_num=1)]
        norm_layout = [normalize_layout_block(lb)]

        result = merge_normalized(norm_text, norm_layout, iou_threshold=0.3)
        assert len(result) == 1
        merged = result[0]
        # Layout has higher confidence -> its block_type wins
        assert merged.block_type == BlockType.TABLE
        assert merged.confidence == 0.9
        assert merged.text == "cell text"

    def test_no_iou_match_keeps_both(self) -> None:
        """Disjoint blocks are both included."""
        tb = _tb("text", 0, 0, 50, 20, conf=0.8)
        lb = _lb("Figure", 200, 200, 400, 400, conf=0.9)

        norm_text = [normalize_text_block(tb, page_num=1)]
        norm_layout = [normalize_layout_block(lb)]

        result = merge_normalized(norm_text, norm_layout, iou_threshold=0.3)
        assert len(result) == 2

    def test_text_confidence_higher_keeps_text_type(self) -> None:
        """When text block has higher confidence, its block_type wins."""
        tb = _tb("heading", 0, 0, 100, 30,
                 block_type=BlockType.HEADING, conf=0.95)
        lb = _lb("Text", 0, 0, 100, 30, conf=0.6)

        norm_text = [normalize_text_block(tb, page_num=1)]
        norm_layout = [normalize_layout_block(lb)]

        result = merge_normalized(norm_text, norm_layout, iou_threshold=0.3)
        assert len(result) == 1
        assert result[0].block_type == BlockType.HEADING
        assert result[0].confidence == 0.95


class TestSuryaLabelMap:
    def test_all_expected_labels_mapped(self) -> None:
        expected = {
            "Table", "Figure", "Picture", "Title",
            "Section-header", "Section-Header", "SectionHeader",
            "Caption", "Text", "Formula", "Equation",
            "List", "List-item",
            "Footer", "Page-Footer",
            "Page-Header", "Header",
            "Page-Number",
            "Footnote",
        }
        assert set(LAYOUT_LABEL_MAP.keys()) == expected

    def test_surya_alias(self) -> None:
        assert SURYA_LABEL_MAP is LAYOUT_LABEL_MAP
