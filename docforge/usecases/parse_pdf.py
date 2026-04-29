"""Main PDF parsing use case - orchestrates the full pipeline.

Pipeline:
1. Profile document -> determine complexity
2. Learn noise patterns across all pages
3. For each page:
   a. Classify page type
   b. Extract text blocks (digital) or OCR (scanned)
   c. Extract tables
   d. Filter noise
   e. Classify text structure
   f. Merge lines into paragraphs
4. Merge cross-page tables
5. Assemble markdown
6. Calculate quality metrics
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from docforge.adapters.pdfplumber_tables import PdfplumberTableExtractor
from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.enums import BlockType, DocumentComplexity, PageType
from docforge.domain.models import (
    Metadata,
    NoiseStats,
    PageContent,
    ParseResult,
    Table,
    TextBlock,
)
from docforge.domain.value_objects import BBox, DocumentProfile, FontInfo, ImageQualityPolicy
from docforge.infrastructure.config import ParserConfig
from docforge.processing import (
    line_merger,
    markdown_assembler,
    noise_detector,
    ocr_corrector,
    page_classifier,
    quality_metrics,
    table_merger,
    text_structurer,
)
from docforge.processing import column_detector
from docforge.processing import confidence_scorer
from docforge.usecases.profile_document import profile_document
from docforge.usecases.ocr_factory import create_ocr_engine

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def parse_pdf(
    pdf_path: Path,
    config: ParserConfig | None = None,
    force_ocr: bool = False,
    on_progress: "Callable[[str], None] | None" = None,  # noqa: F821
    on_page_done: "Callable[[int, str], None] | None" = None,  # noqa: F821
) -> ParseResult:
    """Parse a PDF file into structured markdown.

    Args:
        pdf_path: Path to the input PDF.
        config: Parser configuration (uses defaults if None).
        force_ocr: Force OCR mode even for digital PDFs.
        on_progress: Optional callback for progress messages.

    Returns:
        ParseResult with pages, markdown, metadata, and statistics.
    """
    if config is None:
        config = ParserConfig()

    def _log(msg: str) -> None:
        print(msg)
        if on_progress:
            on_progress(msg)

    start_time = time.time()
    pdf_path = Path(pdf_path)

    # Initialize adapters
    reader = PyMuPDFReader()
    table_extractor = PdfplumberTableExtractor(config)
    ocr_engine = create_ocr_engine(config.ocr_backend)

    # Initialize preprocessing pipeline (for scanned pages)
    from docforge.adapters.image_converter import pil_to_raw_image

    preprocessing_available = False
    scanned_preprocessor = None
    quality_policy = ImageQualityPolicy()
    try:
        from docforge.adapters.opencv_preprocessor import OpenCVPreprocessor
        from docforge.processing.preprocessing_router import process_scanned_page
        scanned_preprocessor = OpenCVPreprocessor()
        preprocessing_available = True
    except ImportError:
        logger.info("OpenCV not available, preprocessing disabled")

    # Step 1: Profile document
    _log("[1/6] Profiling document...")
    profile = profile_document(pdf_path, reader, config)
    _log(f"       Complexity: {profile.complexity.value}, "
         f"Recommended: {profile.recommended_parser}")

    # OCR is attempted per-page when page is SCANNED/MIXED
    ocr_available = ocr_engine.is_available()
    use_ocr = force_ocr or ocr_available
    ocr_actually_used = False

    # Step 2: Learn noise patterns
    _log("[2/6] Learning noise patterns...")
    doc = reader.open(pdf_path)
    total_pages = reader.get_page_count(doc)

    pages_data: list[dict[str, object]] = []
    for page_idx in range(total_pages):
        blocks = reader.extract_text_blocks(doc, page_idx)
        _, page_height = reader.get_page_dimensions(doc, page_idx)
        lines = [
            (b.text, b.bbox.center_y, b.font.size)
            for b in blocks
        ]
        pages_data.append({"lines": lines, "page_height": page_height})

    patterns = noise_detector.learn_patterns(pages_data, config)
    _log(f"       Headers: {len(patterns.header_patterns)}, "
         f"Footers: {len(patterns.footer_patterns)}")

    # Step 3: Calculate document-level statistics
    _log("[3/6] Calculating document statistics...")
    font_sizes = reader.get_font_sizes(doc)
    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0

    # Calculate average line gap from first few pages
    all_gaps: list[float] = []
    sample_pages = min(10, total_pages)
    for page_idx in range(sample_pages):
        gaps = reader.get_line_gaps(doc, page_idx)
        all_gaps.extend(gaps)
    avg_line_gap = sum(all_gaps) / len(all_gaps) if all_gaps else 5.0

    # Step 4: Process pages
    _log(f"[4/6] Processing {total_pages} pages...")
    plumber_doc = table_extractor.open(pdf_path)

    parsed_pages: list[PageContent] = []
    all_page_tables: list[tuple[list[Table], float, float]] = []
    accumulated_noise = NoiseStats()
    toc_page_count = 0

    for page_idx in range(total_pages):
        _log(f"[page] {page_idx + 1}/{total_pages}")
        width, height = reader.get_page_dimensions(doc, page_idx)
        raw_text = reader.extract_raw_text(doc, page_idx)
        char_count = len(raw_text.strip())

        # Get image info for classification
        images = reader.get_page_images(doc, page_idx)
        has_images = len(images) > 0
        page_area = width * height
        image_area = sum(img["area"] for img in images)
        image_ratio = image_area / max(page_area, 1.0)

        # Classify page
        page_type = page_classifier.classify_page(
            char_count, has_images, image_ratio, raw_text, config
        )

        if page_type == PageType.NOISE:
            toc_page_count += 1
            continue

        # Extract text blocks
        blocks: list[TextBlock] = []

        page_gate_result = None

        if page_type in (PageType.DIGITAL, PageType.MIXED):
            blocks = reader.extract_text_blocks(doc, page_idx)

        if page_type == PageType.SCANNED and use_ocr and ocr_available:
            image = reader.render_page_image(doc, page_idx, config.dpi)
            raw_img = pil_to_raw_image(image)
            if preprocessing_available and scanned_preprocessor is not None:
                ocr_blocks, _decision, page_gate_result = process_scanned_page(
                    raw_img, ocr_engine, scanned_preprocessor, quality_policy,
                )
            else:
                ocr_blocks = ocr_engine.recognize(raw_img)
            ocr_blocks = ocr_corrector.correct_blocks(ocr_blocks, config)
            blocks.extend(ocr_blocks)
            if ocr_blocks:
                ocr_actually_used = True

        if page_type == PageType.MIXED and use_ocr and ocr_available:
            # For mixed pages, also OCR and merge
            image = reader.render_page_image(doc, page_idx, config.dpi)
            raw_img = pil_to_raw_image(image)
            if preprocessing_available and scanned_preprocessor is not None:
                ocr_blocks, _decision, page_gate_result = process_scanned_page(
                    raw_img, ocr_engine, scanned_preprocessor, quality_policy,
                )
            else:
                ocr_blocks = ocr_engine.recognize(raw_img)
            ocr_blocks = ocr_corrector.correct_blocks(ocr_blocks, config)
            # Only add OCR blocks that don't overlap with digital text
            for ob in ocr_blocks:
                if not any(_blocks_overlap(ob, db) for db in blocks):
                    blocks.append(ob)
                    ocr_actually_used = True

        # Filter noise
        clean_blocks, page_noise = noise_detector.filter_noise_from_blocks(
            blocks, height, patterns, config
        )

        accumulated_noise = NoiseStats(
            headers=accumulated_noise.headers + page_noise.headers,
            footers=accumulated_noise.footers + page_noise.footers,
            page_numbers=accumulated_noise.page_numbers + page_noise.page_numbers,
            watermarks=accumulated_noise.watermarks + page_noise.watermarks,
        )

        # Detect multi-column layout and reorder blocks
        col_layout = column_detector.detect_columns(clean_blocks, width)
        if col_layout.num_columns > 1:
            clean_blocks = column_detector.reorder_blocks_by_columns(
                clean_blocks, col_layout,
            )

        # Post-processing order depends on page type:
        # DIGITAL: classify structure first, then merge lines
        # SCANNED/MIXED: merge lines first (OCR output needs grouping), then classify
        if page_type == PageType.DIGITAL:
            classified_blocks: list[TextBlock] = []
            for block in clean_blocks:
                block_type, heading_level = text_structurer.classify_block(
                    block.text, block.font.size, block.font.is_bold, avg_font_size, config
                )
                classified_blocks.append(TextBlock(
                    text=block.text,
                    bbox=block.bbox,
                    font=block.font,
                    block_type=block_type,
                    heading_level=heading_level,
                    confidence=block.confidence,
                ))
            merged_blocks = line_merger.merge_lines(
                classified_blocks, avg_font_size, avg_line_gap, config
            )
        else:
            # SCANNED / MIXED: merge first, then classify
            merged_first = line_merger.merge_lines(
                clean_blocks, avg_font_size, avg_line_gap, config
            )
            merged_blocks = []
            for block in merged_first:
                block_type, heading_level = text_structurer.classify_block(
                    block.text, block.font.size, block.font.is_bold, avg_font_size, config
                )
                merged_blocks.append(TextBlock(
                    text=block.text,
                    bbox=block.bbox,
                    font=block.font,
                    block_type=block_type,
                    heading_level=heading_level,
                    confidence=block.confidence,
                ))

        # Extract tables (pass page dimensions for layout table filtering)
        page_tables = table_extractor.extract_from_page(
            plumber_doc, page_idx, page_width=width, page_height=height,
        )

        # OCR-based table extraction for scanned pages
        if page_type == PageType.SCANNED and not page_tables:
            from docforge.adapters.paddle_table import PaddleTableExtractor
            paddle_tables = PaddleTableExtractor()
            if paddle_tables.is_available():
                image = reader.render_page_image(doc, page_idx, config.dpi)
                page_tables = paddle_tables.extract_from_image(image)

        # Filter leader dots from tables (TOC-like entries)
        filtered_tables: list[Table] = []
        for table in page_tables:
            filtered_cells, new_rows = noise_detector.filter_leader_dots_from_table(
                list(table.cells), table.rows, table.cols,
            )
            if new_rows >= config.min_table_rows:
                from docforge.domain.models import TableCell as TC
                filtered_tables.append(Table(
                    cells=tuple(c for c in filtered_cells if isinstance(c, TC)),
                    rows=new_rows,
                    cols=table.cols,
                    bbox=table.bbox,
                    confidence=table.confidence,
                    needs_review=table.needs_review,
                ))
        page_tables = filtered_tables

        all_page_tables.append((page_tables, height, width))

        # Calculate page confidence score
        page_confidence = confidence_scorer.score_page(
            merged_blocks, page_type, width, height, page_gate_result,
        )

        parsed_pages.append(PageContent(
            page_num=page_idx + 1,
            page_type=page_type,
            blocks=tuple(merged_blocks),
            tables=tuple(page_tables),
            raw_text=raw_text,
            width=width,
            height=height,
            confidence=page_confidence,
        ))

    table_extractor.close(plumber_doc)

    # Step 5: Merge cross-page tables
    _log("[5/6] Merging cross-page tables...")
    if all_page_tables:
        merged_tables_per_page = table_merger.merge_cross_page_tables(
            all_page_tables, config
        )
        # Update parsed pages with merged tables
        updated_pages: list[PageContent] = []
        table_page_idx = 0
        for page in parsed_pages:
            # Find the corresponding table list
            while table_page_idx < len(merged_tables_per_page):
                tables = merged_tables_per_page[table_page_idx]
                table_page_idx += 1
                break
            else:
                tables = list(page.tables)

            updated_pages.append(PageContent(
                page_num=page.page_num,
                page_type=page.page_type,
                blocks=page.blocks,
                tables=tuple(tables),
                raw_text=page.raw_text,
                width=page.width,
                height=page.height,
            ))
        parsed_pages = updated_pages

    # Step 6: Assemble markdown
    _log("[6/6] Assembling markdown...")
    page_markdowns: list[str] = []
    for page in parsed_pages:
        md = markdown_assembler.assemble_page(page, avg_font_size, config)
        if md.strip():
            page_markdowns.append(md)
            if on_page_done:
                try:
                    on_page_done(page.page_num, md)
                except Exception:
                    pass  # 콜백 실패가 파싱을 중단하지 않음

    # Build metadata
    noise_with_toc = NoiseStats(
        headers=accumulated_noise.headers,
        footers=accumulated_noise.footers,
        page_numbers=accumulated_noise.page_numbers,
        toc_pages=toc_page_count,
        toc_entries=accumulated_noise.toc_entries,
        watermarks=accumulated_noise.watermarks,
    )

    # Determine source type
    if use_ocr and profile.complexity == DocumentComplexity.IMAGE_HEAVY:
        source_type = "scanned_pdf"
    elif profile.complexity == DocumentComplexity.MIXED:
        source_type = "mixed_pdf"
    else:
        source_type = "digital_pdf"

    tables_need_review = sum(
        sum(1 for t in p.tables if t.needs_review) for p in parsed_pages
    )

    metadata = Metadata(
        source=pdf_path.name,
        source_type=source_type,
        pages=total_pages,
        parsed_at=datetime.now(KST).isoformat(),
        parser_version="1.0.0",
        ocr_used=ocr_actually_used,
        tables_extracted=sum(len(p.tables) for p in parsed_pages),
        tables_need_review=tables_need_review,
        noise_removed=noise_with_toc,
    )

    markdown = markdown_assembler.finalize_markdown(page_markdowns, metadata)

    # Calculate metrics
    elapsed_ms = (time.time() - start_time) * 1000
    stats = quality_metrics.calculate_metrics(
        parsed_pages, markdown, noise_with_toc, elapsed_ms
    )

    reader.close(doc)

    # Print summary
    warnings = quality_metrics.detect_anomalies(stats)
    _log(f"\nDone! {stats.parsed_pages} pages parsed, "
         f"{stats.tables_found} tables extracted, "
         f"{stats.noise_removed.headers + stats.noise_removed.footers + stats.noise_removed.page_numbers} noise items removed "
         f"({elapsed_ms:.0f}ms)")

    for w in warnings:
        _log(f"  [{w.severity.upper()}] {w.message}")

    return ParseResult(
        pages=tuple(parsed_pages),
        markdown=markdown,
        metadata=metadata,
        stats=stats,
        profile=profile,
    )


def _blocks_overlap(block_a: TextBlock, block_b: TextBlock) -> bool:
    """Check if two text blocks significantly overlap."""
    a = block_a.bbox
    b = block_b.bbox

    overlap_x = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
    overlap_y = max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    overlap_area = overlap_x * overlap_y

    min_area = min(a.area, b.area)
    if min_area <= 0:
        return False

    return overlap_area / min_area > 0.5
