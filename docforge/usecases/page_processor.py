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
from docforge.domain.value_objects import ImageQualityPolicy
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

    def process(
        self,
        page_idx: int,
        pdf_path: Path,
        ocr_semaphore: threading.Semaphore,
        log_fn: "Callable[[str], None]",
        total_pages: int,
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

            # COVER/TOC pages bypass the heavy classification pipeline.
            # We preserve their text as-is (no heading hierarchy, no line
            # merging) so the markdown serializer can render them under
            # explicit ``## [표지]`` / ``## [목차]`` section headers.
            if page_type in (PageType.COVER, PageType.TOC):
                page_content = PageContent(
                    page_num=page_idx + 1,
                    page_type=page_type,
                    blocks=tuple(preliminary_blocks),
                    tables=(),
                    raw_text=raw_text,
                    width=width,
                    height=height,
                    confidence=None,
                )
                return PageResult(
                    page_content=page_content,
                    tables_info=None,
                    noise=NoiseStats(),
                    is_toc=False,
                    ocr_used=False,
                )

            blocks: list[TextBlock] = []
            page_gate_result = None
            page_ocr_used = False

            if page_type in (PageType.DIGITAL, PageType.MIXED):
                # Reuse preliminary_blocks when we already extracted them
                # (char_count >= 5). Falsy-check would re-extract for valid
                # but empty pages — explicit None sentinel avoids that.
                blocks = (
                    preliminary_blocks
                    if preliminary_blocks or char_count >= 5
                    else reader.extract_text_blocks(doc, page_idx)
                )

            if page_type == PageType.SCANNED and self._use_ocr:
                with ocr_semaphore:
                    if ocr_engine is None:
                        ocr_engine = create_ocr_engine(config.ocr_backend)
                        ocr_available = ocr_engine.is_available()
                    if ocr_available:
                        ocr_blocks, page_gate_result = self._run_ocr(
                            reader, doc, page_idx, ocr_engine,
                            scanned_preprocessor, quality_policy,
                        )
                        blocks.extend(ocr_blocks)
                        if ocr_blocks:
                            page_ocr_used = True

            if page_type == PageType.MIXED and self._use_ocr:
                with ocr_semaphore:
                    if ocr_engine is None:
                        ocr_engine = create_ocr_engine(config.ocr_backend)
                        ocr_available = ocr_engine.is_available()
                    if ocr_available:
                        ocr_blocks, page_gate_result = self._run_ocr(
                            reader, doc, page_idx, ocr_engine,
                            scanned_preprocessor, quality_policy,
                        )
                        for ob in ocr_blocks:
                            if not any(_blocks_overlap(ob, db) for db in blocks):
                                blocks.append(ob)
                                page_ocr_used = True

            clean_blocks, page_noise = noise_detector.filter_noise_from_blocks(
                blocks, height, self._patterns, config
            )

            col_layout = column_detector.detect_columns(clean_blocks, width)
            if col_layout.num_columns > 1:
                clean_blocks = column_detector.reorder_blocks_by_columns(
                    clean_blocks, col_layout,
                )

            clean_blocks = split_heading_body(
                clean_blocks, morpheme_analyzer=self._morpheme_analyzer,
            )

            merged_blocks = self._classify_and_merge(clean_blocks, page_type)
            merged_blocks = assign_hierarchy(merged_blocks, page_num=page_idx + 1)

            # Phase B-1: layout-detector boost (opt-in via config flag).
            # Single-pass merge_and_label avoids re-scanning the N×M IoU
            # grid when caption_matcher later needs the label map.
            layout_blocks = self._maybe_detect_layout(reader, doc, page_idx)
            layout_label_map: dict[str, str] = {}
            if layout_blocks:
                from docforge.processing.layout_router import merge_and_label

                merged_blocks, layout_label_map = merge_and_label(
                    merged_blocks,
                    layout_blocks,
                    iou_threshold=config.layout_iou_threshold,
                )

            page_tables = table_extractor.extract_from_page(
                plumber_doc, page_idx, page_width=width, page_height=height,
            )

            if page_type == PageType.SCANNED and not page_tables:
                from docforge.adapters.paddle_table import PaddleTableExtractor

                paddle_tables = PaddleTableExtractor()
                if paddle_tables.is_available():
                    image = reader.render_page_image(doc, page_idx, config.dpi)
                    page_tables = paddle_tables.extract_from_image(image)

            page_tables = self._maybe_paddle_fallback(
                page_tables, page_type, reader, doc, page_idx,
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
    ):
        """Run layout detection if enabled and the backend is available."""
        from docforge.adapters.image_converter import pil_to_raw_image

        config = self._config
        detector = self._layout_detector
        if not config.layout_detection_enabled or detector is None:
            return []
        if not detector.is_available():
            return []
        try:
            image = reader.render_page_image(doc, page_idx, config.dpi)
            raw_img = pil_to_raw_image(image)
            return detector.detect(raw_img, page_num=page_idx + 1)
        except Exception:  # pragma: no cover - defensive
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

    def _maybe_paddle_fallback(
        self,
        page_tables: list[Table],
        page_type: PageType,
        reader: PyMuPDFReader,
        doc: object,
        page_idx: int,
    ) -> list[Table]:
        config = self._config
        if not (
            config.region_vlm_paddle_fallback
            and page_type in (PageType.DIGITAL, PageType.MIXED)
            and page_tables
        ):
            return page_tables

        weights = config.table_config.scorer_weights
        threshold = config.table_quality_threshold

        has_low_quality = any(
            score_table(tbl, weights) < threshold for tbl in page_tables
        )
        if not has_low_quality:
            return page_tables

        from docforge.adapters.paddle_table import PaddleTableExtractor

        paddle_ext = PaddleTableExtractor()
        if not paddle_ext.is_available():
            return page_tables

        img = reader.render_page_image(doc, page_idx, config.dpi)
        paddle_results = paddle_ext.extract_from_image(img)

        improved: list[Table] = []
        for tbl in page_tables:
            tbl_score = score_table(tbl, weights)
            if tbl_score < threshold:
                best_paddle = _find_best_overlap(tbl, paddle_results)
                if best_paddle is not None and score_table(best_paddle, weights) > tbl_score:
                    improved.append(best_paddle)
                    continue
            improved.append(tbl)
        return improved

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
