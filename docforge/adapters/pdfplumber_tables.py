"""pdfplumber-based table extraction adapter.

Implements table extraction for digital PDFs using pdfplumber with
multiple fallback strategies.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig

logger = logging.getLogger(__name__)

# --- Strategy 4: Word-grid reconstruction for borderless tables ---

_AIRLINE_PATTERN_RE = re.compile(
    r"(?:"
    r"[A-Z]{2}\s*\d{2,4}"            # flight number: OZ 545, KE 123
    r"|TERMINAL"
    r"|ECONOMY|BUSINESS|FIRST"
    r"|BOARDING\s*PASS"
    r"|GATE"
    r"|(?:SEAT|좌석)"
    r"|(?:ICN|GMP|PUS|CJU|NRT|HND|PEK|PVG|SIN|BKK|PRG|CDG|LHR|FRA|JFK|LAX)"
    r"|(?:편명|항공편|출발|도착|탑승)"
    r")",
    re.IGNORECASE,
)

# Minimum number of airline-pattern matches to consider the page an eticket
_MIN_AIRLINE_MATCHES = 3
# Minimum columns to consider a word-grid as a table
_MIN_WORD_GRID_COLS = 3
# Y-tolerance for grouping words into the same row (in PDF points)
_WORD_GRID_Y_TOLERANCE = 3.0
# Minimum gap ratio (of page width) for column boundary detection
_WORD_GRID_MIN_GAP_RATIO = 0.02
# Y-padding above first header and below last data row for itinerary section
_ITINERARY_Y_PAD = 5.0

_ITINERARY_HEADER_RE = re.compile(
    r"(?:도시|공항|일자|시각|터미널|클래스|비행시간|상태"
    r"|CITY|AIRPORT|DATE|TIME|CLASS|STATUS|FLIGHT)",
    re.IGNORECASE,
)
_MIN_HEADER_MATCHES = 3

_ITINERARY_END_RE = re.compile(
    r"(?:항공권\s*정보|Ticket\s*Information|항공사\s*공지|Airline\s*Notice"
    r"|드리는\s*말씀|Remarks|제한사항|Restriction)",
    re.IGNORECASE,
)


def _has_airline_pattern(words: list[dict]) -> bool:
    """Check if word list contains enough airline-related patterns."""
    text = " ".join(w.get("text", "") for w in words)
    matches = _AIRLINE_PATTERN_RE.findall(text)
    return len(matches) >= _MIN_AIRLINE_MATCHES


def _find_itinerary_rows(
    rows: list[list[dict]],
) -> list[list[dict]]:
    """Find rows belonging to the itinerary section.

    Identifies header rows (containing >=3 column-header keywords like
    도시/공항, 일자/시각, etc.), then collects all rows between
    the first header and the last data row within the itinerary y-range.
    """
    header_indices: list[int] = []
    for i, row in enumerate(rows):
        row_text = " ".join(w.get("text", "") for w in row)
        if len(_ITINERARY_HEADER_RE.findall(row_text)) >= _MIN_HEADER_MATCHES:
            header_indices.append(i)

    if not header_indices:
        return rows

    first_header_y = min(w["top"] for w in rows[header_indices[0]])
    last_header_idx = header_indices[-1]

    y_min = first_header_y - _ITINERARY_Y_PAD

    end_idx = len(rows)
    for i in range(last_header_idx + 1, len(rows)):
        row_text = " ".join(w.get("text", "") for w in rows[i])
        if _ITINERARY_END_RE.search(row_text):
            end_idx = i
            break

    itinerary_rows: list[list[dict]] = []
    for row in rows[:end_idx]:
        row_y = min(w["top"] for w in row)
        if row_y >= y_min:
            itinerary_rows.append(row)

    return itinerary_rows if len(itinerary_rows) >= 2 else rows


def _group_words_by_row(
    words: list[dict],
    y_tolerance: float = _WORD_GRID_Y_TOLERANCE,
) -> list[list[dict]]:
    """Group words into rows based on y-coordinate proximity.

    Words within ``y_tolerance`` points of each other's top coordinate
    are considered to be on the same row.
    """
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = []
    current_row: list[dict] = [sorted_words[0]]
    current_y = sorted_words[0]["top"]

    for word in sorted_words[1:]:
        if abs(word["top"] - current_y) <= y_tolerance:
            current_row.append(word)
        else:
            current_row.sort(key=lambda w: w["x0"])
            rows.append(current_row)
            current_row = [word]
            current_y = word["top"]

    if current_row:
        current_row.sort(key=lambda w: w["x0"])
        rows.append(current_row)

    return rows


def _detect_column_boundaries(
    rows: list[list[dict]],
    page_width: float,
    min_gap_ratio: float = _WORD_GRID_MIN_GAP_RATIO,
) -> list[float]:
    """Detect column boundary x-coordinates from per-row gap clustering.

    For each row with >=2 words, finds gaps between consecutive words.
    Gap midpoints are collected, sorted, and clustered by proximity (15pt).
    Clusters appearing in >=20% of multi-word rows indicate column boundaries.
    """
    min_gap = page_width * min_gap_ratio
    cluster_tolerance = 25.0

    multi_word_rows = [r for r in rows if len(r) >= 2]
    if not multi_word_rows:
        return []

    all_midpoints: list[float] = []
    for row in multi_word_rows:
        sorted_words = sorted(row, key=lambda w: w["x0"])
        row_mids: set[float] = set()
        for i in range(1, len(sorted_words)):
            gap_start = sorted_words[i - 1]["x1"]
            gap_end = sorted_words[i]["x0"]
            if gap_end - gap_start >= min_gap:
                row_mids.add((gap_start + gap_end) / 2)
        all_midpoints.extend(row_mids)

    if not all_midpoints:
        return []

    all_midpoints.sort()
    clusters: list[list[float]] = [[all_midpoints[0]]]
    for mp in all_midpoints[1:]:
        if mp - clusters[-1][-1] <= cluster_tolerance:
            clusters[-1].append(mp)
        else:
            clusters.append([mp])

    threshold = max(len(multi_word_rows) * 0.2, 2)
    boundaries: list[float] = []
    for cluster in clusters:
        if len(cluster) >= threshold:
            boundaries.append(sum(cluster) / len(cluster))

    return sorted(boundaries)


def _assign_words_to_columns(
    row_words: list[dict],
    col_boundaries: list[float],
) -> list[str]:
    """Assign words in a row to column slots based on column boundaries.

    Returns a list of cell texts, one per column (num_columns = len(boundaries) + 1).
    """
    num_cols = len(col_boundaries) + 1
    cells: list[list[str]] = [[] for _ in range(num_cols)]

    for word in row_words:
        word_center = (word["x0"] + word["x1"]) / 2
        col_idx = 0
        for boundary in col_boundaries:
            if word_center > boundary:
                col_idx += 1
            else:
                break
        col_idx = min(col_idx, num_cols - 1)
        cells[col_idx].append(word.get("text", ""))

    return [" ".join(parts).strip() for parts in cells]


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

        # Strategy 4: Word-grid reconstruction for borderless tables
        # (e.g. airline e-ticket itinerary). Only runs when Strategies 1-3
        # produced no tables and the page contains airline-related patterns.
        if not tables:
            word_grid_tables = self._reconstruct_word_grid(
                page, page_width, page_height,
            )
            tables.extend(word_grid_tables)

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

    def _reconstruct_word_grid(
        self,
        page: pdfplumber.page.Page,
        page_width: float,
        page_height: float,
    ) -> list[Table]:
        """Strategy 4: Reconstruct a table from word-level bounding boxes.

        Extracts words from the page, checks for airline e-ticket patterns,
        groups words into rows by y-coordinate, detects column boundaries
        from x-coordinate gaps, and builds a Table object.

        Only activates when airline-related patterns are detected to avoid
        false positives on general documents.
        """
        try:
            words = page.extract_words(
                x_tolerance=1,
                y_tolerance=3,
            )
        except Exception:
            logger.debug("Strategy 4: extract_words failed", exc_info=True)
            return []

        if not words or not _has_airline_pattern(words):
            return []

        all_rows = _group_words_by_row(words, _WORD_GRID_Y_TOLERANCE)
        if len(all_rows) < 2:
            return []

        itinerary_rows = _find_itinerary_rows(all_rows)
        col_boundaries = _detect_column_boundaries(itinerary_rows, page_width)
        num_cols = len(col_boundaries) + 1

        if num_cols < _MIN_WORD_GRID_COLS:
            return []

        # Build grid from itinerary rows only
        grid: list[list[str]] = []
        itinerary_words: list[dict] = []
        for row_words in itinerary_rows:
            row_cells = _assign_words_to_columns(row_words, col_boundaries)
            if any(cell.strip() for cell in row_cells):
                grid.append(row_cells)
                itinerary_words.extend(row_words)

        if len(grid) < 2:
            return []

        # Build Table object
        cells: list[TableCell] = []
        for r_idx, row in enumerate(grid):
            for c_idx, cell_text in enumerate(row):
                cells.append(TableCell(
                    text=cell_text,
                    row=r_idx,
                    col=c_idx,
                ))

        # Compute bounding box from itinerary words
        bbox_words = itinerary_words if itinerary_words else words
        all_x0 = min(w["x0"] for w in bbox_words)
        all_y0 = min(w["top"] for w in bbox_words)
        all_x1 = max(w["x1"] for w in bbox_words)
        all_y1 = max(w["bottom"] for w in bbox_words)

        table = Table(
            cells=tuple(cells),
            rows=len(grid),
            cols=num_cols,
            bbox=BBox(x0=all_x0, y0=all_y0, x1=all_x1, y1=all_y1),
            source="word_grid",
        )

        logger.info(
            "Strategy 4 (word-grid) reconstructed table: %d rows x %d cols "
            "from bbox (%.1f,%.1f,%.1f,%.1f)",
            len(grid), num_cols, all_x0, all_y0, all_x1, all_y1,
        )

        return [table]

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
            # Word-grid reconstructed tables bypass layout filtering
            if getattr(table, "source", "") == "word_grid":
                filtered.append(table)
                continue

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
                if table.cells:
                    cell_texts = [c.text.strip() for c in table.cells]
                    non_empty = [t for t in cell_texts if t]
                    fill_rate = len(non_empty) / max(len(cell_texts), 1)
                    avg_len = (
                        sum(len(t) for t in non_empty) / max(len(non_empty), 1)
                        if non_empty else 0
                    )
                    if fill_rate >= 0.5 and avg_len < 40:
                        filtered.append(table)
                        continue
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
