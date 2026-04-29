"""Quality gate — A/B comparison between original and preprocessed OCR results.

Returns winning_blocks to prevent redundant OCR calls.
"""

from __future__ import annotations

import logging

from docforge.domain.enums import SelectionReason
from docforge.domain.models import TextBlock
from docforge.domain.ports import OCREngine
from docforge.domain.value_objects import (
    ImageQualityPolicy,
    QualityGateResult,
    RawImage,
)

logger = logging.getLogger(__name__)


def quality_gate(
    original: RawImage,
    preprocessed: RawImage,
    ocr_engine: OCREngine,
    policy: ImageQualityPolicy,
) -> QualityGateResult:
    """Compare OCR results from original and preprocessed images.

    Runs OCR on both images and selects the better result.
    Returns QualityGateResult with winning_blocks (no re-OCR needed).

    Args:
        original: Original image.
        preprocessed: Preprocessed image.
        ocr_engine: OCR engine implementing OCREngine protocol.
        policy: Quality policy with thresholds.

    Returns:
        QualityGateResult with comparison metrics and winning blocks.
    """
    orig_blocks = ocr_engine.recognize(original)
    prep_blocks = ocr_engine.recognize(preprocessed)

    orig_conf = _avg_confidence(orig_blocks)
    prep_conf = _avg_confidence(prep_blocks)
    orig_chars = _total_chars(orig_blocks)
    prep_chars = _total_chars(prep_blocks)

    # Case 0: Original has no text, preprocessed recovered some
    if orig_chars == 0 and prep_chars > 0:
        return QualityGateResult(
            use_preprocessed=True,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_RESCUED_EMPTY,
            reason_detail=f"Original 0 chars, preprocessed recovered {prep_chars}",
            winning_blocks=tuple(prep_blocks),
        )

    # Case 1: Preprocessing caused character loss
    if prep_chars < orig_chars * policy.char_loss_threshold:
        return QualityGateResult(
            use_preprocessed=False,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_CHAR_LOSS,
            reason_detail=(
                f"Prep rejected: char loss "
                f"({prep_chars} < {orig_chars}*{policy.char_loss_threshold})"
            ),
            winning_blocks=tuple(orig_blocks),
        )

    # Case 2: Preprocessing significantly improved confidence
    if prep_conf > orig_conf + policy.confidence_margin:
        return QualityGateResult(
            use_preprocessed=True,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_CONFIDENCE_UP,
            reason_detail=(
                f"Prep accepted: confidence {orig_conf:.3f} -> {prep_conf:.3f}"
            ),
            winning_blocks=tuple(prep_blocks),
        )

    # Case 3: Similar confidence but significantly more characters
    if (
        prep_chars > orig_chars * policy.char_gain_threshold
        and prep_conf >= orig_conf
    ):
        return QualityGateResult(
            use_preprocessed=True,
            original_confidence=orig_conf,
            preprocessed_confidence=prep_conf,
            original_char_count=orig_chars,
            preprocessed_char_count=prep_chars,
            reason=SelectionReason.PREP_CHAR_GAIN,
            reason_detail=f"Prep accepted: char gain ({orig_chars} -> {prep_chars})",
            winning_blocks=tuple(prep_blocks),
        )

    # Default: keep original (original-first principle)
    return QualityGateResult(
        use_preprocessed=False,
        original_confidence=orig_conf,
        preprocessed_confidence=prep_conf,
        original_char_count=orig_chars,
        preprocessed_char_count=prep_chars,
        reason=SelectionReason.ORIGINAL_DEFAULT,
        reason_detail="Original kept (no significant improvement)",
        winning_blocks=tuple(orig_blocks),
    )


def _avg_confidence(blocks: list[TextBlock]) -> float:
    """Calculate average confidence across blocks."""
    if not blocks:
        return 0.0
    return sum(b.confidence for b in blocks) / len(blocks)


def _total_chars(blocks: list[TextBlock]) -> int:
    """Count total characters across blocks."""
    return sum(len(b.text) for b in blocks)
