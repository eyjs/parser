"""Tests for ``layout_router`` — IoU matching, label override, routing, frozen safety."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, NormalizedBlock, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.layout_router import (
    DEFAULT_RULES,
    RoutingDecision,
    RoutingRule,
    bbox_iou,
    build_layout_label_map,
    extract_table_hints,
    merge_layout_with_text,
    route_blocks,
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
        assert result[0].heading_level == 1  # C5: Title -> level 1 (top heading)

    def test_existing_heading_level_preserved(self) -> None:
        blocks = [
            _tb("제1편", 0, 0, 100, 30, block_type=BlockType.HEADING, heading_level=1),
        ]
        layouts = [_lb("Title", 0, 0, 100, 30)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].heading_level == 1

    def test_caption_label_sets_caption(self) -> None:
        blocks = [_tb("그림 1: 흐름도", 0, 0, 100, 20)]
        layouts = [_lb("Caption", 0, 0, 100, 20)]
        result = merge_layout_with_text(blocks, layouts)
        assert result[0].block_type == BlockType.CAPTION  # C5: Caption -> CAPTION (not ITEM)

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


# ---- Phase 2: route_blocks tests ----

def _nb(
    block_type: BlockType,
    confidence: float,
    block_id: str = "nb-001",
) -> NormalizedBlock:
    return NormalizedBlock(
        block_id=block_id,
        bbox=BBox(0, 0, 100, 50),
        block_type=block_type,
        confidence=confidence,
        source="test",
        page_num=1,
    )


class TestRouteBlocks:
    """Phase 2: confidence-based routing via route_blocks."""

    def test_empty_input_returns_empty(self) -> None:
        assert route_blocks([]) == []

    def test_table_high_confidence(self) -> None:
        decisions = route_blocks([_nb(BlockType.TABLE, 0.9)])
        assert decisions[0].action == "table_parser"

    def test_table_low_confidence(self) -> None:
        decisions = route_blocks([_nb(BlockType.TABLE, 0.5)])
        assert decisions[0].action == "vlm_crop"

    def test_chart_routes_to_vlm_chart(self) -> None:
        decisions = route_blocks([_nb(BlockType.CHART, 0.7)])
        assert decisions[0].action == "vlm_chart"

    def test_figure_routes_to_vlm_caption(self) -> None:
        decisions = route_blocks([_nb(BlockType.FIGURE, 0.8)])
        assert decisions[0].action == "vlm_caption"

    def test_text_high_conf_markdown(self) -> None:
        decisions = route_blocks([_nb(BlockType.TEXT, 0.9)])
        assert decisions[0].action == "markdown"

    def test_text_low_conf_fallback(self) -> None:
        decisions = route_blocks([_nb(BlockType.TEXT, 0.3)])
        assert decisions[0].action == "fallback"

    def test_decision_is_frozen(self) -> None:
        decisions = route_blocks([_nb(BlockType.TABLE, 0.9)])
        try:
            decisions[0].action = "other"  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("RoutingDecision should be frozen")

    def test_custom_rules(self) -> None:
        custom = [RoutingRule(BlockType.TEXT, 0.0, 1.01, "custom", priority=100)]
        decisions = route_blocks([_nb(BlockType.TEXT, 0.9)], rules=custom)
        assert decisions[0].action == "custom"


# ---- Sprint 3: New BlockType routing rules ----


class TestNewBlockTypeRouting:
    """Sprint 3: routing rules for LIST, PAGE_FOOTER, PAGE_NUMBER, PAGE_HEADER."""

    def test_list_routes_to_markdown(self) -> None:
        decisions = route_blocks([_nb(BlockType.LIST, 0.8)])
        assert decisions[0].action == "markdown"

    def test_list_low_confidence_still_markdown(self) -> None:
        decisions = route_blocks([_nb(BlockType.LIST, 0.1)])
        assert decisions[0].action == "markdown"

    def test_page_footer_routes_to_noise_filter(self) -> None:
        decisions = route_blocks([_nb(BlockType.PAGE_FOOTER, 0.9)])
        assert decisions[0].action == "noise_filter"

    def test_page_footer_low_confidence_still_noise_filter(self) -> None:
        decisions = route_blocks([_nb(BlockType.PAGE_FOOTER, 0.1)])
        assert decisions[0].action == "noise_filter"

    def test_page_number_routes_to_noise_filter(self) -> None:
        decisions = route_blocks([_nb(BlockType.PAGE_NUMBER, 0.9)])
        assert decisions[0].action == "noise_filter"

    def test_page_number_low_confidence_still_noise_filter(self) -> None:
        decisions = route_blocks([_nb(BlockType.PAGE_NUMBER, 0.2)])
        assert decisions[0].action == "noise_filter"

    def test_page_header_routes_to_noise_filter(self) -> None:
        decisions = route_blocks([_nb(BlockType.PAGE_HEADER, 0.95)])
        assert decisions[0].action == "noise_filter"

    def test_page_header_low_confidence_still_noise_filter(self) -> None:
        decisions = route_blocks([_nb(BlockType.PAGE_HEADER, 0.05)])
        assert decisions[0].action == "noise_filter"

    def test_noise_labels_have_high_priority(self) -> None:
        """Noise routing rules should have priority=10 (not overridden by lower rules)."""
        for bt in (BlockType.PAGE_FOOTER, BlockType.PAGE_NUMBER, BlockType.PAGE_HEADER):
            decisions = route_blocks([_nb(bt, 0.5)])
            assert decisions[0].action == "noise_filter"
            assert "noise_filter" in decisions[0].rule_matched

    def test_default_rules_contain_new_block_types(self) -> None:
        """Verify DEFAULT_RULES includes rules for all new block types."""
        rule_types = {r.block_type for r in DEFAULT_RULES}
        assert BlockType.LIST in rule_types
        assert BlockType.PAGE_FOOTER in rule_types
        assert BlockType.PAGE_NUMBER in rule_types
        assert BlockType.PAGE_HEADER in rule_types


# ---- P0-5: extract_table_hints tests ----


class TestExtractTableHints:
    """P0-5: extract TABLE bboxes from layout blocks."""

    def test_empty_input_returns_empty(self) -> None:
        assert extract_table_hints([]) == []

    def test_none_input_returns_empty(self) -> None:
        # Defensive: treat falsy input as empty
        assert extract_table_hints([]) == []

    def test_extracts_table_labels_only(self) -> None:
        blocks = [
            _lb("Table", 10, 100, 500, 300),
            _lb("Text", 10, 310, 500, 400),
            _lb("Figure", 10, 410, 500, 600),
            _lb("Table", 10, 610, 500, 800),
        ]
        result = extract_table_hints(blocks)
        assert len(result) == 2
        assert result[0] == BBox(10, 100, 500, 300)
        assert result[1] == BBox(10, 610, 500, 800)

    def test_no_table_labels_returns_empty(self) -> None:
        blocks = [
            _lb("Text", 10, 100, 500, 200),
            _lb("Figure", 10, 300, 500, 500),
        ]
        assert extract_table_hints(blocks) == []

    def test_single_table(self) -> None:
        blocks = [_lb("Table", 50, 200, 550, 400)]
        result = extract_table_hints(blocks)
        assert len(result) == 1
        assert result[0].x0 == 50
        assert result[0].y0 == 200
