"""Single-page processing pipeline.

Extracts the per-page logic from ``parse_pdf`` into a focused class that
owns one page's lifecycle: open thread-local doc handles, classify, OCR,
extract tables, run noise/structure passes, and optionally route through
LLM/VLM fallbacks.

The processor is stateless across pages — each ``process()`` call opens
its own document handles for thread safety.
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
    line_merger,
    noise_detector,
    ocr_corrector,
    page_classifier,
    text_structurer,
)
from docforge.processing.block_splitter import split_heading_body
from docforge.processing.heading_hierarchy import assign_hierarchy
from docforge.processing.llm_fallback_router import run_llm_fallback, should_invoke_llm
from docforge.processing.noise_detector import LearnedPatterns
from docforge.processing.table_quality_scorer import score_table
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


class PageProcessor:
    """Processes a single PDF page end-to-end.

    Heavy dependencies (config, engines, learned patterns) are injected
    once; ``process()`` opens its own per-call document handles so it is
    safe to invoke from a thread pool.
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
    ) -> PageResult:
        """Process a single page. Opens its own doc/plumber handles."""
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

            effective_type = page_type
            if self._force_ocr and page_type == PageType.DIGITAL:
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

            if effective_type == PageType.MIXED and self._use_ocr:
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

            clean_blocks = split_heading_body(
                clean_blocks, morpheme_analyzer=self._morpheme_analyzer,
            )

            merged_blocks = self._classify_and_merge(clean_blocks, effective_type)

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
            if page_strategy and page_strategy.primary_method != "skip":
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

    # --- internal helpers --------------------------------------------------

    def _run_ocr(
        self,
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
        ocr_engine,
        scanned_preprocessor,
        quality_policy,
    ) -> tuple[list[TextBlock], object | None]:
        from docforge.adapters.image_converter import pil_to_raw_image

        config = self._config
        image = reader.render_page_image(doc, page_idx, config.dpi)
        raw_img = pil_to_raw_image(image)
        page_gate_result = None
        if self._preprocessing_available and scanned_preprocessor is not None:
            from docforge.processing.preprocessing_router import process_scanned_page

            ocr_blocks, _decision, page_gate_result = process_scanned_page(
                raw_img, ocr_engine, scanned_preprocessor, quality_policy,
            )
        else:
            ocr_blocks = ocr_engine.recognize(raw_img)
        ocr_blocks = ocr_corrector.correct_blocks(ocr_blocks, config)
        return ocr_blocks, page_gate_result

    def _maybe_detect_layout(
        self,
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
        page_type: PageType = PageType.DIGITAL,
        page_strategy: "PageStrategy | None" = None,
    ):
        """Run layout detection if applicable.

        **Phase 2 -- Surya Conditional Activation**:
        - ``config.layout_detection_enabled`` acts as a global kill-switch.
          When ``False`` (default), layout detection is always off.
        - When ``True``, layout detection is further gated by page type:
          only ``SCANNED`` and ``MIXED`` pages trigger Surya.  Digital
          pages already have reliable font/structure metadata and do not
          benefit from the extra cost.
        - **Exception**: Digital pages with ``table_heavy`` estimated
          complexity also trigger Surya for TABLE hint extraction.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)

        from docforge.adapters.image_converter import pil_to_raw_image

        config = self._config
        detector = self._layout_detector

        # Global kill-switch
        if not config.layout_detection_enabled or detector is None:
            return []
        if not detector.is_available():
            return []

        is_table_heavy = (
            page_strategy is not None
            and page_strategy.estimated_complexity == "table_heavy"
        )

        if (
            page_type not in (PageType.SCANNED, PageType.MIXED)
            and not is_table_heavy
            and not config.layout_detection_all_pages
        ):
            _logger.debug(
                "Skipping layout detection for %s page (page_idx=%d)",
                page_type.value,
                page_idx,
            )
            return []

        try:
            image = reader.render_page_image(doc, page_idx, config.dpi)
            raw_img = pil_to_raw_image(image)
            return detector.detect(raw_img, page_num=page_idx + 1)
        except Exception:  # pragma: no cover - defensive
            _logger.warning(
                "Layout detection failed for page %d", page_idx, exc_info=True,
            )
            return []

    def _maybe_extract_images(
        self,
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
        text_blocks: list[TextBlock],
        layout_blocks,
        layout_label_map: dict[str, str] | None = None,
    ):
        """Detect image regions; extract bytes only if extraction is on.

        When ``image_placeholders_enabled`` (default True), every image
        region is recorded with bbox + deterministic id + empty bytes so
        markdown placeholders can preserve the spatial slot for future
        VLM caption injection. ``image_extraction_enabled`` adds the
        bytes-decoding step on top of that.
        """
        config = self._config
        if not (config.image_placeholders_enabled or config.image_extraction_enabled):
            return []
        try:
            from docforge.processing.caption_matcher import match_captions
            from docforge.processing.image_extractor import extract_images

            images = extract_images(
                reader, doc, page_idx,
                include_bytes=config.image_extraction_enabled,
            )
            if not images:
                return []
            if layout_label_map is None:
                # Fall back: compute label map only if caller didn't pass one.
                from docforge.processing.layout_router import build_layout_label_map
                layout_label_map = build_layout_label_map(
                    text_blocks, layout_blocks or [],
                    iou_threshold=config.layout_iou_threshold,
                )
            return match_captions(
                images, text_blocks,
                layout_label_map=layout_label_map,
                proximity_pt=config.caption_proximity_pt,
            )
        except Exception:  # pragma: no cover - defensive
            return []

    def _classify_and_merge(
        self,
        clean_blocks: list[TextBlock],
        page_type: PageType,
    ) -> list[TextBlock]:
        config = self._config
        avg_font_size = self._avg_font_size
        avg_line_gap = self._avg_line_gap
        morpheme_analyzer = self._morpheme_analyzer
        domain_profile = self._domain_profile

        def _classify(block: TextBlock) -> TextBlock:
            block_type, heading_level = text_structurer.classify_block(
                block.text,
                block.font.size,
                block.font.is_bold,
                avg_font_size,
                config,
                domain_profile=domain_profile,
            )
            return TextBlock(
                text=block.text,
                bbox=block.bbox,
                font=block.font,
                block_type=block_type,
                heading_level=heading_level,
                confidence=block.confidence,
            )

        if page_type == PageType.DIGITAL:
            classified = [_classify(b) for b in clean_blocks]
            return line_merger.merge_lines(
                classified, avg_font_size, avg_line_gap, config,
                morpheme_analyzer=morpheme_analyzer,
            )

        merged_first = line_merger.merge_lines(
            clean_blocks, avg_font_size, avg_line_gap, config,
            morpheme_analyzer=morpheme_analyzer,
        )
        return [_classify(b) for b in merged_first]

    def _maybe_vlm_table_fallback(
        self,
        page_tables: list[Table],
        page_type: PageType,
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
    ) -> list[Table]:
        """Apply table quality check and flag low-quality tables for VLM re-extraction.

        Phase 2 change: PaddleTable fallback removed. Low-quality tables
        are now handled by ``_maybe_route_to_vlm`` downstream (VLM
        crop-and-correct). This method just ensures quality scoring runs
        on all page types (except NOISE) so the downstream VLM router
        gets accurate quality signals.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)

        config = self._config
        # Phase 2: extend quality check to all page types except NOISE
        if page_type == PageType.NOISE or not page_tables:
            return page_tables

        weights = config.table_config.scorer_weights
        threshold = config.table_quality_threshold

        result: list[Table] = []
        for tbl in page_tables:
            tbl_score = score_table(tbl, weights)
            if tbl_score < threshold:
                _logger.info(
                    "Table quality %.2f < threshold %.2f on page %d -- "
                    "will be candidate for VLM re-extraction downstream",
                    tbl_score,
                    threshold,
                    page_idx + 1,
                )
            result.append(tbl)
        return result

    # --- Phase 2 integration helpers -----------------------------------------

    def _apply_ocr_multipass(
        self,
        ocr_blocks: list[TextBlock],
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
        page_width: float,
        page_height: float,
    ) -> list[TextBlock]:
        """Split OCR blocks by confidence; re-OCR low ones via VLM fallback.

        Graceful degradation: when no VLM engine is available, returns the
        original blocks unchanged.
        """
        if not ocr_blocks:
            return ocr_blocks
        try:
            from docforge.processing.ocr_multipass import (
                evaluate_ocr_blocks,
                fallback_ocr_via_vlm,
            )

            acceptable, low = evaluate_ocr_blocks(ocr_blocks)
            if not low:
                return ocr_blocks
            if self._llm_engine is None:
                _page_logger.debug(
                    "OCR multipass: %d low-confidence blocks but no VLM engine",
                    len(low),
                )
                return acceptable + low

            from docforge.adapters.image_converter import pil_to_raw_image

            image = reader.render_page_image(doc, page_idx, self._config.dpi)
            raw_img = pil_to_raw_image(image)
            # Convert to PNG bytes for roi_cropper
            import io
            from PIL import Image as _PILImage

            buf = io.BytesIO()
            if hasattr(image, "save"):
                image.save(buf, format="PNG")
            else:
                _PILImage.fromarray(raw_img.data).save(buf, format="PNG")
            page_image_bytes = buf.getvalue()

            corrected = fallback_ocr_via_vlm(
                low,
                self._llm_engine,
                page_image_bytes,
                int(page_width),
                int(page_height),
            )
            return acceptable + corrected
        except Exception:
            _page_logger.warning(
                "OCR multipass failed for page %d, using original blocks",
                page_idx,
                exc_info=True,
            )
            return ocr_blocks

    def _apply_heading_detector(
        self,
        blocks: list[TextBlock],
        layout_blocks: list,
        page_height: float,
    ) -> list[TextBlock]:
        """Run alternative heading detection for OCR pages (font_size=0.0).

        Graceful degradation: returns original blocks on any error.
        """
        try:
            from docforge.processing.heading_detector import detect_headings_ocr

            return detect_headings_ocr(blocks, layout_blocks or None, page_height)
        except Exception:
            _page_logger.warning(
                "Heading detector failed, using original blocks",
                exc_info=True,
            )
            return blocks

    def _run_normalization_and_routing(
        self,
        merged_blocks: list[TextBlock],
        layout_blocks: list,
        page_idx: int,
        page_width: float,
        page_height: float,
        reader: PyMuPDFReader,
        doc: object,
    ) -> list:
        """Normalize blocks and apply confidence-based routing rules.

        Returns a list of ``RoutingDecision`` objects for downstream
        dispatch. On failure, returns an empty list (graceful degradation).
        """
        try:
            from docforge.processing.block_normalizer import (
                merge_normalized,
                normalize_layout_block,
                normalize_text_block,
            )
            from docforge.processing.layout_router import route_blocks

            page_num = page_idx + 1

            # Step 1: Normalize text blocks
            norm_text = [
                normalize_text_block(tb, page_num=page_num)
                for tb in merged_blocks
            ]

            # Step 2: Normalize layout blocks (if any)
            norm_layout = [
                normalize_layout_block(lb)
                for lb in (layout_blocks or [])
            ]

            # Step 3: Merge via IoU matching
            norm_merged = merge_normalized(
                norm_text,
                norm_layout,
                iou_threshold=self._config.layout_iou_threshold,
            )

            # Step 4: Apply routing rules
            decisions = route_blocks(norm_merged)

            _page_logger.info(
                "Phase 2 routing: page=%d, blocks=%d, decisions=%d",
                page_num,
                len(norm_merged),
                len(decisions),
            )

            # Step 5: Dispatch VLM-requiring decisions
            self._dispatch_routing_decisions(
                decisions, reader, doc, page_idx, page_width, page_height,
            )

            return decisions
        except Exception:
            _page_logger.warning(
                "Block normalization/routing failed for page %d",
                page_idx,
                exc_info=True,
            )
            return []

    def _dispatch_routing_decisions(
        self,
        decisions: list,
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
        page_width: float,
        page_height: float,
    ) -> None:
        """Process routing decisions that require VLM invocation.

        Actions handled:
        - ``vlm_crop``: Crop table region, send to VLM for re-extraction.
        - ``vlm_chart``: Crop chart region, send enriched prompt to VLM.
        - ``vlm_caption``: Crop figure region, generate alt-text.
        - ``table_parser``, ``markdown``, ``fallback``: No additional work
          (handled by existing pipeline paths).

        Results are logged for audit. This method does not modify the
        downstream ``page_tables`` or ``merged_blocks`` -- it enriches the
        routing records for reporting and future use.
        """
        if not decisions or self._llm_engine is None:
            return

        vlm_actions = {"vlm_crop", "vlm_chart", "vlm_caption"}
        vlm_decisions = [d for d in decisions if d.action in vlm_actions]
        if not vlm_decisions:
            return

        try:
            from docforge.processing.roi_cropper import crop_region_from_image

            # Render page image once for all crops
            from docforge.adapters.image_converter import pil_to_raw_image

            image = reader.render_page_image(doc, page_idx, self._config.dpi)
            import io
            from PIL import Image as _PILImage

            buf = io.BytesIO()
            if hasattr(image, "save"):
                image.save(buf, format="PNG")
            else:
                raw_img = pil_to_raw_image(image)
                _PILImage.fromarray(raw_img.data).save(buf, format="PNG")
            page_image_bytes = buf.getvalue()

            for decision in vlm_decisions:
                block = decision.block
                cropped = crop_region_from_image(
                    page_image_bytes,
                    int(page_width),
                    int(page_height),
                    block.bbox,
                    padding=10,
                )
                if not cropped:
                    _page_logger.warning(
                        "ROI crop empty for block %s (action=%s)",
                        block.block_id,
                        decision.action,
                    )
                    continue

                if decision.action == "vlm_chart":
                    # Enriched input: image + OCR context text
                    result = self._llm_engine.describe_image(
                        image_data=cropped,
                        format="png",
                        prompt_hint=self._config.llm_domain_hint,
                        block_type="chart",
                        context_text=block.text[:500] if block.text else "",
                        bbox_info=f"[{block.bbox.x0:.1f},{block.bbox.y0:.1f},"
                                  f"{block.bbox.x1:.1f},{block.bbox.y1:.1f}]",
                    )
                    _page_logger.info(
                        "VLM chart analysis: block=%s, result_len=%d",
                        block.block_id,
                        len(result) if result else 0,
                    )
                elif decision.action == "vlm_caption":
                    result = self._llm_engine.describe_image(
                        image_data=cropped,
                        format="png",
                        prompt_hint=self._config.llm_domain_hint,
                        block_type="figure",
                    )
                    _page_logger.info(
                        "VLM caption: block=%s, result_len=%d",
                        block.block_id,
                        len(result) if result else 0,
                    )
                elif decision.action == "vlm_crop":
                    # Table VLM re-extraction -- handled downstream by
                    # _maybe_route_to_vlm. Log for audit only.
                    _page_logger.info(
                        "VLM crop routed: block=%s, conf=%.2f",
                        block.block_id,
                        decision.confidence,
                    )
        except Exception:
            _page_logger.warning(
                "Routing dispatch failed for page %d",
                page_idx,
                exc_info=True,
            )

    @staticmethod
    def _build_captioner_context(
        routing_records: list,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Extract block_type hints and context texts from routing decisions.

        Returns ``(block_type_hints, context_texts)`` dicts keyed by block_id
        for use by ``caption_images()``.
        """
        bt_hints: dict[str, str] = {}
        ctx_texts: dict[str, str] = {}
        for decision in routing_records:
            block = decision.block
            if decision.action == "vlm_chart":
                bt_hints[block.block_id] = "chart"
                if block.text:
                    ctx_texts[block.block_id] = block.text[:500]
            elif decision.action == "vlm_caption":
                bt_hints[block.block_id] = "figure"
            elif decision.action == "vlm_crop":
                bt_hints[block.block_id] = "table"
        return bt_hints, ctx_texts

    # --- Phase 3: Adaptive Retry Loop ----------------------------------------

    def _adaptive_retry(
        self,
        blocks: list[TextBlock],
        page_strategy: PageStrategy,
        page_idx: int,
        reader: PyMuPDFReader,
        doc: object,
        ocr_semaphore: "threading.Semaphore",
        width: float,
        height: float,
    ) -> tuple[list[TextBlock], dict]:
        """Delegate to processing.adaptive_retry module."""
        from docforge.processing.adaptive_retry import adaptive_retry

        return adaptive_retry(
            blocks, page_strategy, page_idx,
            reader, doc, ocr_semaphore, width, height,
            self._config, self._llm_engine,
        )

    # --- existing internal helpers -------------------------------------------

    def _filter_leader_dots(self, page_tables: list[Table]) -> list[Table]:
        config = self._config
        filtered: list[Table] = []
        for table in page_tables:
            filtered_cells, new_rows = noise_detector.filter_leader_dots_from_table(
                list(table.cells), table.rows, table.cols,
            )
            if new_rows >= config.min_table_rows:
                from docforge.domain.models import TableCell as TC

                filtered.append(Table(
                    cells=tuple(c for c in filtered_cells if isinstance(c, TC)),
                    rows=new_rows,
                    cols=table.cols,
                    bbox=table.bbox,
                    confidence=table.confidence,
                    needs_review=table.needs_review,
                ))
        return filtered

    def _maybe_route_to_vlm(
        self,
        page_tables: list[Table],
        pdf_path: Path,
        page_idx: int,
    ) -> tuple[list[RegionVLMRecord], list[Table]]:
        config = self._config
        if not (config.region_vlm_enabled and self._llm_engine is not None and page_tables):
            return [], page_tables

        from docforge.adapters.region_crop import crop_table_region
        from docforge.processing.region_vlm_router import route_table_to_vlm

        weights = config.table_config.scorer_weights
        threshold = config.table_quality_threshold

        records: list[RegionVLMRecord] = []
        improved: list[Table] = []
        for tbl in page_tables:
            tbl_score = score_table(tbl, weights)
            if tbl_score < threshold:
                cropped = crop_table_region(pdf_path, page_idx, tbl.bbox, config.dpi)
                if cropped is not None:
                    vlm_table, vlm_record = route_table_to_vlm(
                        cropped_image=cropped,
                        original_bbox=tbl.bbox,
                        quality_score=tbl_score,
                        page_num=page_idx + 1,
                        llm_engine=self._llm_engine,
                        domain_hint=config.llm_domain_hint,
                    )
                    records.append(vlm_record)
                    if vlm_table is not None:
                        improved.append(vlm_table)
                        continue
            improved.append(tbl)
        return records, improved


def _find_best_overlap(target: Table, candidates: list[Table]) -> Table | None:
    """Find the candidate table with the best BBox overlap to the target."""
    best: Table | None = None
    best_iou = 0.0

    for candidate in candidates:
        iou = target.bbox.iou(candidate.bbox)
        if iou > best_iou:
            best_iou = iou
            best = candidate

    return best if best_iou > 0.2 else None


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


__all__ = ["PageProcessor", "PageResult"]
