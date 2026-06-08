"""Single-page processing pipeline (public interface).

Extracts the per-page logic from ``parse_pdf`` into a focused class that
owns one page's lifecycle: open thread-local doc handles, classify, OCR,
extract tables, run noise/structure passes, and optionally route through
LLM/VLM fallbacks.

The processor is stateless across pages — each ``process()`` call opens
its own document handles for thread safety.

Step22 G25 god-file split: this module keeps the **public interface**
(``PageResult``, ``PageProcessor.__init__``, ``PageProcessor.process()``) and
its import path unchanged. The 13 ``_*`` helper methods were moved verbatim to
:mod:`docforge.usecases._page_processor_helpers` as the ``_PageProcessorHelpers``
mixin; ``PageProcessor`` inherits them so ``self._run_ocr(...)``,
``self._maybe_route_to_vlm(...)`` etc. resolve via MRO with identical behavior.
The ``llm_engine.describe_image()`` / ``is_available()`` call paths (G23) are
unchanged.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.adapters.pdfplumber_tables import PdfplumberTableExtractor
from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.enums import PageType
from docforge.domain.models import (
    LLMFallbackRecord,
    NoiseStats,
    PageContent,
    RegionVLMRecord,
    Table,
    TextBlock,
)
from docforge.domain.value_objects import ImageQualityPolicy, PageStrategy
from docforge.infrastructure.config import ParserConfig
from docforge.processing import (
    column_detector,
    confidence_scorer,
    noise_detector,
    page_classifier,
    text_structurer,
)
from docforge.processing.block_splitter import split_heading_body
from docforge.processing.heading_hierarchy import assign_hierarchy
from docforge.processing.llm_fallback_router import run_llm_fallback, should_invoke_llm
from docforge.processing.noise_detector import LearnedPatterns
from docforge.usecases._page_processor_helpers import (
    _PageProcessorHelpers,
    _blocks_overlap,
)
from docforge.usecases.ocr_factory import create_ocr_engine

import logging as _logging

_page_logger = _logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from docforge.domain.ports import (
        DomainProfile,
        LayoutDetector,
        MorphemeAnalyzer,
        VisionLLMEngine,
    )


@dataclass(frozen=True)
class PageResult:
    """Internal result from processing a single page."""

    page_content: PageContent | None
    tables_info: tuple[list[Table], float, float] | None
    noise: NoiseStats
    is_toc: bool
    ocr_used: bool
    llm_record: LLMFallbackRecord | None = None
    region_vlm_records: tuple[RegionVLMRecord, ...] = ()
    # Phase 3: block-level retry statistics
    blocks_retried: int = 0
    blocks_fallback_ocr: int = 0
    blocks_fallback_vlm: int = 0
    avg_block_quality: float = 1.0


class PageProcessor(_PageProcessorHelpers):
    """Processes a single PDF page end-to-end.

    Heavy dependencies (config, engines, learned patterns) are injected
    once; ``process()`` opens its own per-call document handles so it is
    safe to invoke from a thread pool.

    Per-page helper methods live on the ``_PageProcessorHelpers`` mixin
    (Step22 G25 split); they are inherited unchanged.
    """

    def __init__(
        self,
        config: ParserConfig,
        llm_engine: "VisionLLMEngine | None",
        morpheme_analyzer: "MorphemeAnalyzer | None",
        preprocessing_available: bool,
        domain_profile: "DomainProfile",
        avg_font_size: float,
        avg_line_gap: float,
        patterns: LearnedPatterns,
        use_ocr: bool,
        layout_detector: "LayoutDetector | None" = None,
        force_ocr: bool = False,
    ) -> None:
        self._config = config
        self._llm_engine = llm_engine
        self._morpheme_analyzer = morpheme_analyzer
        self._preprocessing_available = preprocessing_available
        self._domain_profile = domain_profile
        self._avg_font_size = avg_font_size
        self._avg_line_gap = avg_line_gap
        self._patterns = patterns
        self._use_ocr = use_ocr
        self._layout_detector = layout_detector
        self._force_ocr = force_ocr

    def process(
        self,
        page_idx: int,
        pdf_path: Path,
        ocr_semaphore: threading.Semaphore,
        log_fn: "Callable[[str], None]",
        total_pages: int,
        page_strategy: PageStrategy | None = None,
        override_hints: dict[str, object] | None = None,
    ) -> PageResult:
        """Process a single page. Opens its own doc/plumber handles.

        When ``override_hints`` is provided (from the A4 reprocessing
        loop), the hints override instance-level settings for this call
        only.  Supported keys: ``"force_ocr"`` (bool),
        ``"layout_detection_all_pages"`` (bool).
        """
        from docforge.adapters.image_converter import pil_to_raw_image

        config = self._config
        reader = PyMuPDFReader()
        doc = reader.open(pdf_path)
        table_extractor = PdfplumberTableExtractor(config)
        plumber_doc = table_extractor.open(pdf_path)

        ocr_engine = None
        ocr_available = False

        scanned_preprocessor = None
        quality_policy = ImageQualityPolicy()
        if self._preprocessing_available:
            from docforge.adapters.opencv_preprocessor import OpenCVPreprocessor

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

            # Need blocks for COVER/TOC heuristics — extract eagerly only
            # when the page has enough text to be a candidate.
            preliminary_blocks: list[TextBlock] = []
            if char_count >= 5:
                try:
                    preliminary_blocks = reader.extract_text_blocks(doc, page_idx)
                except Exception:
                    preliminary_blocks = []

            page_type = page_classifier.classify_page_with_blocks(
                page_idx=page_idx,
                char_count=char_count,
                has_images=has_images,
                image_area_ratio=image_ratio,
                raw_text=raw_text,
                blocks=preliminary_blocks,
                page_width=width,
                page_height=height,
                config=config,
            )

            if page_type == PageType.NOISE:
                return PageResult(
                    page_content=None, tables_info=None,
                    noise=NoiseStats(), is_toc=True, ocr_used=False,
                )

            # COVER/TOC pages bypass the heavy classification pipeline
            # (heading hierarchy, line merging, structure classification)
            # but still need layout analysis for correct reading order.
            if page_type in (PageType.COVER, PageType.TOC):
                cover_blocks, cover_noise = noise_detector.filter_noise_from_blocks(
                    preliminary_blocks, height, self._patterns, config,
                )
                col_layout = column_detector.detect_columns(cover_blocks, width)
                if col_layout.num_columns > 1:
                    cover_blocks = column_detector.reorder_blocks_by_columns(
                        cover_blocks, col_layout,
                    )
                page_content = PageContent(
                    page_num=page_idx + 1,
                    page_type=page_type,
                    blocks=tuple(cover_blocks),
                    tables=(),
                    raw_text=raw_text,
                    width=width,
                    height=height,
                    confidence=None,
                )
                return PageResult(
                    page_content=page_content,
                    tables_info=None,
                    noise=cover_noise,
                    is_toc=False,
                    ocr_used=False,
                )

            blocks: list[TextBlock] = []
            page_gate_result = None
            page_ocr_used = False

            # A4: resolve override hints as local variables — no instance
            # mutation.  The reprocessing loop passes escalated hints here.
            _hints = override_hints or {}
            effective_force_ocr = self._force_ocr or bool(
                _hints.get("force_ocr", False),
            )
            effective_layout_all = config.layout_detection_all_pages or bool(
                _hints.get("layout_detection_all_pages", False),
            )

            effective_type = page_type
            if effective_force_ocr and page_type == PageType.DIGITAL:
                effective_type = PageType.SCANNED

            if effective_type in (PageType.DIGITAL, PageType.MIXED):
                blocks = (
                    preliminary_blocks
                    if preliminary_blocks or char_count >= 5
                    else reader.extract_text_blocks(doc, page_idx)
                )

            if effective_type == PageType.SCANNED and self._use_ocr:
                with ocr_semaphore:
                    if ocr_engine is None:
                        ocr_engine = create_ocr_engine(config.ocr_backend)
                        ocr_available = ocr_engine.is_available()
                    if ocr_available:
                        ocr_blocks, page_gate_result = self._run_ocr(
                            reader, doc, page_idx, ocr_engine,
                            scanned_preprocessor, quality_policy,
                        )
                        # Phase 2: OCR multi-pass -- VLM fallback for low-confidence blocks
                        ocr_blocks = self._apply_ocr_multipass(
                            ocr_blocks, reader, doc, page_idx, width, height,
                        )
                        blocks.extend(ocr_blocks)
                        if ocr_blocks:
                            page_ocr_used = True

            # Skip OCR for MIXED pages whose text layer is already rich: the
            # image area comes from borders/figures/logos, not text-bearing
            # images, so OCR would only re-read and discard overlapping blocks
            # at a large per-page cost. Text-sparse MIXED pages still OCR.
            skip_mixed_ocr = (
                effective_type == PageType.MIXED
                and char_count >= config.mixed_ocr_text_trust_chars
            )
            if effective_type == PageType.MIXED and self._use_ocr and not skip_mixed_ocr:
                with ocr_semaphore:
                    if ocr_engine is None:
                        ocr_engine = create_ocr_engine(config.ocr_backend)
                        ocr_available = ocr_engine.is_available()
                    if ocr_available:
                        ocr_blocks, page_gate_result = self._run_ocr(
                            reader, doc, page_idx, ocr_engine,
                            scanned_preprocessor, quality_policy,
                        )
                        # Phase 2: OCR multi-pass -- VLM fallback for low-confidence blocks
                        ocr_blocks = self._apply_ocr_multipass(
                            ocr_blocks, reader, doc, page_idx, width, height,
                        )
                        for ob in ocr_blocks:
                            if not any(_blocks_overlap(ob, db) for db in blocks):
                                blocks.append(ob)
                                page_ocr_used = True

            layout_blocks = self._maybe_detect_layout(
                reader, doc, page_idx, page_type=effective_type,
                page_strategy=page_strategy,
                override_layout_all=effective_layout_all,
            )

            if layout_blocks and config.layout_reading_order_enabled:
                clean_blocks, page_noise = noise_detector.filter_noise_with_layout(
                    blocks, layout_blocks, height, self._patterns, config,
                )
            else:
                clean_blocks, page_noise = noise_detector.filter_noise_from_blocks(
                    blocks, height, self._patterns, config,
                )

            if layout_blocks and config.layout_reading_order_enabled:
                clean_blocks = column_detector.reorder_blocks_by_layout(
                    clean_blocks, layout_blocks,
                )
            else:
                col_layout = column_detector.detect_columns(clean_blocks, width)
                if col_layout.num_columns > 1:
                    clean_blocks = column_detector.reorder_blocks_by_columns(
                        clean_blocks, col_layout,
                    )

            # Text Quality Gate: evaluate and repair low-quality blocks
            # before classification. Runs in-place on clean_blocks.
            clean_blocks = self._apply_text_quality_gate(clean_blocks)

            clean_blocks = split_heading_body(
                clean_blocks, morpheme_analyzer=self._morpheme_analyzer,
            )

            merged_blocks = self._classify_and_merge(
                clean_blocks, effective_type,
                layout_blocks=layout_blocks,
                page_height=height,
                page_width=width,
            )

            if page_ocr_used:
                merged_blocks = self._apply_heading_detector(
                    merged_blocks, layout_blocks, height,
                )

            merged_blocks = assign_hierarchy(merged_blocks, page_num=page_idx + 1)

            # Phase B-1: layout-detector boost (opt-in via config flag).
            # Single-pass merge_and_label avoids re-scanning the N×M IoU
            # grid when caption_matcher later needs the label map.
            layout_label_map: dict[str, str] = {}
            if layout_blocks:
                from docforge.processing.layout_router import merge_and_label

                merged_blocks, layout_label_map = merge_and_label(
                    merged_blocks,
                    layout_blocks,
                    iou_threshold=config.layout_iou_threshold,
                )

            # Phase 2: Block normalization + confidence-based routing.
            # Produces NormalizedBlock list -> RoutingDecision list.
            # Routing decisions drive downstream dispatch (VLM crop,
            # chart analysis, etc.) via _dispatch_routing_decisions().
            routing_records = self._run_normalization_and_routing(
                merged_blocks, layout_blocks, page_idx, width, height,
                reader, doc,
            )

            # Phase 3: Adaptive Retry Loop -- block-level quality
            # verification + selective retry for garbled blocks.
            retry_stats = {
                "blocks_retried": 0,
                "blocks_fallback_ocr": 0,
                "blocks_fallback_vlm": 0,
                "avg_block_quality": 1.0,
            }
            # Born-digital page guard: when the text layer is cleanly decoded
            # (no real font-decode failure) and substantial, the per-block OCR
            # retry only fires on the fuzzy Korean garbled heuristic's
            # false-positives (dense tabular Korean -- coverage tables,
            # parenthesised insurance terms). That OCR is pure cost (~seconds/
            # page) and, in OCR-less environments (e.g. Apple Vision unavailable
            # in a Linux container), hangs. Trust the text layer and skip the
            # adaptive-retry OCR loop. Genuine decode failures (PUA / U+FFFD)
            # have a high pua-garbled ratio and still retry.
            from docforge.processing.text_quality_utils import is_pua_garbled
            text_layer_clean = (
                char_count >= config.mixed_ocr_text_trust_chars
                and not is_pua_garbled(raw_text)
            )
            if (
                page_strategy
                and page_strategy.primary_method != "skip"
                and not text_layer_clean
            ):
                merged_blocks, retry_stats = self._adaptive_retry(
                    merged_blocks, page_strategy, page_idx,
                    reader, doc, ocr_semaphore, width, height,
                )

            # P0-5: Extract Surya TABLE hint bboxes for pdfplumber.
            table_hint_bboxes: list | None = None
            if layout_blocks:
                from docforge.processing.layout_router import extract_table_hints

                table_hint_bboxes = extract_table_hints(layout_blocks) or None

            page_tables = table_extractor.extract_from_page(
                plumber_doc, page_idx, page_width=width, page_height=height,
                table_hint_bboxes=table_hint_bboxes,
                page_dpi=config.dpi,
            )

            # Phase 2: PaddleTable removed. Scanned pages without
            # pdfplumber tables rely on VLM crop-and-correct via
            # _maybe_route_to_vlm below.

            page_tables = self._maybe_vlm_table_fallback(
                page_tables, effective_type, reader, doc, page_idx,
            )

            page_tables = self._filter_leader_dots(page_tables)

            region_vlm_records, page_tables = self._maybe_route_to_vlm(
                page_tables, pdf_path, page_idx,
            )

            page_confidence = confidence_scorer.score_page(
                merged_blocks, page_type, width, height, page_gate_result,
            )

            # Phase B-2: image region detection + caption matching.
            # Reuses the layout label map computed above instead of
            # re-scanning the IoU grid inside the matcher.
            page_images = self._maybe_extract_images(
                reader, doc, page_idx, merged_blocks, layout_blocks,
                layout_label_map=layout_label_map,
            )

            # Phase B-2+: VLM image captioning — fills alt_text for
            # images that have actual bytes, using describe_image().
            # Phase 2: enriched input -- pass block_type hints and OCR
            # context from routing records for chart/table-aware prompts.
            if page_images and self._llm_engine is not None and config.image_extraction_enabled:
                from docforge.processing.image_vlm_captioner import caption_images

                bt_hints, ctx_texts = self._build_captioner_context(routing_records)
                page_images = caption_images(
                    page_images,
                    self._llm_engine,
                    prompt_hint=config.llm_domain_hint,
                    block_type_hints=bt_hints,
                    context_texts=ctx_texts,
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
                images=tuple(page_images),
            )

            llm_record = None
            if self._llm_engine is not None and should_invoke_llm(page_content, config):
                page_image_raw = pil_to_raw_image(
                    reader.render_page_image(doc, page_idx, config.dpi)
                )
                final_blocks, llm_record = run_llm_fallback(
                    page_content, page_image_raw, self._llm_engine, config,
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
                        images=tuple(page_images),
                    )

            return PageResult(
                page_content=page_content,
                tables_info=(page_tables, height, width),
                noise=page_noise,
                is_toc=False,
                ocr_used=page_ocr_used,
                llm_record=llm_record,
                region_vlm_records=tuple(region_vlm_records),
                blocks_retried=retry_stats["blocks_retried"],
                blocks_fallback_ocr=retry_stats["blocks_fallback_ocr"],
                blocks_fallback_vlm=retry_stats["blocks_fallback_vlm"],
                avg_block_quality=retry_stats["avg_block_quality"],
            )
        finally:
            reader.close(doc)
            table_extractor.close(plumber_doc)


__all__ = ["PageProcessor", "PageResult"]
