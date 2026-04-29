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

        # Extract rows from HTML
        rows_html = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
        if not rows_html:
            return None

        cells: list[TableCell] = []
        max_cols = 0

        for r_idx, row_html in enumerate(rows_html):
            # Match both <td> and <th> cells
            cell_matches = re.findall(
                r"<t[dh](?:\s+[^>]*)?>(.+?)</t[dh]>",
                row_html,
                re.DOTALL,
            )
            for c_idx, cell_text in enumerate(cell_matches):
                # Strip HTML tags from cell content
                clean_text = re.sub(r"<[^>]+>", "", cell_text).strip()
                cells.append(TableCell(
                    text=clean_text,
                    row=r_idx,
                    col=c_idx,
                ))
            max_cols = max(max_cols, len(cell_matches))

        if not cells or len(rows_html) < 2 or max_cols < 2:
            return None

        return Table(
            cells=tuple(cells),
            rows=len(rows_html),
            cols=max_cols,
            bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
            confidence=0.8,
            needs_review=True,  # Image-based extraction is less reliable
        )
