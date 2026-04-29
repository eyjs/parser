"""Unit tests for table_quality_scorer module."""

from __future__ import annotations

import pytest

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.processing.table_quality_scorer import (
    score_table,
    _score_empty_cells,
    _score_row_col_consistency,
    _score_text_lengths,
)


def _make_bbox() -> BBox:
    return BBox(x0=0.0, y0=0.0, x1=100.0, y1=50.0)


def _make_table(
    cells: list[tuple[str, int, int]],
    rows: int,
    cols: int,
) -> Table:
    return Table(
        cells=tuple(
            TableCell(text=text, row=r, col=c)
            for text, r, c in cells
        ),
        rows=rows,
        cols=cols,
        bbox=_make_bbox(),
    )


class TestScoreTable:
    """Tests for the main score_table function."""

    def test_empty_table_returns_zero(self) -> None:
        table = Table(
            cells=(), rows=0, cols=0, bbox=_make_bbox(),
        )
        assert score_table(table) == 0.0

    def test_no_cells_returns_zero(self) -> None:
        table = Table(
            cells=(), rows=2, cols=3, bbox=_make_bbox(),
        )
        assert score_table(table) == 0.0

    def test_perfect_table_returns_high_score(self) -> None:
        cells = [
            ("Name", 0, 0), ("Age", 0, 1), ("City", 0, 2),
            ("Alice", 1, 0), ("30", 1, 1), ("Seoul", 1, 2),
            ("Bob", 2, 0), ("25", 2, 1), ("Busan", 2, 2),
        ]
        table = _make_table(cells, rows=3, cols=3)
        score = score_table(table)
        assert score > 0.8

    def test_mostly_empty_table_returns_low_score(self) -> None:
        cells = [
            ("", 0, 0), ("", 0, 1), ("A", 0, 2),
            ("", 1, 0), ("", 1, 1), ("", 1, 2),
        ]
        table = _make_table(cells, rows=2, cols=3)
        score = score_table(table)
        assert score < 0.5

    def test_score_in_valid_range(self) -> None:
        cells = [
            ("Hello", 0, 0), ("World", 0, 1),
            ("Foo", 1, 0), ("", 1, 1),
        ]
        table = _make_table(cells, rows=2, cols=2)
        score = score_table(table)
        assert 0.0 <= score <= 1.0

    def test_inconsistent_columns_lowers_score(self) -> None:
        # Row 0 has 3 cells, row 1 has only 2 (cols=3)
        cells = [
            ("A", 0, 0), ("B", 0, 1), ("C", 0, 2),
            ("D", 1, 0), ("E", 1, 1),
        ]
        table = _make_table(cells, rows=2, cols=3)
        score = score_table(table)

        # Compare with consistent table
        cells_consistent = [
            ("A", 0, 0), ("B", 0, 1), ("C", 0, 2),
            ("D", 1, 0), ("E", 1, 1), ("F", 1, 2),
        ]
        table_consistent = _make_table(cells_consistent, rows=2, cols=3)
        score_consistent = score_table(table_consistent)

        assert score < score_consistent

    def test_single_char_cells_lower_score(self) -> None:
        # All single-char cells
        cells = [
            ("A", 0, 0), ("B", 0, 1),
            ("C", 1, 0), ("D", 1, 1),
        ]
        table = _make_table(cells, rows=2, cols=2)
        score_short = score_table(table)

        # Multi-char cells
        cells_long = [
            ("Alice", 0, 0), ("Bob", 0, 1),
            ("Carol", 1, 0), ("Dave", 1, 1),
        ]
        table_long = _make_table(cells_long, rows=2, cols=2)
        score_long = score_table(table_long)

        assert score_short < score_long

    def test_deterministic_output(self) -> None:
        cells = [
            ("A", 0, 0), ("BB", 0, 1),
            ("CCC", 1, 0), ("", 1, 1),
        ]
        table = _make_table(cells, rows=2, cols=2)
        scores = [score_table(table) for _ in range(10)]
        assert all(s == scores[0] for s in scores)


class TestSubScorers:
    """Tests for individual scoring components."""

    def test_empty_cells_all_filled(self) -> None:
        cells = [("A", 0, 0), ("B", 0, 1)]
        table = _make_table(cells, rows=1, cols=2)
        assert _score_empty_cells(table) == 1.0

    def test_empty_cells_all_empty(self) -> None:
        cells = [("", 0, 0), ("  ", 0, 1)]
        table = _make_table(cells, rows=1, cols=2)
        assert _score_empty_cells(table) == 0.0

    def test_consistency_perfect(self) -> None:
        cells = [
            ("A", 0, 0), ("B", 0, 1),
            ("C", 1, 0), ("D", 1, 1),
        ]
        table = _make_table(cells, rows=2, cols=2)
        assert _score_row_col_consistency(table) == 1.0

    def test_consistency_one_row_missing(self) -> None:
        cells = [
            ("A", 0, 0), ("B", 0, 1),
            ("C", 1, 0),
        ]
        table = _make_table(cells, rows=2, cols=2)
        assert _score_row_col_consistency(table) == 0.5

    def test_text_length_all_meaningful(self) -> None:
        cells = [("Hello", 0, 0), ("World", 0, 1)]
        table = _make_table(cells, rows=1, cols=2)
        assert _score_text_lengths(table) == 1.0

    def test_text_length_all_short(self) -> None:
        cells = [("A", 0, 0), ("B", 0, 1)]
        table = _make_table(cells, rows=1, cols=2)
        assert _score_text_lengths(table) == 0.0
