"""Cross-page table merging.

When a table spans across pages, this module detects the continuation
and merges them into a single table.
"""

from __future__ import annotations

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig


def merge_cross_page_tables(
    pages_tables: list[tuple[list[Table], float, float]],
    config: ParserConfig,
) -> list[list[Table]]:
    """Merge tables that span across consecutive pages.

    Args:
        pages_tables: List of (tables, page_height, page_width) per page.
        config: Parser configuration.

    Returns:
        Updated list of tables per page (merged tables appear on the first page).
    """
    if len(pages_tables) < 2:
        return [tables for tables, _, _ in pages_tables]

    result: list[list[Table]] = [list(tables) for tables, _, _ in pages_tables]

    for i in range(len(result) - 1):
        if not result[i] or not result[i + 1]:
            continue

        _, page_height, _ = pages_tables[i]
        last_table = result[i][-1]
        first_table_next = result[i + 1][0]

        if not _is_continuation_candidate(
            last_table, first_table_next, page_height, pages_tables[i + 1][1], config
        ):
            continue

        merged = _merge_tables(last_table, first_table_next)
        if merged is not None:
            result[i][-1] = merged
            result[i + 1] = result[i + 1][1:]

    return result


def _is_continuation_candidate(
    table_prev: Table,
    table_next: Table,
    prev_page_height: float,
    next_page_height: float,
    config: ParserConfig,
) -> bool:
    """Check if two tables on consecutive pages might be a continuation."""
    # Column count must match
    if table_prev.cols != table_next.cols:
        return False

    # Extreme column count difference means completely different tables
    if abs(table_prev.cols - table_next.cols) > 3:
        return False

    # Previous table should be near the bottom of its page
    bottom_threshold = prev_page_height * (1 - config.table_bottom_ratio)
    if table_prev.bbox.y1 < bottom_threshold:
        return False

    # Next table should be near the top of its page
    top_threshold = next_page_height * config.table_top_ratio
    if table_next.bbox.y0 > top_threshold:
        return False

    return True


def _merge_tables(table_prev: Table, table_next: Table) -> Table | None:
    """Merge two tables, removing duplicate header rows."""
    if table_prev.cols != table_next.cols:
        return None

    # Check if the first row of table_next is a repeated header
    prev_header = _get_row_texts(table_prev, 0)
    next_first_row = _get_row_texts(table_next, 0)

    start_row = 1 if prev_header == next_first_row else 0

    # Build merged cells
    prev_cells = list(table_prev.cells)
    offset = table_prev.rows

    for cell in table_next.cells:
        if cell.row < start_row:
            continue
        prev_cells.append(TableCell(
            text=cell.text,
            row=cell.row - start_row + offset,
            col=cell.col,
            colspan=cell.colspan,
            rowspan=cell.rowspan,
        ))

    merged_rows = offset + table_next.rows - start_row

    return Table(
        cells=tuple(prev_cells),
        rows=merged_rows,
        cols=table_prev.cols,
        bbox=BBox(
            x0=min(table_prev.bbox.x0, table_next.bbox.x0),
            y0=table_prev.bbox.y0,
            x1=max(table_prev.bbox.x1, table_next.bbox.x1),
            y1=table_next.bbox.y1,
        ),
        confidence=min(table_prev.confidence, table_next.confidence),
        needs_review=table_prev.needs_review or table_next.needs_review,
    )


def _get_row_texts(table: Table, row_idx: int) -> list[str]:
    """Get normalized text values for a specific row."""
    cells_in_row = [c for c in table.cells if c.row == row_idx]
    cells_in_row.sort(key=lambda c: c.col)
    return [c.text.strip().lower() for c in cells_in_row]
