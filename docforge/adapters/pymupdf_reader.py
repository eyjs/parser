"""PyMuPDF-based PDF reader adapter.

Implements the PDFReader port using fitz (PyMuPDF) for:
- Text extraction with font info and bounding boxes
- Page image rendering for OCR and verification
- Image metadata extraction for page classification
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo

logger = logging.getLogger(__name__)


class PyMuPDFReader:
    """PDF reader using PyMuPDF (fitz)."""

    def open(self, path: Path) -> fitz.Document:
        """Open a PDF file."""
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        return fitz.open(str(path))

    def get_page_count(self, doc: fitz.Document) -> int:
        """Return total page count."""
        return len(doc)

    def extract_text_blocks(self, doc: fitz.Document, page_idx: int) -> list[TextBlock]:
        """Extract text blocks with font info and bounding boxes.

        Each 'line' within a PyMuPDF block becomes a TextBlock, preserving
        reading order and font metadata.
        """
        page = doc[page_idx]
        blocks_data = page.get_text("dict")["blocks"]
        result: list[TextBlock] = []

        for block in blocks_data:
            if block["type"] != 0:  # text blocks only
                continue

            for line in block["lines"]:
                spans = line["spans"]
                if not spans:
                    continue

                line_text = "".join(span["text"] for span in spans)
                if not line_text.strip():
                    continue

                # Use the primary span (most text) for font info
                primary_span = max(spans, key=lambda s: len(s["text"]))
                font_name = primary_span.get("font", "")
                font_size = primary_span.get("size", 0.0)
                is_bold = "bold" in font_name.lower() or "Bold" in font_name

                bbox = BBox(
                    x0=line["bbox"][0],
                    y0=line["bbox"][1],
                    x1=line["bbox"][2],
                    y1=line["bbox"][3],
                )

                result.append(TextBlock(
                    text=line_text,
                    bbox=bbox,
                    font=FontInfo(name=font_name, size=font_size, is_bold=is_bold),
                    block_type=BlockType.TEXT,
                ))

        return result

    def extract_raw_text(self, doc: fitz.Document, page_idx: int) -> str:
        """Extract plain text from a page."""
        return doc[page_idx].get_text("text")

    def render_page_image(self, doc: fitz.Document, page_idx: int, dpi: int = 200) -> Any:
        """Render a page as a PIL Image.

        Returns a PIL.Image.Image object.
        """
        from PIL import Image
        import io

        page = doc[page_idx]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        return Image.open(io.BytesIO(img_bytes))

    def render_page_to_base64(self, doc: fitz.Document, page_idx: int, dpi: int = 150) -> str:
        """Render a page as a base64-encoded PNG string."""
        import base64

        page = doc[page_idx]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        return base64.b64encode(img_bytes).decode("utf-8")

    def get_page_dimensions(self, doc: fitz.Document, page_idx: int) -> tuple[float, float]:
        """Return (width, height) of a page."""
        page = doc[page_idx]
        return page.rect.width, page.rect.height

    def get_page_images(self, doc: fitz.Document, page_idx: int) -> list[dict[str, Any]]:
        """Return list of image info dicts.

        Each dict has: bbox (BBox), area (float), width, height.
        """
        page = doc[page_idx]
        images: list[dict[str, Any]] = []

        for img in page.get_image_info():
            bbox = BBox(
                x0=img["bbox"][0],
                y0=img["bbox"][1],
                x1=img["bbox"][2],
                y1=img["bbox"][3],
            )
            images.append({
                "bbox": bbox,
                "area": bbox.area,
                "width": bbox.width,
                "height": bbox.height,
            })

        return images

    def get_font_sizes(self, doc: fitz.Document) -> list[float]:
        """Collect all font sizes from the entire document for averaging."""
        sizes: list[float] = []
        for page in doc:
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["text"].strip():
                            sizes.append(span["size"])
        return sizes

    def get_line_gaps(self, doc: fitz.Document, page_idx: int) -> list[float]:
        """Calculate vertical gaps between consecutive lines on a page."""
        page = doc[page_idx]
        blocks = page.get_text("dict")["blocks"]
        y_positions: list[tuple[float, float]] = []  # (y0, y1) pairs

        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                y_positions.append((line["bbox"][1], line["bbox"][3]))

        y_positions.sort(key=lambda p: p[0])
        gaps: list[float] = []
        for i in range(1, len(y_positions)):
            gap = y_positions[i][0] - y_positions[i - 1][1]
            if gap > 0:
                gaps.append(gap)

        return gaps

    def close(self, doc: fitz.Document) -> None:
        """Close the document."""
        doc.close()
