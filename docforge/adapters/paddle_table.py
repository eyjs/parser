"""PP-StructureV3 adapter for image-based table extraction on scanned PDFs.

PaddleOCR Structure is an optional dependency. This adapter gracefully
handles the case where it is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)

_ppstructure_available: bool | None = None
_engine_instance: Any = None


def _check_availability() -> bool:
    """Check if PP-Structure is installed and importable."""
    global _ppstructure_available
    if _ppstructure_available is not None:
        return _ppstructure_available

    try:
        from paddleocr import PPStructure  # noqa: F401
        _ppstructure_available = True
    except ImportError:
        _ppstructure_available = False
        logger.info("PP-Structure not installed - image table extraction disabled")

    return _ppstructure_available


def _get_engine_instance() -> Any:
    """Get or create PP-Structure instance (singleton)."""
    global _engine_instance
    if _engine_instance is not None:
        return _engine_instance

    if not _check_availability():
        return None

    from paddleocr import PPStructure

    _engine_instance = PPStructure(
        table=True,
        ocr=True,
        show_log=False,
        lang="korean",
    )
    return _engine_instance


class PaddleTableExtractor:
    """Image-based table extractor using PP-StructureV3."""

    def is_available(self) -> bool:
        """Check if PP-Structure is installed and ready."""
        return _check_availability()

    def extract_from_page(self, source: Any, page_idx: int) -> list[Table]:
        """Not applicable for image-based extraction.

        Use extract_from_image instead.
        """
        return []

    def extract_from_image(self, image: Any) -> list[Table]:
        """Extract tables from a page image.

        Args:
            image: PIL.Image.Image object.

        Returns:
            List of Table objects extracted from the image.
        """
        if not self.is_available():
            logger.warning("PP-Structure not available - returning empty results")
            return []

        engine = _get_engine_instance()
        if engine is None:
            return []

        import numpy as np
        from PIL import Image

        if isinstance(image, Image.Image):
            img_array = np.array(image)
        else:
            img_array = image

        try:
            results = engine(img_array)
        except Exception:
            logger.error("PP-Structure table extraction failed", exc_info=True)
            return []

        tables: list[Table] = []

        for region in results:
            if region.get("type") != "table":
                continue

            html = region.get("res", {}).get("html", "")
            box = region.get("bbox", [0, 0, 0, 0])

            if not html:
                continue

            table = self._html_table_to_domain(html, box)
            if table is not None:
                tables.append(table)

        return tables

    def _html_table_to_domain(
        self,
        html: str,
        box: list[float],
    ) -> Table | None:
        """Convert PP-Structure HTML table output to domain Table model."""
        import re

        rows_html = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
        if not rows_html:
            return None

        num_rows = len(rows_html)
        raw_rows: list[list[tuple[str, int, int]]] = []

        for row_html in rows_html:
            row_cells: list[tuple[str, int, int]] = []
            for m in re.finditer(r"<t[dh]([^>]*)>(.+?)</t[dh]>", row_html, re.DOTALL):
                attrs, content = m.group(1), m.group(2)
                text = re.sub(r"<[^>]+>", "", content).strip()
                cs_m = re.search(r'colspan\s*=\s*"?(\d+)', attrs)
                rs_m = re.search(r'rowspan\s*=\s*"?(\d+)', attrs)
                cs = int(cs_m.group(1)) if cs_m else 1
                rs = int(rs_m.group(1)) if rs_m else 1
                row_cells.append((text, cs, rs))
            raw_rows.append(row_cells)

        if not raw_rows:
            return None

        _COL_SAFETY_MARGIN = 8
        max_cols = max((sum(cs for _, cs, _ in row) for row in raw_rows), default=0)
        grid_width = max_cols + _COL_SAFETY_MARGIN
        occupied = [[False] * grid_width for _ in range(num_rows)]
        cells: list[TableCell] = []

        for r_idx, row_cells in enumerate(raw_rows):
            c_idx = 0
            for text, cs, rs in row_cells:
                while c_idx < grid_width and occupied[r_idx][c_idx]:
                    c_idx += 1
                cells.append(TableCell(text=text, row=r_idx, col=c_idx, colspan=cs, rowspan=rs))
                for dr in range(rs):
                    for dc in range(cs):
                        rr, cc = r_idx + dr, c_idx + dc
                        if rr < num_rows and cc < grid_width:
                            occupied[rr][cc] = True
                c_idx += cs

        actual_cols = max((c.col + c.colspan for c in cells), default=0)

        if not cells or num_rows < 2 or actual_cols < 2:
            return None

        return Table(
            cells=tuple(cells),
            rows=num_rows,
            cols=actual_cols,
            bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
            confidence=0.8,
            needs_review=True,
        )
