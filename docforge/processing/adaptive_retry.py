"""Adaptive Retry Loop -- block-level quality verification + selective retry.

Phase 3 component that scores individual TextBlocks via
BlockQualityVerifier, identifies retry candidates, and attempts
fallback methods (OCR / VLM) to improve quality.

This module is called from PageProcessor but lives separately to
keep page_processor.py within the 800-line budget.
"""

from __future__ import annotations

import io
import logging
import threading
from typing import TYPE_CHECKING

from docforge.domain.models import TextBlock
from docforge.domain.value_objects import PageStrategy
from docforge.processing.block_normalizer import normalize_text_block
from docforge.processing.block_quality_verifier import BlockQualityVerifier
from docforge.usecases.ocr_factory import create_ocr_engine

if TYPE_CHECKING:
    from docforge.adapters.pymupdf_reader import PyMuPDFReader
    from docforge.domain.ports import VisionLLMEngine
    from docforge.infrastructure.config import ParserConfig

logger = logging.getLogger(__name__)


RetryStats = dict[str, int | float]

_DEFAULT_STATS: RetryStats = {
    "blocks_retried": 0,
    "blocks_fallback_ocr": 0,
    "blocks_fallback_vlm": 0,
    "avg_block_quality": 1.0,
}


def adaptive_retry(
    blocks: list[TextBlock],
    page_strategy: PageStrategy,
    page_idx: int,
    reader: "PyMuPDFReader",
    doc: object,
    ocr_semaphore: threading.Semaphore,
    width: float,
    height: float,
    config: "ParserConfig",
    llm_engine: "VisionLLMEngine | None",
) -> tuple[list[TextBlock], RetryStats]:
    """Block-level quality verification + selective retry.

    Converts TextBlocks to NormalizedBlocks for quality scoring,
    retries low-quality blocks through the fallback_chain, then
    maps improved results back to TextBlocks.

    Returns (final_blocks, retry_stats_dict).
    """
    if not blocks:
        return blocks, dict(_DEFAULT_STATS)

    page_num = page_idx + 1
    verifier = BlockQualityVerifier()
    threshold = page_strategy.block_quality_threshold

    # Score all blocks
    norm_blocks = [normalize_text_block(tb, page_num=page_num) for tb in blocks]
    results = verifier.score_blocks(norm_blocks, threshold)
    candidates = verifier.filter_retry_candidates(norm_blocks, results)

    avg_quality = sum(r.score for r in results) / len(results) if results else 1.0
    if not candidates:
        stats = dict(_DEFAULT_STATS)
        stats["avg_block_quality"] = avg_quality
        return blocks, stats

    logger.info(
        "Adaptive retry: page=%d, candidates=%d/%d",
        page_num, len(candidates), len(blocks),
    )

    # Build index: norm_block.block_id -> position in blocks list
    id_to_idx: dict[str, int] = {
        nb.block_id: i for i, nb in enumerate(norm_blocks)
    }
    final_blocks = list(blocks)
    stats: RetryStats = {
        "blocks_retried": 0,
        "blocks_fallback_ocr": 0,
        "blocks_fallback_vlm": 0,
    }

    for norm_block, quality_result in candidates:
        if quality_result.recommended_fallback == "none":
            continue

        block_idx = id_to_idx.get(norm_block.block_id)
        if block_idx is None:
            continue
        original_tb = blocks[block_idx]

        for fallback_method in page_strategy.fallback_chain:
            retried_tb = _retry_block(
                original_tb, fallback_method, page_idx,
                reader, doc, ocr_semaphore, width, height,
                config, llm_engine,
            )
            if retried_tb is None:
                continue

            # Score the retry result
            retry_norm = normalize_text_block(retried_tb, page_num=page_num)
            retry_result = verifier.score(retry_norm, threshold)
            if retry_result.score > quality_result.score:
                final_blocks[block_idx] = retried_tb
                stats["blocks_retried"] = int(stats["blocks_retried"]) + 1
                if fallback_method == "apple_vision_ocr":
                    stats["blocks_fallback_ocr"] = int(stats["blocks_fallback_ocr"]) + 1
                elif fallback_method == "vlm_full":
                    stats["blocks_fallback_vlm"] = int(stats["blocks_fallback_vlm"]) + 1
                break  # improved -- move to next candidate

    # Recompute average quality on final blocks
    final_norms = [normalize_text_block(tb, page_num=page_num) for tb in final_blocks]
    final_results = verifier.score_blocks(final_norms, threshold)
    stats["avg_block_quality"] = (
        sum(r.score for r in final_results) / len(final_results)
        if final_results else 1.0
    )
    return final_blocks, stats


def _retry_block(
    block: TextBlock,
    fallback_method: str,
    page_idx: int,
    reader: "PyMuPDFReader",
    doc: object,
    ocr_semaphore: threading.Semaphore,
    width: float,
    height: float,
    config: "ParserConfig",
    llm_engine: "VisionLLMEngine | None",
) -> TextBlock | None:
    """Retry a single block via the specified fallback method.

    - "apple_vision_ocr": re-OCR the full page, find best overlap
    - "vlm_full": crop bbox region and send to VLM for text extraction

    Returns a replacement TextBlock or None on failure.
    """
    try:
        from docforge.adapters.image_converter import pil_to_raw_image

        image = reader.render_page_image(doc, page_idx, config.dpi)
        raw_img = pil_to_raw_image(image)

        if fallback_method == "apple_vision_ocr":
            with ocr_semaphore:
                ocr_engine = create_ocr_engine(config.ocr_backend)
                if not ocr_engine.is_available():
                    return None
                ocr_blocks = ocr_engine.recognize(raw_img)
            return _find_best_ocr_overlap(block, ocr_blocks)

        if fallback_method == "vlm_full":
            if llm_engine is None:
                return None
            from docforge.processing.roi_cropper import crop_region_from_image
            from PIL import Image as _PILImage

            buf = io.BytesIO()
            if hasattr(image, "save"):
                image.save(buf, format="PNG")
            else:
                _PILImage.fromarray(raw_img.data).save(buf, format="PNG")
            page_image_bytes = buf.getvalue()

            cropped = crop_region_from_image(
                page_image_bytes, int(width), int(height),
                block.bbox, padding=10,
            )
            if not cropped:
                return None

            vlm_text = llm_engine.describe_image(
                image_data=cropped,
                format="png",
                prompt_hint="이 영역의 텍스트를 정확하게 추출하세요. 출력: 텍스트만.",
            )
            if vlm_text and vlm_text.strip():
                return TextBlock(
                    text=vlm_text.strip(),
                    bbox=block.bbox,
                    font=block.font,
                    block_type=block.block_type,
                    heading_level=block.heading_level,
                    confidence=0.85,
                    block_id=block.block_id,
                    parent_id=block.parent_id,
                )
            return None

        return None
    except Exception:
        logger.warning(
            "Retry block failed (method=%s, page=%d)",
            fallback_method, page_idx,
            exc_info=True,
        )
        return None


def _find_best_ocr_overlap(
    target: TextBlock, candidates: list[TextBlock],
) -> TextBlock | None:
    """Find the OCR block that best overlaps with the target bbox."""
    best: TextBlock | None = None
    best_iou = 0.0
    for c in candidates:
        iou = target.bbox.iou(c.bbox)
        if iou > best_iou:
            best_iou = iou
            best = c
    return best if best_iou > 0.2 else None


__all__ = ["adaptive_retry"]
