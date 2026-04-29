"""pdfplumber-based table extraction adapter.

Implements table extraction for digital PDFs using pdfplumber with
multiple fallback strategies.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pdfplumber

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig

logger = logging.getLogger(__name__)


class PdfplumberTableExtractor:
    """Table extractor using pdfplumber."""

    def __init__(self, config: ParserConfig) -> None:
        self._config = config

    def open(self, path: Path) -> pdfplumber.PDF:
        """Open a PDF with pdfplumber."""
        return pdfplumber.open(str(path))

    def close(self, doc: pdfplumber.PDF) -> None:
        """Close the pdfplumber document."""
        doc.close()

    def extract_from_page(
        self,
        doc: pdfplumber.PDF,
        page_idx: int,
        page_width: float = 0.0,
        page_height: float = 0.0,
    ) -> list[Table]:
        """Extract tables from a single page with fallback strategies.

        Strategy 1: lines_strict (most accurate for well-formed tables)
        Strategy 2: text-based detection (fallback for borderless tables)

        Args:
            doc: pdfplumber PDF document.
            page_idx: Zero-based page index.
            page_width: Page width for layout table filtering (0 = auto).
            page_height: Page height for layout table filtering (0 = auto).
        """
        if page_idx >= len(doc.pages):
            return []

        page = doc.pages[page_idx]

        # Auto-detect page dimensions if not provided
        if page_width <= 0 or page_height <= 0:
            page_width = float(page.width)
            page_height = float(page.height)

        # Strategy 1: lines_strict
        tables = self._extract_with_strategy(page, "lines_strict")
        if tables:
            return self._filter_layout_tables(tables, page_width, page_height)

        # Strategy 2: text-based
        tables = self._extract_with_strategy(page, "text")
        return self._filter_layout_tables(tables, page_width, page_height)

    def extract_from_image(self, image: Any) -> list[Table]:
        """Not supported by pdfplumber - returns empty list.

        Image-based table extraction is handled by PaddleOCR adapter.
        """
        return []

    def _extract_with_strategy(
        self,
        page: pdfplumber.page.Page,
        strategy: str,
    ) -> list[Table]:
        """Extract tables using a specific strategy."""
        settings = {
            "vertical_strategy": strategy,
            "horizontal_strategy": strategy,
            "snap_tolerance": self._config.snap_tolerance,
            "join_tolerance": self._config.join_tolerance,
            "edge_min_length": self._config.edge_min_length,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        }

        try:
            found_tables = page.find_tables(settings)
        except Exception:
            logger.debug("Table extraction with strategy '%s' failed", strategy)
            return []

        result: list[Table] = []

        for table in found_tables:
            try:
                extracted = table.extract()
                if not extracted:
                    continue

                rows = len(extracted)
                cols = max(len(row) for row in extracted) if extracted else 0

                if rows < self._config.min_table_rows or cols < self._config.min_table_cols:
                    continue

                # Detect merged cells from pdfplumber's cell structure
                merged_map = self._detect_merged_cells(table, rows, cols)

                cells: list[TableCell] = []
                for r_idx, row in enumerate(extracted):
                    for c_idx, cell_text in enumerate(row):
                        if c_idx < cols:
                            colspan, rowspan = merged_map.get((r_idx, c_idx), (1, 1))
                            # Skip cells that are part of a merged region (not the origin)
                            if cell_text is None and (r_idx, c_idx) not in merged_map:
                                # None cells in pdfplumber often indicate merged region
                                cell_text = ""
                            cells.append(TableCell(
                                text=cell_text or "",
                                row=r_idx,
                                col=c_idx,
                                colspan=colspan,
                                rowspan=rowspan,
                            ))

                bbox = BBox(
                    x0=table.bbox[0],
                    y0=table.bbox[1],
                    x1=table.bbox[2],
                    y1=table.bbox[3],
                )

                # Filter tables where >90% cells are empty
                non_empty = sum(1 for c in cells if c.text.strip())
                total_cells = max(len(cells), 1)
                if non_empty / total_cells < (1.0 - self._config.empty_cell_threshold):
                    continue

                needs_review = non_empty / total_cells < 0.3

                result.append(Table(
                    cells=tuple(cells),
                    rows=rows,
                    cols=cols,
                    bbox=bbox,
                    needs_review=needs_review,
                ))

            except Exception:
                logger.debug("Failed to extract individual table", exc_info=True)
                continue

        return result

    def _detect_merged_cells(
        self,
        table: object,
        rows: int,
        cols: int,
    ) -> dict[tuple[int, int], tuple[int, int]]:
        """Detect merged cells by analyzing pdfplumber's cell bounding boxes.

        Returns:
            Dict mapping (row, col) -> (colspan, rowspan) for merged cells.
        """
        merged: dict[tuple[int, int], tuple[int, int]] = {}

        try:
            # pdfplumber tables expose cells as list of (x0, y0, x1, y1)
            if not hasattr(table, "cells") or not table.cells:  # type: ignore[union-attr]
                return merged

            raw_cells = table.cells  # type: ignore[union-attr]
            if not raw_cells:
                return merged

            # Build a grid of unique x and y coordinates
            x_coords = sorted(set(c[0] for c in raw_cells) | set(c[2] for c in raw_cells))
            y_coords = sorted(set(c[1] for c in raw_cells) | set(c[3] for c in raw_cells))

            if len(x_coords) < 2 or len(y_coords) < 2:
                return merged

            # Map each raw cell to its grid span
            for cell_bbox in raw_cells:
                x0, y0, x1, y1 = cell_bbox

                # Find column span
                col_start = _find_nearest_idx(x_coords, x0)
                col_end = _find_nearest_idx(x_coords, x1)
                colspan = max(1, col_end - col_start)

                # Find row span
                row_start = _find_nearest_idx(y_coords, y0)
                row_end = _find_nearest_idx(y_coords, y1)
                rowspan = max(1, row_end - row_start)

                if colspan > 1 or rowspan > 1:
                    if row_start < rows and col_start < cols:
                        merged[(row_start, col_start)] = (colspan, rowspan)

        except Exception:
            logger.debug("Merged cell detection failed", exc_info=True)

        return merged

    def _filter_layout_tables(
        self,
        tables: list[Table],
        page_width: float,
        page_height: float,
    ) -> list[Table]:
        """Filter out layout tables that span the full page or have abnormal structure.

        Criteria for filtering:
        1. Table bbox covers >= 80% of page area (layout ruling lines)
        2. Column count >= 10 (abnormal for document tables)
        3. Average cell text length < 3 chars (fragmented layout cells)
        """
        page_area = page_width * page_height
        if page_area <= 0:
            return tables

        filtered: list[Table] = []
        for table in tables:
            bbox = table.bbox
            table_area = (bbox.x1 - bbox.x0) * (bbox.y1 - bbox.y0)
            area_ratio = table_area / page_area

            # Check 1: Full-page layout table
            if area_ratio >= 0.8:
                logger.info(
                    "Filtered layout table: bbox covers %.0f%% of page area "
                    "(rows=%d, cols=%d)",
                    area_ratio * 100, table.rows, table.cols,
                )
                continue

            # Check 2: Abnormal column count
            if table.cols >= 10:
                logger.info(
                    "Filtered abnormal table: %d columns (rows=%d)",
                    table.cols, table.rows,
                )
                continue

            # Check 3: Fragmented layout cells (avg text < 3 chars)
            if table.cells:
                total_len = sum(len(c.text.strip()) for c in table.cells)
                avg_len = total_len / len(table.cells)
                if avg_len < 3.0 and table.rows > 2:
                    logger.info(
                        "Filtered fragmented table: avg cell text %.1f chars "
                        "(rows=%d, cols=%d)",
                        avg_len, table.rows, table.cols,
                    )
                    continue

            filtered.append(table)

        return filtered


def _find_nearest_idx(coords: list[float], value: float, tolerance: float = 3.0) -> int:
    """Find the index of the nearest coordinate within tolerance."""
    for i, c in enumerate(coords):
        if abs(c - value) <= tolerance:
            return i
    # Fallback: find closest
    min_dist = float("inf")
    best_idx = 0
    for i, c in enumerate(coords):
        dist = abs(c - value)
        if dist < min_dist:
            min_dist = dist
            best_idx = i
    return best_idx
