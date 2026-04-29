"""LLM Fallback router.

Routes low-confidence pages to Vision LLM for correction.
Falls back to original OCR results when LLM is unavailable or produces worse output.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docforge.domain.models import LLMFallbackRecord, PageContent, TextBlock
from docforge.domain.value_objects import RawImage
from docforge.infrastructure.config import ParserConfig

if TYPE_CHECKING:
    from docforge.domain.ports import VisionLLMEngine

logger = logging.getLogger(__name__)


def should_invoke_llm(page: PageContent, config: ParserConfig) -> bool:
    if not config.llm_fallback_enabled:
        return False
    if page.confidence is None:
        return False
    return page.confidence.overall < config.llm_confidence_threshold


def run_llm_fallback(
    page: PageContent,
    page_image: RawImage,
    llm_engine: VisionLLMEngine,
    config: ParserConfig,
) -> tuple[list[TextBlock], LLMFallbackRecord]:
    original_blocks = list(page.blocks)
    original_confidence = page.confidence.overall if page.confidence else 0.0

    try:
        llm_blocks = llm_engine.correct_page(
            image=page_image,
            ocr_blocks=original_blocks,
            prompt_hint=config.llm_domain_hint,
        )
    except Exception:
        logger.warning("LLM correction failed (page %d), keeping OCR result", page.page_num, exc_info=True)
        return original_blocks, LLMFallbackRecord(
            page_num=page.page_num,
            trigger_confidence=original_confidence,
            llm_confidence=0.0,
            adopted=False,
            reason="LLM invocation failed",
        )

    llm_confidence = _avg_confidence(llm_blocks)
    llm_char_count = sum(len(b.text) for b in llm_blocks)
    orig_char_count = sum(len(b.text) for b in original_blocks)

    if orig_char_count > 0 and llm_char_count < orig_char_count * config.llm_char_loss_threshold:
        return original_blocks, LLMFallbackRecord(
            page_num=page.page_num,
            trigger_confidence=original_confidence,
            llm_confidence=llm_confidence,
            adopted=False,
            reason=f"LLM rejected — char loss ({llm_char_count} < {orig_char_count}*{config.llm_char_loss_threshold})",
        )

    if llm_confidence > original_confidence + config.llm_confidence_margin:
        return llm_blocks, LLMFallbackRecord(
            page_num=page.page_num,
            trigger_confidence=original_confidence,
            llm_confidence=llm_confidence,
            adopted=True,
            reason=f"LLM adopted — confidence {original_confidence:.3f} -> {llm_confidence:.3f}",
        )

    return original_blocks, LLMFallbackRecord(
        page_num=page.page_num,
        trigger_confidence=original_confidence,
        llm_confidence=llm_confidence,
        adopted=False,
        reason="LLM rejected — no significant improvement",
    )


def _avg_confidence(blocks: list[TextBlock]) -> float:
    if not blocks:
        return 0.0
    return sum(b.confidence for b in blocks) / len(blocks)
