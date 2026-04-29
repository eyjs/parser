"""Main PDF parsing use case - orchestrates the full pipeline.

Pipeline:
1. Profile document -> determine complexity
2. Learn noise patterns across all pages
3. For each page (parallel when max_workers > 1):
   a. Classify page type
   b. Extract text blocks (digital) or OCR (scanned)
   c. Extract tables
   d. Filter noise
   e. Classify text structure
   f. Merge lines into paragraphs
   g. LLM fallback for low-confidence pages (opt-in)
4. Merge cross-page tables
5. Assemble markdown
6. Calculate quality metrics
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.adapters.pdfplumber_tables import PdfplumberTableExtractor
from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.enums import BlockType, DocumentComplexity, PageType
from docforge.domain.models import (
    LLMFallbackRecord,
    Metadata,
    NoiseStats,
    PageContent,
    ParseResult,
    RegionVLMRecord,
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
from docforge.processing.llm_fallback_router import run_llm_fallback, should_invoke_llm
from docforge.processing.noise_detector import LearnedPatterns
from docforge.usecases.profile_document import profile_document
from docforge.usecases.ocr_factory import create_ocr_engine

if TYPE_CHECKING:
    from collections.abc import Callable
    from docforge.domain.ports import VisionLLMEngine

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class _PageResult:
    """Internal result from processing a single page."""
    page_content: PageContent | None
    tables_info: tuple[list[Table], float, float] | None
    noise: NoiseStats
    is_toc: bool
    ocr_used: bool
    llm_record: LLMFallbackRecord | None = None
    region_vlm_records: tuple[RegionVLMRecord, ...] = ()


def parse_pdf(
    pdf_path: Path,
    config: ParserConfig | None = None,
    force_ocr: bool = False,
    on_progress: Callable[[str], None] | None = None,
    on_page_done: Callable[[int, str], None] | None = None,
) -> ParseResult:
    if config is None:
        config = ParserConfig()

    progress_lock = threading.Lock()

    def _log(msg: str) -> None:
        with progress_lock:
            print(msg)
            if on_progress:
                on_progress(msg)

    start_time = time.time()
    pdf_path = Path(pdf_path)

    reader = PyMuPDFReader()
    ocr_engine = create_ocr_engine(config.ocr_backend)

    # Check preprocessing availability once (avoid per-worker ImportError try-catch)
    preprocessing_available = False
    try:
        from docforge.adapters.opencv_preprocessor import OpenCVPreprocessor  # noqa: F401
        from docforge.processing.preprocessing_router import process_scanned_page  # noqa: F401
        preprocessing_available = True
    except ImportError:
        logger.info("OpenCV not available, preprocessing disabled")

    # Step 1: Profile document
    _log("[1/6] Profiling document...")
    profile = profile_document(pdf_path, reader, config)
    _log(f"       Complexity: {profile.complexity.value}, "
         f"Recommended: {profile.recommended_parser}")

    ocr_available = ocr_engine.is_available()
    use_ocr = force_ocr or ocr_available

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

    all_gaps: list[float] = []
    sample_pages = min(10, total_pages)
    for page_idx in range(sample_pages):
        gaps = reader.get_line_gaps(doc, page_idx)
        all_gaps.extend(gaps)
    avg_line_gap = sum(all_gaps) / len(all_gaps) if all_gaps else 5.0

    reader.close(doc)

    # LLM fallback engine (opt-in, graceful skip if unavailable)
    # Also used for region-level VLM table routing when region_vlm_enabled
    llm_engine: VisionLLMEngine | None = None
    if config.llm_fallback_enabled or config.region_vlm_enabled:
        try:
            from docforge.adapters.vision_llm_engine import Qwen2VLMLXEngine
            _candidate = Qwen2VLMLXEngine()
            if _candidate.is_available():
                llm_engine = _candidate
            else:
                logger.info("LLM fallback disabled — mlx_vlm not installed")
        except Exception:
            logger.warning("LLM engine init failed, LLM fallback disabled", exc_info=True)

    # Step 4: Process pages (parallel when max_workers > 1)
    _log(f"[4/6] Processing {total_pages} pages...")

    ocr_semaphore = threading.Semaphore(config.max_ocr_workers)

    def _submit_page(page_idx: int) -> _PageResult:
        return _process_single_page(
            page_idx=page_idx,
            pdf_path=pdf_path,
            config=config,
            patterns=patterns,
            avg_font_size=avg_font_size,
            avg_line_gap=avg_line_gap,
            use_ocr=use_ocr,
            preprocessing_available=preprocessing_available,
            ocr_semaphore=ocr_semaphore,
            llm_engine=llm_engine,
            log_fn=_log,
            total_pages=total_pages,
        )

    raw_results: list[tuple[int, _PageResult]] = []

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = {
            executor.submit(_submit_page, page_idx): page_idx
            for page_idx in range(total_pages)
        }
        for future in as_completed(futures):
            page_idx = futures[future]
            try:
                result = future.result()
            except Exception:
                logger.warning("Page %d processing failed, skipping", page_idx + 1, exc_info=True)
                result = _PageResult(
                    page_content=None, tables_info=None,
                    noise=NoiseStats(), is_toc=False, ocr_used=False,
                )
            raw_results.append((page_idx, result))

    raw_results.sort(key=lambda x: x[0])

    parsed_pages: list[PageContent] = []
    all_page_tables: list[tuple[list[Table], float, float]] = []
    accumulated_noise = NoiseStats()
    toc_page_count = 0
    ocr_actually_used = False
    llm_fallback_records: list[LLMFallbackRecord] = []
    all_region_vlm_records: list[RegionVLMRecord] = []

    for _idx, pr in raw_results:
        if pr.is_toc:
            toc_page_count += 1
            continue
        if pr.page_content is not None:
            parsed_pages.append(pr.page_content)
        if pr.tables_info is not None:
            all_page_tables.append(pr.tables_info)
        accumulated_noise = NoiseStats(
            headers=accumulated_noise.headers + pr.noise.headers,
            footers=accumulated_noise.footers + pr.noise.footers,
            page_numbers=accumulated_noise.page_numbers + pr.noise.page_numbers,
            watermarks=accumulated_noise.watermarks + pr.noise.watermarks,
        )
        if pr.ocr_used:
            ocr_actually_used = True
        if pr.llm_record is not None:
            llm_fallback_records.append(pr.llm_record)
        all_region_vlm_records.extend(pr.region_vlm_records)

    # Step 5: Merge cross-page tables
    _log("[5/6] Merging cross-page tables...")
    if all_page_tables:
        merged_tables_per_page = table_merger.merge_cross_page_tables(
            all_page_tables, config
        )
        updated_pages: list[PageContent] = []
        table_page_idx = 0
        for page in parsed_pages:
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
                    pass

    for record in llm_fallback_records:
        logger.info(
            "LLM fallback page=%d adopted=%s reason=%s",
            record.page_num, record.adopted, record.reason,
        )

    for record in all_region_vlm_records:
        logger.info(
            "Region VLM page=%d replaced=%s score=%.3f reason=%s",
            record.page_num, record.replaced, record.quality_score, record.reason,
        )

    noise_with_toc = NoiseStats(
        headers=accumulated_noise.headers,
        footers=accumulated_noise.footers,
        page_numbers=accumulated_noise.page_numbers,
        toc_pages=toc_page_count,
        toc_entries=accumulated_noise.toc_entries,
        watermarks=accumulated_noise.watermarks,
    )

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

    elapsed_ms = (time.time() - start_time) * 1000
    stats = quality_metrics.calculate_metrics(
        parsed_pages, markdown, noise_with_toc, elapsed_ms
    )

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
        llm_fallback_records=tuple(llm_fallback_records),
        region_vlm_records=tuple(all_region_vlm_records),
    )


def _process_single_page(
    page_idx: int,
    pdf_path: Path,
    config: ParserConfig,
    patterns: LearnedPatterns,
    avg_font_size: float,
    avg_line_gap: float,
    use_ocr: bool,
    preprocessing_available: bool,
    ocr_semaphore: threading.Semaphore,
    llm_engine: VisionLLMEngine | None,
    log_fn: Callable[[str], None],
    total_pages: int,
) -> _PageResult:
    """Process a single page. Opens its own doc/plumber handles for thread safety."""
    from docforge.adapters.image_converter import pil_to_raw_image

    reader = PyMuPDFReader()
    doc = reader.open(pdf_path)
    table_extractor = PdfplumberTableExtractor(config)
    plumber_doc = table_extractor.open(pdf_path)

    # OCR engine created inside semaphore to avoid multiple heavy instances
    ocr_engine = None
    ocr_available = False

    scanned_preprocessor = None
    quality_policy = ImageQualityPolicy()
    if preprocessing_available:
        from docforge.adapters.opencv_preprocessor import OpenCVPreprocessor
        from docforge.processing.preprocessing_router import process_scanned_page
        scanned_preprocessor = OpenCVPreprocessor()

    try:
        log_fn(f"[page] {page_idx + 1}/{total_pages}")
        width, height = reader.get_page_dimensions(doc, page_idx)
        raw_text = reader.extract_raw_text(doc, page_idx)
        char_count = len(raw_text.strip())

        images = reader.get_page_images(doc, page_idx)
        has_images = len(images) > 0
        page_area = width * height
        image_area = sum(img["area"] for img in images)
        image_ratio = image_area / max(page_area, 1.0)

        page_type = page_classifier.classify_page(
            char_count, has_images, image_ratio, raw_text, config
        )

        if page_type == PageType.NOISE:
            return _PageResult(
                page_content=None, tables_info=None,
                noise=NoiseStats(), is_toc=True, ocr_used=False,
            )

        blocks: list[TextBlock] = []
        page_gate_result = None
        page_ocr_used = False

        if page_type in (PageType.DIGITAL, PageType.MIXED):
            blocks = reader.extract_text_blocks(doc, page_idx)

        if page_type == PageType.SCANNED and use_ocr:
            with ocr_semaphore:
                if ocr_engine is None:
                    ocr_engine = create_ocr_engine(config.ocr_backend)
                    ocr_available = ocr_engine.is_available()
                if ocr_available:
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
                        page_ocr_used = True

        if page_type == PageType.MIXED and use_ocr:
            with ocr_semaphore:
                if ocr_engine is None:
                    ocr_engine = create_ocr_engine(config.ocr_backend)
                    ocr_available = ocr_engine.is_available()
                if ocr_available:
                    image = reader.render_page_image(doc, page_idx, config.dpi)
                    raw_img = pil_to_raw_image(image)
                    if preprocessing_available and scanned_preprocessor is not None:
                        ocr_blocks, _decision, page_gate_result = process_scanned_page(
                            raw_img, ocr_engine, scanned_preprocessor, quality_policy,
                        )
                    else:
                        ocr_blocks = ocr_engine.recognize(raw_img)
                    ocr_blocks = ocr_corrector.correct_blocks(ocr_blocks, config)
                    for ob in ocr_blocks:
                        if not any(_blocks_overlap(ob, db) for db in blocks):
                            blocks.append(ob)
                            page_ocr_used = True

        # Filter noise
        clean_blocks, page_noise = noise_detector.filter_noise_from_blocks(
            blocks, height, patterns, config
        )

        # Detect multi-column layout
        col_layout = column_detector.detect_columns(clean_blocks, width)
        if col_layout.num_columns > 1:
            clean_blocks = column_detector.reorder_blocks_by_columns(
                clean_blocks, col_layout,
            )

        # Post-processing order
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

        # Extract tables
        page_tables = table_extractor.extract_from_page(
            plumber_doc, page_idx, page_width=width, page_height=height,
        )

        if page_type == PageType.SCANNED and not page_tables:
            from docforge.adapters.paddle_table import PaddleTableExtractor
            paddle_tables = PaddleTableExtractor()
            if paddle_tables.is_available():
                image = reader.render_page_image(doc, page_idx, config.dpi)
                page_tables = paddle_tables.extract_from_image(image)

        # Region VLM: PaddleOCR TSR fallback for low-quality digital tables
        if (
            config.region_vlm_paddle_fallback
            and page_type in (PageType.DIGITAL, PageType.MIXED)
            and page_tables
        ):
            from docforge.processing.table_quality_scorer import score_table

            has_low_quality = any(
                score_table(tbl) < config.table_quality_threshold
                for tbl in page_tables
            )
            if has_low_quality:
                paddle_ext = PaddleTableExtractor()
                if paddle_ext.is_available():
                    img = reader.render_page_image(doc, page_idx, config.dpi)
                    paddle_results = paddle_ext.extract_from_image(img)

                    improved_tables: list[Table] = []
                    for tbl in page_tables:
                        tbl_score = score_table(tbl)
                        if tbl_score < config.table_quality_threshold:
                            best_paddle = _find_best_overlap(tbl, paddle_results)
                            if best_paddle is not None and score_table(best_paddle) > tbl_score:
                                improved_tables.append(best_paddle)
                                continue
                        improved_tables.append(tbl)
                    page_tables = improved_tables

        # Filter leader dots
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

        # Region VLM routing for low-quality tables
        region_vlm_records: list[RegionVLMRecord] = []
        if config.region_vlm_enabled and llm_engine is not None and page_tables:
            from docforge.adapters.region_crop import crop_table_region
            from docforge.processing.region_vlm_router import route_table_to_vlm
            from docforge.processing.table_quality_scorer import score_table

            vlm_improved_tables: list[Table] = []
            for tbl in page_tables:
                tbl_score = score_table(tbl)
                if tbl_score < config.table_quality_threshold:
                    cropped = crop_table_region(pdf_path, page_idx, tbl.bbox, config.dpi)
                    if cropped is not None:
                        vlm_table, vlm_record = route_table_to_vlm(
                            cropped_image=cropped,
                            original_bbox=tbl.bbox,
                            quality_score=tbl_score,
                            page_num=page_idx + 1,
                            llm_engine=llm_engine,
                            domain_hint=config.llm_domain_hint,
                        )
                        region_vlm_records.append(vlm_record)
                        if vlm_table is not None:
                            vlm_improved_tables.append(vlm_table)
                            continue
                vlm_improved_tables.append(tbl)

            page_tables = vlm_improved_tables

        # Calculate page confidence
        page_confidence = confidence_scorer.score_page(
            merged_blocks, page_type, width, height, page_gate_result,
        )

        page_content = PageContent(
            page_num=page_idx + 1,
            page_type=page_type,
            blocks=tuple(merged_blocks),
            tables=tuple(page_tables),
            raw_text=raw_text,
            width=width,
            height=height,
            confidence=page_confidence,
        )

        # LLM Fallback for low-confidence pages
        llm_record = None
        if llm_engine is not None and should_invoke_llm(page_content, config):
            page_image_raw = pil_to_raw_image(
                reader.render_page_image(doc, page_idx, config.dpi)
            )
            final_blocks, llm_record = run_llm_fallback(
                page_content, page_image_raw, llm_engine, config,
            )
            if llm_record.adopted:
                page_content = PageContent(
                    page_num=page_idx + 1,
                    page_type=page_type,
                    blocks=tuple(final_blocks),
                    tables=tuple(page_tables),
                    raw_text=raw_text,
                    width=width,
                    height=height,
                    confidence=page_confidence,
                )

        return _PageResult(
            page_content=page_content,
            tables_info=(page_tables, height, width),
            noise=page_noise,
            is_toc=False,
            ocr_used=page_ocr_used,
            llm_record=llm_record,
            region_vlm_records=tuple(region_vlm_records),
        )
    finally:
        reader.close(doc)
        table_extractor.close(plumber_doc)


def _find_best_overlap(target: Table, candidates: list[Table]) -> Table | None:
    """Find the candidate table with the best BBox overlap to the target."""
    best: Table | None = None
    best_iou = 0.0

    for candidate in candidates:
        iou = _bbox_iou(target.bbox, candidate.bbox)
        if iou > best_iou:
            best_iou = iou
            best = candidate

    # Require at least 20% IoU to consider it a match
    return best if best_iou > 0.2 else None


def _bbox_iou(a: BBox, b: BBox) -> float:
    """Compute Intersection over Union for two BBoxes."""
    inter_x0 = max(a.x0, b.x0)
    inter_y0 = max(a.y0, b.y0)
    inter_x1 = min(a.x1, b.x1)
    inter_y1 = min(a.y1, b.y1)

    inter_area = max(0, inter_x1 - inter_x0) * max(0, inter_y1 - inter_y0)
    union_area = a.area + b.area - inter_area

    return inter_area / union_area if union_area > 0 else 0.0


def _blocks_overlap(block_a: TextBlock, block_b: TextBlock) -> bool:
    a = block_a.bbox
    b = block_b.bbox

    overlap_x = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
    overlap_y = max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    overlap_area = overlap_x * overlap_y

    min_area = min(a.area, b.area)
    if min_area <= 0:
        return False

    return overlap_area / min_area > 0.5
