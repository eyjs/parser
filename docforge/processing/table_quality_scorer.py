"""Table quality scorer - pure function for evaluating extracted table quality.

Computes a 0.0~1.0 quality score based on:
- Empty cell ratio
- Row/column consistency
- Cell text length distribution
"""

from __future__ import annotations

from docforge.domain.models import Table


def score_table(table: Table) -> float:
    """Compute quality score for a Table. Returns 0.0~1.0 (higher = better).

    Args:
        table: Domain Table object to evaluate.

    Returns:
        Quality score between 0.0 and 1.0.
    """
    if table.rows == 0 or table.cols == 0 or not table.cells:
        return 0.0

    empty_ratio_score = _score_empty_cells(table)
    consistency_score = _score_row_col_consistency(table)
    text_length_score = _score_text_lengths(table)

    # 가중 평균: 빈 셀 40%, 행열 일관성 35%, 텍스트 길이 25%
    score = (
        empty_ratio_score * 0.40
        + consistency_score * 0.35
        + text_length_score * 0.25
    )
    return max(0.0, min(1.0, score))


def _score_empty_cells(table: Table) -> float:
    """Score based on proportion of non-empty cells. 1.0 = all cells have text."""
    total = len(table.cells)
    if total == 0:
        return 0.0
    non_empty = sum(1 for cell in table.cells if cell.text.strip())
    return non_empty / total


def _score_row_col_consistency(table: Table) -> float:
    """Score based on whether each row has the expected number of columns.

    1.0 = every row has exactly `table.cols` cells.
    """
    if table.rows == 0:
        return 0.0

    cells_per_row: dict[int, int] = {}
    for cell in table.cells:
        cells_per_row[cell.row] = cells_per_row.get(cell.row, 0) + 1

    consistent_rows = 0
    for row_idx in range(table.rows):
        count = cells_per_row.get(row_idx, 0)
        if count == table.cols:
            consistent_rows += 1

    return consistent_rows / table.rows


def _score_text_lengths(table: Table) -> float:
    """Score based on cell text length distribution.

    Penalizes tables where most cells have very short text (<=1 char),
    which often indicates extraction failure.
    1.0 = all cells have meaningful text (>1 char).
    """
    total = len(table.cells)
    if total == 0:
        return 0.0

    meaningful = sum(1 for cell in table.cells if len(cell.text.strip()) > 1)
    return meaningful / total
