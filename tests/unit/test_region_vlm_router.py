"""Unit tests for region_vlm_router module."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import numpy as np
import pytest

from docforge.domain.enums import BlockType
from docforge.domain.models import Table, TableCell, TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage
from docforge.processing.region_vlm_router import (
    parse_markdown_table,
    route_table_to_vlm,
    _extract_table_lines,
)


def _make_bbox() -> BBox:
    return BBox(x0=10.0, y0=20.0, x1=200.0, y1=100.0)


def _make_raw_image() -> RawImage:
    return RawImage(
        data=np.zeros((100, 200, 3), dtype=np.uint8),
        width=200,
        height=100,
        channels=3,
    )


def _make_text_block(text: str) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=100, y1=20),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


class TestParseMarkdownTable:
    """Tests for parse_markdown_table."""

    def test_valid_pipe_table(self) -> None:
        md = (
            "| Name | Age |\n"
            "| --- | --- |\n"
            "| Alice | 30 |\n"
            "| Bob | 25 |\n"
        )
        table = parse_markdown_table(md, _make_bbox())
        assert table is not None
        assert table.rows == 3
        assert table.cols == 2
        assert table.bbox == _make_bbox()

    def test_fenced_code_block(self) -> None:
        md = (
            "Here is the table:\n"
            "```\n"
            "| Col1 | Col2 |\n"
            "| --- | --- |\n"
            "| A | B |\n"
            "```\n"
        )
        table = parse_markdown_table(md, _make_bbox())
        assert table is not None
        assert table.rows == 2
        assert table.cols == 2

    def test_no_table_returns_none(self) -> None:
        md = "This is just plain text without any table."
        table = parse_markdown_table(md, _make_bbox())
        assert table is None

    def test_single_row_returns_none(self) -> None:
        md = "| only | one | row |"
        table = parse_markdown_table(md, _make_bbox())
        assert table is None

    def test_single_column_returns_none(self) -> None:
        md = "| A |\n| --- |\n| B |"
        table = parse_markdown_table(md, _make_bbox())
        assert table is None

    def test_separator_rows_filtered(self) -> None:
        md = (
            "| H1 | H2 |\n"
            "| --- | --- |\n"
            "| D1 | D2 |\n"
        )
        table = parse_markdown_table(md, _make_bbox())
        assert table is not None
        # Separator row should be filtered, leaving header + 1 data row
        assert table.rows == 2

    def test_preserves_bbox(self) -> None:
        md = (
            "| A | B |\n"
            "| --- | --- |\n"
            "| C | D |\n"
        )
        bbox = BBox(x0=50.0, y0=100.0, x1=300.0, y1=200.0)
        table = parse_markdown_table(md, bbox)
        assert table is not None
        assert table.bbox == bbox

    def test_confidence_set(self) -> None:
        md = (
            "| A | B |\n"
            "| --- | --- |\n"
            "| C | D |\n"
        )
        table = parse_markdown_table(md, _make_bbox())
        assert table is not None
        assert table.confidence == 0.85

    def test_cells_have_correct_positions(self) -> None:
        md = (
            "| A | B |\n"
            "| --- | --- |\n"
            "| C | D |\n"
        )
        table = parse_markdown_table(md, _make_bbox())
        assert table is not None
        cell_map = {(c.row, c.col): c.text for c in table.cells}
        assert cell_map[(0, 0)] == "A"
        assert cell_map[(0, 1)] == "B"
        assert cell_map[(1, 0)] == "C"
        assert cell_map[(1, 1)] == "D"


class TestExtractTableLines:
    """Tests for _extract_table_lines helper."""

    def test_raw_pipe_lines(self) -> None:
        text = "| A | B |\n| C | D |"
        lines = _extract_table_lines(text)
        assert len(lines) == 2

    def test_fenced_block(self) -> None:
        text = "```\n| A | B |\n| C | D |\n```"
        lines = _extract_table_lines(text)
        assert len(lines) == 2

    def test_no_pipes_returns_empty(self) -> None:
        text = "no tables here"
        lines = _extract_table_lines(text)
        assert len(lines) == 0


class TestRouteTableToVLM:
    """Tests for route_table_to_vlm orchestration."""

    def test_successful_vlm_replacement(self) -> None:
        mock_engine = MagicMock()
        mock_engine.correct_page.return_value = [
            _make_text_block("| Name | Age |"),
            _make_text_block("| --- | --- |"),
            _make_text_block("| Alice | 30 |"),
            _make_text_block("| Bob | 25 |"),
        ]

        table, record = route_table_to_vlm(
            cropped_image=_make_raw_image(),
            original_bbox=_make_bbox(),
            quality_score=0.3,
            page_num=1,
            llm_engine=mock_engine,
        )

        assert table is not None
        assert record.replaced is True
        assert record.page_num == 1
        assert record.quality_score == 0.3

    def test_vlm_exception_returns_none(self) -> None:
        mock_engine = MagicMock()
        mock_engine.correct_page.side_effect = RuntimeError("Model not loaded")

        table, record = route_table_to_vlm(
            cropped_image=_make_raw_image(),
            original_bbox=_make_bbox(),
            quality_score=0.4,
            page_num=2,
            llm_engine=mock_engine,
        )

        assert table is None
        assert record.replaced is False
        assert "failed" in record.reason.lower()

    def test_vlm_returns_no_table(self) -> None:
        mock_engine = MagicMock()
        mock_engine.correct_page.return_value = [
            _make_text_block("I could not find any table in this image."),
        ]

        table, record = route_table_to_vlm(
            cropped_image=_make_raw_image(),
            original_bbox=_make_bbox(),
            quality_score=0.5,
            page_num=3,
            llm_engine=mock_engine,
        )

        assert table is None
        assert record.replaced is False
        assert "valid markdown table" in record.reason.lower()

    def test_domain_hint_passed_to_prompt(self) -> None:
        mock_engine = MagicMock()
        mock_engine.correct_page.return_value = []

        route_table_to_vlm(
            cropped_image=_make_raw_image(),
            original_bbox=_make_bbox(),
            quality_score=0.3,
            page_num=1,
            llm_engine=mock_engine,
            domain_hint="보험약관",
        )

        call_args = mock_engine.correct_page.call_args
        assert "보험약관" in call_args.kwargs.get("prompt_hint", "")

    def test_record_contains_bbox(self) -> None:
        mock_engine = MagicMock()
        mock_engine.correct_page.return_value = []
        bbox = BBox(x0=10.0, y0=20.0, x1=300.0, y1=400.0)

        _, record = route_table_to_vlm(
            cropped_image=_make_raw_image(),
            original_bbox=bbox,
            quality_score=0.2,
            page_num=5,
            llm_engine=mock_engine,
        )

        assert record.table_bbox == bbox
