"""Region cropping helper for extracting table regions from PDF pages.

Uses PyMuPDF (fitz) to render and crop specific regions.
Handles coordinate conversion from pdfplumber PDF points to pixel space.
"""

from __future__ import annotations

import logging
from pathlib import Path

from docforge.domain.value_objects import BBox, RawImage

logger = logging.getLogger(__name__)


def crop_table_region(
    pdf_path: Path,
    page_idx: int,
    table_bbox: BBox,
    dpi: int = 300,
    padding: float = 5.0,
) -> RawImage | None:
    """Crop a table region from a PDF page and return as RawImage.

    Args:
        pdf_path: Path to the PDF file.
        page_idx: Zero-based page index.
        table_bbox: Table BBox in pdfplumber PDF point coordinates.
        dpi: Resolution for rendering.
        padding: Extra padding (in PDF points) around the table bbox.

    Returns:
        RawImage of the cropped region, or None on failure.
    """
    try:
        import fitz
        import numpy as np
    except ImportError:
        logger.warning("PyMuPDF (fitz) not available for region cropping")
        return None

    doc = None
    try:
        doc = fitz.open(str(pdf_path))
        page = doc[page_idx]

        page_rect = page.rect
        clip_rect = fitz.Rect(
            max(table_bbox.x0 - padding, page_rect.x0),
            max(table_bbox.y0 - padding, page_rect.y0),
            min(table_bbox.x1 + padding, page_rect.x1),
            min(table_bbox.y1 + padding, page_rect.y1),
        )

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, clip=clip_rect)

        img_data = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n,
        )

        if pixmap.n == 4:
            img_data = img_data[:, :, :3].copy()

        h, w = img_data.shape[:2]
        channels = img_data.shape[2] if len(img_data.shape) == 3 else 1

        return RawImage(data=img_data, width=w, height=h, channels=channels)
    except Exception:
        logger.warning(
            "Failed to crop region page=%d bbox=%s", page_idx, table_bbox,
            exc_info=True,
        )
        return None
    finally:
        if doc is not None:
            doc.close()
