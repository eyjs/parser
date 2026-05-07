"""Tests for confidence-based routing engine (layout_router.route_blocks)."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import NormalizedBlock
from docforge.domain.value_objects import BBox
from docforge.processing.layout_router import (
    DEFAULT_RULES,
    RoutingDecision,
    RoutingRule,
    route_blocks,
)


def _nb(
    block_type: BlockType,
    confidence: float,
    block_id: str = "test-block",
    text: str = "",
) -> NormalizedBlock:
    return NormalizedBlock(
        block_id=block_id,
        bbox=BBox(0, 0, 100, 50),
        block_type=block_type,
        confidence=confidence,
        text=text,
        source="test",
        page_num=1,
    )


class TestRouteBlocks:
    def test_empty_input(self) -> None:
        assert route_blocks([]) == []

    def test_table_high_confidence_table_parser(self) -> None:
        blocks = [_nb(BlockType.TABLE, 0.9)]
        decisions = route_blocks(blocks)
        assert len(decisions) == 1
        assert decisions[0].action == "table_parser"

    def test_table_low_confidence_vlm_crop(self) -> None:
        blocks = [_nb(BlockType.TABLE, 0.5)]
        decisions = route_blocks(blocks)
        assert len(decisions) == 1
        assert decisions[0].action == "vlm_crop"

    def test_table_boundary_confidence_08(self) -> None:
        """Confidence exactly 0.8 falls in [0.8, 1.01) -> table_parser."""
        blocks = [_nb(BlockType.TABLE, 0.8)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "table_parser"

    def test_table_just_below_08(self) -> None:
        """Confidence 0.79 falls in [0.0, 0.8) -> vlm_crop."""
        blocks = [_nb(BlockType.TABLE, 0.79)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "vlm_crop"

    def test_chart_always_vlm_chart(self) -> None:
        blocks = [_nb(BlockType.CHART, 0.7)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "vlm_chart"

    def test_chart_low_confidence_still_vlm_chart(self) -> None:
        blocks = [_nb(BlockType.CHART, 0.1)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "vlm_chart"

    def test_figure_always_vlm_caption(self) -> None:
        blocks = [_nb(BlockType.FIGURE, 0.8)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "vlm_caption"

    def test_text_high_confidence_markdown(self) -> None:
        blocks = [_nb(BlockType.TEXT, 0.9)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "markdown"

    def test_text_low_confidence_fallback(self) -> None:
        blocks = [_nb(BlockType.TEXT, 0.3)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "fallback"

    def test_unknown_always_fallback(self) -> None:
        blocks = [_nb(BlockType.UNKNOWN, 0.5)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "fallback"

    def test_heading_always_markdown(self) -> None:
        blocks = [_nb(BlockType.HEADING, 0.9)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "markdown"

    def test_clause_always_markdown(self) -> None:
        blocks = [_nb(BlockType.CLAUSE, 0.7)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "markdown"

    def test_footnote_always_markdown(self) -> None:
        blocks = [_nb(BlockType.FOOTNOTE, 0.5)]
        decisions = route_blocks(blocks)
        assert decisions[0].action == "markdown"

    def test_multiple_blocks_independent_routing(self) -> None:
        blocks = [
            _nb(BlockType.TABLE, 0.9, block_id="t1"),
            _nb(BlockType.CHART, 0.7, block_id="c1"),
            _nb(BlockType.TEXT, 0.8, block_id="x1"),
        ]
        decisions = route_blocks(blocks)
        assert len(decisions) == 3
        assert decisions[0].action == "table_parser"
        assert decisions[1].action == "vlm_chart"
        assert decisions[2].action == "markdown"

    def test_decision_carries_confidence(self) -> None:
        blocks = [_nb(BlockType.TABLE, 0.85)]
        decisions = route_blocks(blocks)
        assert decisions[0].confidence == 0.85

    def test_decision_carries_rule_matched(self) -> None:
        blocks = [_nb(BlockType.TABLE, 0.9)]
        decisions = route_blocks(blocks)
        assert "table" in decisions[0].rule_matched.lower()


class TestRoutingRule:
    def test_frozen(self) -> None:
        rule = RoutingRule(BlockType.TABLE, 0.0, 1.0, "test")
        try:
            rule.action = "other"  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("RoutingRule should be frozen")

    def test_custom_rules_override_defaults(self) -> None:
        """Custom rules can be passed to route_blocks."""
        custom = [
            RoutingRule(BlockType.TEXT, 0.0, 1.01, "custom_action", priority=100),
        ]
        blocks = [_nb(BlockType.TEXT, 0.9)]
        decisions = route_blocks(blocks, rules=custom)
        assert decisions[0].action == "custom_action"


class TestDefaultRules:
    def test_all_block_types_covered(self) -> None:
        """Every routable BlockType has at least one rule in DEFAULT_RULES."""
        covered = {r.block_type for r in DEFAULT_RULES}
        assert BlockType.TABLE in covered
        assert BlockType.CHART in covered
        assert BlockType.FIGURE in covered
        assert BlockType.TEXT in covered
        assert BlockType.HEADING in covered
        assert BlockType.UNKNOWN in covered
