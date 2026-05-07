"""OCR Multi-pass -- Vision OCR confidence check + VLM fallback.

After Apple Vision OCR produces text blocks, this module evaluates
per-block confidence and routes low-confidence blocks to a VLM OCR
fallback (bbox-level crop -> VLM text extraction).

Confidence tiers:
  - >= 0.8:  high -- use as-is
  - [0.5, 0.8): medium -- use with warning log
  - < 0.5:  low -- candidate for VLM OCR fallback
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo

if TYPE_CHECKING:
    from docforge.domain.ports import VisionLLMEngine

logger = logging.getLogger(__name__)

_CONF_HIGH = 0.8
_CONF_MEDIUM = 0.5


@dataclass(frozen=True)
class OCRQualityTier:
    """Classification result for a single OCR block."""

    tier: str           # "high", "medium", "low"
    block: TextBlock
    confidence: float


def evaluate_ocr_blocks(
    blocks: list[TextBlock],
) -> tuple[list[TextBlock], list[TextBlock]]:
    """Split OCR blocks into acceptable and low-confidence groups.

    Returns:
        (acceptable_blocks, low_confidence_blocks)
        - acceptable: confidence >= 0.5 -- used directly (medium gets warning)
        - low: confidence < 0.5 -- VLM fallback candidates
    """
    if not blocks:
        return [], []

    acceptable: list[TextBlock] = []
    low: list[TextBlock] = []

    for block in blocks:
        conf = block.confidence
        if conf >= _CONF_HIGH:
            acceptable.append(block)
        elif conf >= _CONF_MEDIUM:
            logger.warning(
                "OCR medium confidence (%.2f) for block: '%s'",
                conf,
                block.text[:40],
            )
            acceptable.append(block)
        else:
            logger.info(
                "OCR low confidence (%.2f) -> VLM fallback candidate: '%s'",
                conf,
                block.text[:40],
            )
            low.append(block)

    return acceptable, low


def fallback_ocr_via_vlm(
    low_blocks: list[TextBlock],
    vlm_engine: "VisionLLMEngine",
    page_image_bytes: bytes,
    page_width: int,
    page_height: int,
) -> list[TextBlock]:
    """Re-OCR low-confidence blocks by cropping their bbox and sending to VLM.

    For each low-confidence block:
    1. Crop the block's bbox region from the page image
    2. Send to VLM with a text-extraction prompt
    3. Replace with VLM result if non-empty; otherwise keep original

    Returns a list of replacement blocks (same length as ``low_blocks``).
    """
    if not low_blocks or not page_image_bytes:
        return list(low_blocks)

    from docforge.processing.roi_cropper import crop_region_from_image

    result: list[TextBlock] = []
    for block in low_blocks:
        try:
            cropped = crop_region_from_image(
                page_image_bytes,
                page_width,
                page_height,
                block.bbox,
                padding=10,
            )
            if not cropped:
                result.append(block)
                continue

            vlm_text = vlm_engine.describe_image(
                image_data=cropped,
                format="png",
                prompt_hint="이 영역의 텍스트를 정확하게 추출하세요. 출력: 텍스트만.",
            )
            if vlm_text and vlm_text.strip():
                replacement = TextBlock(
                    text=vlm_text.strip(),
                    bbox=block.bbox,
                    font=block.font,
                    block_type=block.block_type,
                    heading_level=block.heading_level,
                    confidence=0.85,  # VLM-corrected confidence
                    block_id=block.block_id,
                    parent_id=block.parent_id,
                )
                logger.info(
                    "VLM OCR fallback replaced block (conf %.2f -> 0.85): '%s' -> '%s'",
                    block.confidence,
                    block.text[:30],
                    vlm_text.strip()[:30],
                )
                result.append(replacement)
            else:
                result.append(block)
        except Exception:
            logger.warning(
                "VLM OCR fallback failed for block '%s'",
                block.text[:30],
                exc_info=True,
            )
            result.append(block)

    return result


__all__ = [
    "evaluate_ocr_blocks",
    "fallback_ocr_via_vlm",
]
