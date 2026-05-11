"""Multi-column layout detection and reading order restoration.

Analyzes text block x-coordinates to detect 2/3-column layouts and
reorders blocks from left-to-right, top-to-bottom within each column.

When ML layout blocks (Docling/Surya) are available,
``reorder_blocks_by_layout`` uses their reading order instead of
heuristic gap analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from docforge.domain.models import LayoutBlock, TextBlock

logger = logging.getLogger(__name__)

# Minimum gap between columns (as fraction of page width)
_MIN_COLUMN_GAP_RATIO = 0.05
# Minimum blocks per column to consider it a real column
_MIN_BLOCKS_PER_COLUMN = 3
# Maximum columns to detect
_MAX_COLUMNS = 4


@dataclass(frozen=True)
class ColumnLayout:
    """Detected column layout for a page."""

    num_columns: int
    column_boundaries: tuple[tuple[float, float], ...]  # (x_start, x_end) per column


def detect_columns(
    blocks: Sequence[TextBlock],
    page_width: float,
) -> ColumnLayout:
    """Detect multi-column layout from text block positions.

    Uses gap analysis on block x-coordinates to find column boundaries.

    Args:
        blocks: Text blocks on the page.
        page_width: Width of the page.

    Returns:
        ColumnLayout with detected column count and boundaries.
    """
    if not blocks or page_width <= 0:
        return ColumnLayout(num_columns=1, column_boundaries=((0.0, page_width),))

    # Collect all block x0 and x1 coordinates
    x_ranges: list[tuple[float, float]] = [(b.bbox.x0, b.bbox.x1) for b in blocks]

    if len(x_ranges) < _MIN_BLOCKS_PER_COLUMN * 2:
        return ColumnLayout(num_columns=1, column_boundaries=((0.0, page_width),))

    # Find gaps in the x-coordinate space
    # Sort blocks by x0, then look for significant gaps
    sorted_ranges = sorted(x_ranges, key=lambda r: r[0])

    # Build coverage intervals and find gaps
    min_gap = page_width * _MIN_COLUMN_GAP_RATIO
    gaps = _find_x_gaps(sorted_ranges, page_width, min_gap)

    if not gaps:
        return ColumnLayout(num_columns=1, column_boundaries=((0.0, page_width),))

    # Build column boundaries from gaps
    boundaries: list[tuple[float, float]] = []
    prev_end = 0.0
    for gap_start, gap_end in gaps:
        if gap_start > prev_end:
            boundaries.append((prev_end, gap_start))
        prev_end = gap_end
    if prev_end < page_width:
        boundaries.append((prev_end, page_width))

    # Validate: each column must have enough blocks
    validated = _validate_columns(boundaries, blocks)

    if len(validated) <= 1:
        return ColumnLayout(num_columns=1, column_boundaries=((0.0, page_width),))

    if len(validated) > _MAX_COLUMNS:
        return ColumnLayout(num_columns=1, column_boundaries=((0.0, page_width),))

    logger.info("Detected %d-column layout", len(validated))
    return ColumnLayout(
        num_columns=len(validated),
        column_boundaries=tuple(validated),
    )


def reorder_blocks_by_columns(
    blocks: list[TextBlock],
    layout: ColumnLayout,
) -> list[TextBlock]:
    """Reorder text blocks following column reading order: left-to-right, top-to-bottom.

    Args:
        blocks: Text blocks on the page.
        layout: Detected column layout.

    Returns:
        Reordered blocks.
    """
    if layout.num_columns <= 1:
        return blocks

    # Assign each block to a column
    column_blocks: list[list[TextBlock]] = [[] for _ in range(layout.num_columns)]

    for block in blocks:
        col_idx = _assign_to_column(block, layout)
        column_blocks[col_idx].append(block)

    # Sort within each column by y-coordinate (top to bottom)
    result: list[TextBlock] = []
    for col in column_blocks:
        col.sort(key=lambda b: (b.bbox.y0, b.bbox.x0))
        result.extend(col)

    return result


def _find_x_gaps(
    sorted_ranges: list[tuple[float, float]],
    page_width: float,
    min_gap: float,
) -> list[tuple[float, float]]:
    """Find significant gaps in x-coordinate coverage.

    Args:
        sorted_ranges: Block x-ranges sorted by x0.
        page_width: Page width.
        min_gap: Minimum gap width to consider.

    Returns:
        List of (gap_start, gap_end) tuples.
    """
    # Merge overlapping ranges
    merged: list[tuple[float, float]] = []
    for x0, x1 in sorted_ranges:
        if merged and x0 <= merged[-1][1] + 2.0:  # small tolerance
            merged[-1] = (merged[-1][0], max(merged[-1][1], x1))
        else:
            merged.append((x0, x1))

    # Find gaps between merged ranges
    gaps: list[tuple[float, float]] = []
    for i in range(1, len(merged)):
        gap_start = merged[i - 1][1]
        gap_end = merged[i][0]
        gap_width = gap_end - gap_start
        if gap_width >= min_gap:
            gaps.append((gap_start, gap_end))

    return gaps


def _validate_columns(
    boundaries: list[tuple[float, float]],
    blocks: Sequence[TextBlock],
) -> list[tuple[float, float]]:
    """Validate that each column has enough blocks."""
    valid: list[tuple[float, float]] = []
    for col_start, col_end in boundaries:
        count = sum(
            1 for b in blocks
            if b.bbox.center_x >= col_start and b.bbox.center_x <= col_end
        )
        if count >= _MIN_BLOCKS_PER_COLUMN:
            valid.append((col_start, col_end))
    return valid


def _assign_to_column(block: TextBlock, layout: ColumnLayout) -> int:
    """Assign a block to the best-matching column."""
    center_x = block.bbox.center_x
    best_col = 0
    best_dist = float("inf")

    for i, (col_start, col_end) in enumerate(layout.column_boundaries):
        col_center = (col_start + col_end) / 2
        dist = abs(center_x - col_center)
        if dist < best_dist:
            best_dist = dist
            best_col = i

    return best_col


def reorder_blocks_by_layout(
    blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock],
    iou_threshold: float = 0.3,
) -> list[TextBlock]:
    """Reorder text blocks to match ML layout-block reading order.

    For each layout block (in order), find the best-matching text block
    by IoU. Matched blocks are emitted in layout order; unmatched blocks
    are appended at the end sorted by y-coordinate.

    Args:
        blocks: Text blocks from PyMuPDF extraction.
        layout_blocks: Layout blocks from Docling/Surya (in reading order).
        iou_threshold: Minimum IoU to consider a match.

    Returns:
        Reordered text blocks.
    """
    if not layout_blocks or not blocks:
        return blocks

    remaining = list(blocks)
    ordered: list[TextBlock] = []

    for lb in layout_blocks:
        best_tb: TextBlock | None = None
        best_iou = 0.0
        best_idx = -1

        for i, tb in enumerate(remaining):
            iou = tb.bbox.iou(lb.bbox)
            if iou > best_iou and iou >= iou_threshold:
                best_iou = iou
                best_tb = tb
                best_idx = i

        if best_tb is not None:
            ordered.append(best_tb)
            remaining.pop(best_idx)

    # Append unmatched blocks sorted by y-coordinate
    remaining.sort(key=lambda b: (b.bbox.y0, b.bbox.x0))
    ordered.extend(remaining)

    return ordered
