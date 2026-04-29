"""Unit tests for table_quality_scorer module."""

from __future__ import annotations

import pytest

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import (
    ACADEMIC_PRESET,
    FINANCIAL_PRESET,
    LEGAL_PRESET,
    ScorerWeights,
)
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


class TestScorerWeights:
    """Validation rules on ScorerWeights."""

    def test_default_sums_to_one(self) -> None:
        weights = ScorerWeights()
        total = weights.empty_ratio + weights.consistency + weights.text_length
        assert abs(total - 1.0) < 1e-6

    def test_invalid_sum_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScorerWeights(0.5, 0.5, 0.5)

    def test_negative_component_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScorerWeights(-0.1, 0.6, 0.5)

    def test_frozen(self) -> None:
        weights = ScorerWeights()
        with pytest.raises(Exception):
            weights.empty_ratio = 0.5  # type: ignore[misc]


class TestPresets:
    """Domain presets must be exposed and usable."""

    def test_legal_preset_matches_historical_weights(self) -> None:
        assert LEGAL_PRESET.empty_ratio == 0.40
        assert LEGAL_PRESET.consistency == 0.35
        assert LEGAL_PRESET.text_length == 0.25

    def test_financial_preset_emphasises_emptiness(self) -> None:
        assert FINANCIAL_PRESET.empty_ratio > LEGAL_PRESET.empty_ratio

    def test_academic_preset_emphasises_text(self) -> None:
        assert ACADEMIC_PRESET.text_length > LEGAL_PRESET.text_length


class TestWeightInjection:
    """``score_table`` must honour custom weights."""

    def test_default_matches_legal_preset(self) -> None:
        cells = [("Hello", 0, 0), ("World", 0, 1), ("A", 1, 0), ("B", 1, 1)]
        table = _make_table(cells, rows=2, cols=2)
        assert score_table(table) == score_table(table, LEGAL_PRESET)

    def test_presets_produce_different_scores(self) -> None:
        # Build a table where text_length and empty_ratio diverge:
        # all cells filled (empty_ratio=1.0) but mostly single-char (text_length=0.0).
        cells = [("A", 0, 0), ("B", 0, 1), ("C", 1, 0), ("D", 1, 1)]
        table = _make_table(cells, rows=2, cols=2)

        legal = score_table(table, LEGAL_PRESET)
        financial = score_table(table, FINANCIAL_PRESET)
        academic = score_table(table, ACADEMIC_PRESET)

        # Financial weighs empty_ratio higher → higher score on a fully-filled
        # but short-text table compared to academic.
        assert financial > academic
        assert legal != academic

    def test_explicit_weights(self) -> None:
        cells = [("Hello", 0, 0), ("", 0, 1)]
        table = _make_table(cells, rows=1, cols=2)
        # All-text-length weighting → 0.5 (one of two cells meaningful).
        weights = ScorerWeights(0.0, 0.0, 1.0)
        assert score_table(table, weights) == 0.5

    def test_score_in_valid_range_for_all_presets(self) -> None:
        cells = [("X", 0, 0)]
        table = _make_table(cells, rows=1, cols=1)
        for preset in (LEGAL_PRESET, FINANCIAL_PRESET, ACADEMIC_PRESET):
            assert 0.0 <= score_table(table, preset) <= 1.0
