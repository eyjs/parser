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
        table_hint_bboxes: list[BBox] | None = None,
        page_dpi: int | None = None,
    ) -> list[Table]:
        """Extract tables from a single page with fallback strategies.

        Strategy 1: lines_strict (most accurate for well-formed tables)
        Strategy 2: text-based detection (fallback for borderless tables)
        Strategy 3: Surya TABLE hint bbox-constrained extraction (P0-5)

        Args:
            doc: pdfplumber PDF document.
            page_idx: Zero-based page index.
            page_width: Page width for layout table filtering (0 = auto).
            page_height: Page height for layout table filtering (0 = auto).
            table_hint_bboxes: Surya TABLE region bboxes for hint-constrained
                extraction. When provided, any hint region not covered by an
                already-detected table triggers a bbox-constrained retry.
            page_dpi: Document DPI for adaptive tolerance calculation (P0-6).
                When None, falls back to fixed config values.
        """
        if page_idx >= len(doc.pages):
            return []

        page = doc.pages[page_idx]

        # Auto-detect page dimensions if not provided
        if page_width <= 0 or page_height <= 0:
            page_width = float(page.width)
            page_height = float(page.height)

        # Strategy 1: lines_strict (fixed tolerance — lines are precise)
        tables = self._extract_with_strategy(page, "lines_strict")
        if tables:
            tables = self._filter_layout_tables(tables, page_width, page_height)
        else:
            # Strategy 2: text-based with adaptive tolerance (P0-6)
            tables = self._extract_with_strategy(page, "text", page_dpi=page_dpi)
            tables = self._filter_layout_tables(tables, page_width, page_height)

        # Strategy 3: Surya TABLE hint — attempt bbox-constrained extraction
        # for hint regions not already covered by detected tables.
        if table_hint_bboxes:
            hint_tables = self._extract_from_hints(
                page, tables, table_hint_bboxes, page_width, page_height,
            )
            tables.extend(hint_tables)

        return tables

    def extract_from_image(self, image: Any) -> list[Table]:
        """Not supported by pdfplumber - returns empty list.

        Image-based table extraction is handled by PaddleOCR adapter.
        """
        return []

    def _compute_adaptive_tolerance(
        self,
        page_dpi: int | None = None,
    ) -> tuple[int, int]:
        """Compute DPI-adaptive snap and join tolerances (P0-6).

        For the ``text`` strategy (borderless tables), fixed tolerance
        values cause missed detections on high-DPI scans and false
        positives on low-DPI documents.  Scaling by DPI ratio keeps
        the tolerance proportional to the document's spatial resolution.

        Returns:
            (snap_tolerance, join_tolerance) as integers clamped to [3, 15].
        """
        config = self._config
        if not config.adaptive_tolerance_enabled or page_dpi is None:
            return config.snap_tolerance, config.join_tolerance

        base = max(config.base_dpi, 1)
        scale = max(page_dpi, 72) / base
        snap = round(config.snap_tolerance * scale)
        join = round(config.join_tolerance * scale)
        snap = max(3, min(25, snap))
        join = max(3, min(25, join))
        return snap, join

    def _extract_with_strategy(
        self,
        page: pdfplumber.page.Page,
        strategy: str,
        page_dpi: int | None = None,
    ) -> list[Table]:
        """Extract tables using a specific strategy."""
        # P0-6: Use adaptive tolerance for "text" strategy
        if strategy == "text":
            snap_tol, join_tol = self._compute_adaptive_tolerance(page_dpi)
        else:
            snap_tol = self._config.snap_tolerance
            join_tol = self._config.join_tolerance

        settings = {
            "vertical_strategy": strategy,
            "horizontal_strategy": strategy,
            "snap_tolerance": snap_tol,
            "join_tolerance": join_tol,
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

    def extract_from_bbox(
        self,
        page: pdfplumber.page.Page,
        bbox: BBox,
        page_width: float = 0.0,
        page_height: float = 0.0,
    ) -> list[Table]:
        """Extract tables within a specific bounding box region.

        Crops the page to the bbox region and runs text-strategy extraction
        with the configured tolerances. Used for Surya TABLE hint-driven
        extraction (P0-5/P0-6).

        Args:
            page: pdfplumber page object.
            bbox: Region bounding box to constrain extraction.
            page_width: Page width for layout filtering (0 = auto).
            page_height: Page height for layout filtering (0 = auto).
        """
        if page_width <= 0 or page_height <= 0:
            page_width = float(page.width)
            page_height = float(page.height)

        try:
            cropped = page.crop((bbox.x0, bbox.y0, bbox.x1, bbox.y1))
        except Exception:
            logger.debug(
                "Failed to crop page for bbox (%.1f,%.1f,%.1f,%.1f)",
                bbox.x0, bbox.y0, bbox.x1, bbox.y1,
            )
            return []

        tables = self._extract_with_strategy(cropped, "text")
        if not tables:
            return []

        # Re-map table bboxes from cropped coordinates back to page coordinates
        remapped: list[Table] = []
        for table in tables:
            adjusted_bbox = BBox(
                x0=table.bbox.x0 + bbox.x0,
                y0=table.bbox.y0 + bbox.y0,
                x1=table.bbox.x1 + bbox.x0,
                y1=table.bbox.y1 + bbox.y0,
            )
            remapped.append(Table(
                cells=table.cells,
                rows=table.rows,
                cols=table.cols,
                bbox=adjusted_bbox,
                confidence=table.confidence,
                needs_review=table.needs_review,
            ))

        return self._filter_layout_tables(remapped, page_width, page_height)

    def _extract_from_hints(
        self,
        page: pdfplumber.page.Page,
        existing_tables: list[Table],
        hint_bboxes: list[BBox],
        page_width: float,
        page_height: float,
    ) -> list[Table]:
        """Extract tables from Surya TABLE hint regions not already covered.

        For each hint bbox, checks whether an existing table already overlaps
        (IoU > 0.3). If not, attempts bbox-constrained extraction.
        """
        iou_threshold = 0.3
        new_tables: list[Table] = []

        for hint_bbox in hint_bboxes:
            # Check if any existing table already covers this hint
            covered = any(
                hint_bbox.iou(t.bbox) > iou_threshold
                for t in existing_tables
            )
            if covered:
                continue

            hint_tables = self.extract_from_bbox(
                page, hint_bbox, page_width, page_height,
            )
            if hint_tables:
                new_tables.extend(hint_tables)
                logger.info(
                    "Surya TABLE hint extracted %d table(s) from bbox "
                    "(%.1f,%.1f,%.1f,%.1f)",
                    len(hint_tables),
                    hint_bbox.x0, hint_bbox.y0, hint_bbox.x1, hint_bbox.y1,
                )

        return new_tables

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
        4. Large-area table (>= 60%) with few columns and paragraph-length cells
        5. Single-column table (not a real table)
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

            # Check 4: Large table with paragraph-length cells (document layout)
            if area_ratio >= 0.4 and table.cells:
                cell_texts = [c.text.strip() for c in table.cells if c.text.strip()]
                long_cells = sum(1 for t in cell_texts if len(t) > 80)
                if long_cells >= 2 or (cell_texts and long_cells / max(len(cell_texts), 1) >= 0.3):
                    logger.info(
                        "Filtered document-layout table: %.0f%% page area, "
                        "%d long cells (rows=%d, cols=%d)",
                        area_ratio * 100, long_cells, table.rows, table.cols,
                    )
                    continue

            # Check 5: Single-column table (not a real table)
            if table.cols == 1 and table.rows >= 2:
                logger.info(
                    "Filtered single-column table: rows=%d",
                    table.rows,
                )
                continue

            # Check 6: Wide sparse table (many cols, few rows) — layout artifact
            if table.cols >= 6 and table.rows <= 3 and area_ratio >= 0.25:
                logger.info(
                    "Filtered wide sparse table: cols=%d, rows=%d, "
                    "%.0f%% page area",
                    table.cols, table.rows, area_ratio * 100,
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
