"""Main PDF parsing use case - orchestrates the full pipeline.

Pipeline:
1. Profile document -> determine complexity
2. Learn noise patterns across all pages
3. For each page (parallel when max_workers > 1):
   a. Classify page type
   b. Extract text blocks (digital) or OCR (scanned)
   c. Extract tables / filter noise / classify structure / merge lines
   d. LLM fallback for low-confidence pages (opt-in)
4. Merge cross-page tables
5. Assemble markdown
6. Calculate quality metrics

Per-page work is delegated to :class:`PageProcessor`; parallel execution
is owned by :class:`PipelineCoordinator`. Internal helpers live in
``_parse_pdf_helpers``. This module is the thin top-level orchestrator.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.models import ParseResult
from docforge.domain.value_objects import DocumentStrategyReport
from docforge.infrastructure.config import ParserConfig
from docforge.processing import markdown_assembler, quality_metrics
from docforge.processing.document_intelligence import DocumentIntelligence
from docforge.processing.domain_profiles import get_profile
from docforge.usecases import _parse_pdf_helpers as _h
from docforge.usecases.ocr_factory import create_ocr_engine
from docforge.usecases.page_processor import PageProcessor, PageResult
from docforge.usecases.pipeline_coordinator import PipelineCoordinator
from docforge.usecases.profile_document import profile_document

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# Backward-compatible alias — older imports referenced ``_PageResult``.
_PageResult = PageResult


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
    morpheme_analyzer = _h.build_morpheme_analyzer()
    preprocessing_available = _h.check_preprocessing()

    # === Phase 1: Intelligence ===
    _log("[1/7] Document Intelligence...")
    strategy_report = _run_document_intelligence(pdf_path, _log)

    # === Phase 2: Execution ===
    _log("[2/7] Profiling document...")
    profile = profile_document(pdf_path, reader, config)
    _log(f"       Complexity: {profile.complexity.value}, "
         f"Recommended: {profile.recommended_parser}")

    use_ocr = force_ocr or ocr_engine.is_available()

    _log("[3/7] Learning noise patterns...")
    doc = reader.open(pdf_path)
    total_pages = reader.get_page_count(doc)
    patterns = _h.learn_noise(reader, doc, total_pages, config)
    _log(f"       Headers: {len(patterns.header_patterns)}, "
         f"Footers: {len(patterns.footer_patterns)}")

    _log("[4/7] Calculating document statistics...")
    avg_font_size, avg_line_gap = _h.doc_stats(reader, doc, total_pages)
    reader.close(doc)

    llm_engine = _h.build_llm_engine(config)
    layout_detector = _h.build_layout_detector(config)

    _log(f"[5/7] Processing {total_pages} pages...")
    page_processor = PageProcessor(
        config=config,
        llm_engine=llm_engine,
        morpheme_analyzer=morpheme_analyzer,
        preprocessing_available=preprocessing_available,
        domain_profile=get_profile(config.domain_profile),
        avg_font_size=avg_font_size,
        avg_line_gap=avg_line_gap,
        patterns=patterns,
        use_ocr=use_ocr,
        layout_detector=layout_detector,
        force_ocr=force_ocr,
    )
    coordinator = PipelineCoordinator(
        page_processor=page_processor,
        max_workers=config.max_workers,
    )

    ocr_semaphore = threading.Semaphore(config.max_ocr_workers)

    # Per-page early markdown emit — lets the SSE/dashboard render each
    # page as soon as it finishes parsing instead of waiting for [7/7].
    def _emit_page_markdown(result: PageResult) -> None:
        if on_page_done is None:
            return
        page_content = result.page_content
        if page_content is None:
            return
        try:
            md = markdown_assembler.assemble_page(page_content, avg_font_size, config)
        except Exception:
            logger.warning("Per-page markdown assembly failed", exc_info=True)
            return
        if not md.strip():
            return
        try:
            on_page_done(page_content.page_num, md)
        except Exception:
            logger.warning("on_page_done callback failed", exc_info=True)

    ordered_results, page_errors = coordinator.run(
        pdf_path, total_pages, ocr_semaphore, _log,
        on_page_complete=_emit_page_markdown,
        strategy_report=strategy_report,
    )

    (
        parsed_pages,
        all_page_tables,
        noise_with_toc,
        ocr_actually_used,
        llm_fallback_records,
        all_region_vlm_records,
        retry_stats,
    ) = _h.aggregate_results(ordered_results)

    _log("[6/7] Merging cross-page tables...")
    parsed_pages = _h.merge_cross_page_tables(parsed_pages, all_page_tables, config)
    parsed_pages = _h.promote_numbered_headings(parsed_pages)

    # --- AST construction (opt-in via config.ast_enabled) ---
    document_ast = None
    if config.ast_enabled:
        try:
            from docforge.processing.ast_builder import build as build_ast

            document_ast = build_ast(tuple(parsed_pages), source_file=str(pdf_path))
            _log(f"       AST built: {len(document_ast.root.children)} top-level nodes")
        except Exception:
            logger.warning("AST build failed, continuing without AST", exc_info=True)

    _log("[7/7] Assembling markdown...")
    # on_page_done already fired per-page in the coordinator loop above —
    # don't double-emit here, just collect markdowns for finalization.
    page_markdowns = _h.assemble_page_markdowns(
        parsed_pages, avg_font_size, config, on_page_done=None,
    )
    _h.log_records(llm_fallback_records, all_region_vlm_records)

    metadata = _h.build_metadata(
        pdf_path=pdf_path, total_pages=total_pages,
        parsed_pages=parsed_pages, profile=profile,
        use_ocr=use_ocr, ocr_actually_used=ocr_actually_used,
        noise_with_toc=noise_with_toc,
    )

    markdown = markdown_assembler.finalize_markdown(page_markdowns, metadata)
    elapsed_ms = (time.time() - start_time) * 1000
    stats = quality_metrics.calculate_metrics(
        parsed_pages, markdown, noise_with_toc, elapsed_ms,
        retry_stats=retry_stats,
    )

    warnings = quality_metrics.detect_anomalies(stats)
    noise = stats.noise_removed
    _log(
        f"\nDone! {stats.parsed_pages} pages parsed, "
        f"{stats.tables_found} tables extracted, "
        f"{noise.headers + noise.footers + noise.page_numbers} noise items removed "
        f"({elapsed_ms:.0f}ms)"
    )
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
        page_errors=tuple(page_errors),
        ast=document_ast,
    )


# ---------------------------------------------------------------------------
# Phase 3: Document Intelligence helpers
# ---------------------------------------------------------------------------


def _run_document_intelligence(
    pdf_path: Path,
    log_fn: "Callable[[str], None]",
) -> DocumentStrategyReport | None:
    """Run pre-parse document intelligence scan.

    Returns None on failure (graceful degradation -- pipeline continues
    without per-page strategies).
    """
    try:
        import fitz  # type: ignore[import-untyped]

        intelligence = DocumentIntelligence()
        with fitz.open(str(pdf_path)) as doc:
            strategy_report = intelligence.analyze(doc)
        _log_strategy_report(strategy_report, log_fn)
        return strategy_report
    except Exception:
        logger.warning(
            "Document Intelligence scan failed, continuing without strategies",
            exc_info=True,
        )
        return None


def _log_strategy_report(
    report: DocumentStrategyReport,
    log_fn: "Callable[[str], None]",
) -> None:
    """Print strategy report summary to console/SSE."""
    log_fn(f"       Pages analyzed: {report.total_pages}")
    for method, count in sorted(report.strategy_counts.items()):
        log_fn(f"       {method}: {count} pages")
    if report.surya_page_count > 0:
        log_fn(f"       Surya needed: {report.surya_page_count} pages")
