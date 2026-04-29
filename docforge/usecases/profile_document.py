"""Document profiling use case.

Scans the entire document quickly to determine its complexity level
and recommend the optimal parsing pipeline.
"""

from __future__ import annotations

from pathlib import Path

from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.enums import DocumentComplexity
from docforge.domain.value_objects import DocumentProfile
from docforge.infrastructure.config import ParserConfig


def profile_document(
    pdf_path: Path,
    reader: PyMuPDFReader,
    config: ParserConfig,
) -> DocumentProfile:
    """Profile a PDF document to determine parsing strategy.

    Performs a lightweight scan (~1ms/page) using only text extraction
    (no rendering). Classifies the document complexity and recommends
    the appropriate parser combination.

    Args:
        pdf_path: Path to the PDF file.
        reader: PyMuPDF reader instance.
        config: Parser configuration.

    Returns:
        DocumentProfile with complexity classification and recommendation.
    """
    doc = reader.open(pdf_path)
    total_pages = reader.get_page_count(doc)

    text_pages = 0
    image_only_pages = 0
    total_chars = 0
    total_image_area = 0.0
    total_page_area = 0.0
    has_tables = False

    for page_idx in range(total_pages):
        raw_text = reader.extract_raw_text(doc, page_idx)
        char_count = len(raw_text.strip())
        total_chars += char_count

        width, height = reader.get_page_dimensions(doc, page_idx)
        page_area = width * height
        total_page_area += page_area

        # Image area calculation
        images = reader.get_page_images(doc, page_idx)
        page_image_area = sum(img["area"] for img in images)
        total_image_area += page_image_area

        # Classify page
        if char_count >= config.min_chars_per_page:
            text_pages += 1
        else:
            image_only_pages += 1

        # Table hint detection
        if not has_tables:
            for keyword in config.table_line_keywords:
                if keyword in raw_text:
                    has_tables = True
                    break

    reader.close(doc)

    avg_chars = total_chars / max(total_pages, 1)
    image_area_ratio = total_image_area / max(total_page_area, 1.0)
    image_only_ratio = image_only_pages / max(total_pages, 1)

    # Routing decision tree
    if image_only_ratio >= config.image_heavy_ratio:
        complexity = DocumentComplexity.IMAGE_HEAVY
        recommended = "paddle_ocr"
    elif image_only_pages > 0 and image_area_ratio > config.image_area_table_hint:
        complexity = DocumentComplexity.MIXED
        recommended = "mixed"
    elif has_tables or image_area_ratio > config.image_area_table_hint:
        complexity = DocumentComplexity.TEXT_WITH_TABLES
        recommended = "pymupdf_pdfplumber"
    else:
        complexity = DocumentComplexity.TEXT_ONLY
        recommended = "pymupdf"

    return DocumentProfile(
        total_pages=total_pages,
        text_pages=text_pages,
        image_only_pages=image_only_pages,
        total_chars=total_chars,
        has_tables=has_tables,
        avg_chars_per_page=round(avg_chars, 1),
        image_area_ratio=round(image_area_ratio, 4),
        complexity=complexity,
        recommended_parser=recommended,
    )
