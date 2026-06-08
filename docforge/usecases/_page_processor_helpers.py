"""Per-page helper methods for :class:`PageProcessor` (Step22 G25 split).

Pure-movement extraction of the 13 ``_*`` helper methods (OCR pass, layout
detection, classification/merge, normalization+routing, VLM dispatch, adaptive
retry, table VLM fallback, image extraction, etc.) plus the module-level block
overlap helpers from ``page_processor.py``.

These live on a mixin ``_PageProcessorHelpers`` that ``PageProcessor`` inherits.
No logic, signature, or call-order change — the methods resolve via MRO exactly
as before, so ``self._llm_engine.describe_image()`` / ``is_available()`` call
paths (G23) are byte-identical.

The mixin reads ``self._config``, ``self._llm_engine`` etc., which are set by
``PageProcessor.__init__``; ``TYPE_CHECKING`` guards avoid any import cycle back
to the public module.
"""

from __future__ import annotations

import logging as _logging
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.enums import PageType
from docforge.domain.models import (
    RegionVLMRecord,
    Table,
    TextBlock,
)
from docforge.processing import (
    line_merger,
    noise_detector,
    ocr_corrector,
)
from docforge.processing.table_quality_scorer import score_table

if TYPE_CHECKING:
    import threading

    from docforge.domain.value_objects import PageStrategy

_page_logger = _logging.getLogger("docforge.usecases.page_processor")


class _PageProcessorHelpers:
    """Mixin: per-page helper methods consumed by ``process()``.

    Attributes (``_config``, ``_llm_engine``, ``_morpheme_analyzer``,
    ``_avg_font_size``, ``_avg_line_gap``, ``_preprocessing_available``,
    ``_layout_detector``) are provided by ``PageProcessor.__init__``.
    """

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
        override_layout_all: bool = False,
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

        strategy_requires_layout = (
            page_strategy is not None
            and (
                page_strategy.estimated_complexity == "table_heavy"
                or page_strategy.surya_needed
            )
        )

        effective_layout_all_pages = config.layout_detection_all_pages or override_layout_all
        if (
            page_type not in (PageType.SCANNED, PageType.MIXED)
            and not strategy_requires_layout
            and not effective_layout_all_pages
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
        *,
        layout_blocks: list | None = None,
        page_height: float = 0.0,
        page_width: float = 0.0,
    ) -> list[TextBlock]:
        config = self._config
        avg_font_size = self._avg_font_size
        avg_line_gap = self._avg_line_gap
        morpheme_analyzer = self._morpheme_analyzer

        from docforge.processing.block_classifier import classify_blocks

        def _signal_classify(blocks: list[TextBlock]) -> list[TextBlock]:
            return classify_blocks(
                blocks,
                avg_font_size=avg_font_size,
                page_height=page_height,
                page_width=page_width,
                layout_blocks=layout_blocks,
            )

        if page_type == PageType.DIGITAL:
            classified = _signal_classify(clean_blocks)
            return line_merger.merge_lines(
                classified, avg_font_size, avg_line_gap, config,
                morpheme_analyzer=morpheme_analyzer,
            )

        merged_first = line_merger.merge_lines(
            clean_blocks, avg_font_size, avg_line_gap, config,
            morpheme_analyzer=morpheme_analyzer,
        )
        return _signal_classify(merged_first)

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

    def _apply_text_quality_gate(
        self,
        blocks: list[TextBlock],
    ) -> list[TextBlock]:
        """Run the text quality gate on extracted blocks.

        Low-quality blocks get their text repaired (encoding fix, CID
        strip) or their confidence penalized. Original blocks are never
        mutated -- new TextBlock instances are returned.

        Graceful degradation: returns original blocks on any error.
        """
        if not blocks:
            return blocks
        try:
            from docforge.processing.text_quality_gate import TextQualityGate

            gate = TextQualityGate()
            result: list[TextBlock] = []
            repaired_count = 0
            penalized_count = 0
            for block in blocks:
                qr = gate.evaluate(block.text)
                if qr.repair_applied and qr.repaired_text:
                    repaired_count += 1
                    new_confidence = max(0.0, block.confidence - qr.confidence_penalty)
                    result.append(TextBlock(
                        text=qr.repaired_text,
                        bbox=block.bbox,
                        font=block.font,
                        block_type=block.block_type,
                        heading_level=block.heading_level,
                        confidence=new_confidence,
                        block_id=block.block_id,
                        parent_id=block.parent_id,
                    ))
                elif qr.confidence_penalty > 0:
                    penalized_count += 1
                    new_confidence = max(0.0, block.confidence - qr.confidence_penalty)
                    result.append(TextBlock(
                        text=block.text,
                        bbox=block.bbox,
                        font=block.font,
                        block_type=block.block_type,
                        heading_level=block.heading_level,
                        confidence=new_confidence,
                        block_id=block.block_id,
                        parent_id=block.parent_id,
                    ))
                else:
                    result.append(block)
            if repaired_count or penalized_count:
                _page_logger.info(
                    "quality_gate: %d blocks repaired, %d penalized (of %d)",
                    repaired_count, penalized_count, len(blocks),
                )
            return result
        except Exception:
            _page_logger.warning(
                "Text quality gate failed, using original blocks",
                exc_info=True,
            )
            return blocks

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
