"""Preprocessing router — orchestrates diagnosis, decision, preprocessing, and quality gate.

This module coordinates the full preprocessing pipeline:
1. Diagnose image quality
2. Decide which preprocessing to apply
3. Apply selective preprocessing
4. Run quality gate (A/B comparison)
5. Return winning OCR blocks (no redundant OCR calls)
"""

from __future__ import annotations

import logging

from docforge.domain.enums import SelectionReason
from docforge.domain.models import TextBlock
from docforge.domain.ports import ImagePreprocessor, OCREngine
from docforge.domain.value_objects import (
    ImageQualityPolicy,
    PreprocessingDecision,
    QualityGateResult,
    RawImage,
)
from docforge.processing.image_diagnostics import diagnose_image, to_grayscale
from docforge.processing.quality_gate import quality_gate

logger = logging.getLogger(__name__)


def process_scanned_page(
    image: RawImage,
    ocr_engine: OCREngine,
    preprocessor: ImagePreprocessor,
    policy: ImageQualityPolicy,
) -> tuple[list[TextBlock], PreprocessingDecision, QualityGateResult | None]:
    """Process a scanned page through the preprocessing pipeline.

    Flow:
    1. Diagnose image quality (5 metrics)
    2. Decide preprocessing based on policy
    3. If all clean: OCR once, return
    4. If preprocessing needed: apply, then A/B compare
    5. Return winning blocks from quality gate

    Args:
        image: Input image as RawImage.
        ocr_engine: OCR engine implementing OCREngine protocol.
        preprocessor: Image preprocessor implementing ImagePreprocessor protocol.
        policy: Quality policy thresholds.

    Returns:
        Tuple of (text_blocks, decision, gate_result).
        gate_result is None if preprocessing was skipped.
    """
    # Step 1: Diagnose
    gray = to_grayscale(image.data)
    report = diagnose_image(gray)

    # Step 2: Decide
    decision = PreprocessingDecision(
        apply_upscale=report.needs_upscale(policy),
        apply_deskew=report.needs_deskew(policy),
        apply_contrast=report.needs_contrast(policy),
        apply_denoise=report.needs_denoise(policy),
        apply_binarize=report.needs_binarize(policy),
        quality_report=report,
        skew_angle=report.skew_angle,
    )

    # Step 3: Clean image -> OCR once, no preprocessing
    if decision.skip_all:
        logger.debug("Image is clean, skipping preprocessing")
        blocks = ocr_engine.recognize(image)
        return blocks, decision, None

    # Step 4: Apply selective preprocessing (fallback to original on failure)
    try:
        preprocessed = preprocessor.preprocess(image, decision)
    except Exception:
        logger.warning("Preprocessing failed, falling back to original", exc_info=True)
        blocks = ocr_engine.recognize(image)
        gate = QualityGateResult(
            use_preprocessed=False,
            original_confidence=0.0,
            preprocessed_confidence=0.0,
            original_char_count=0,
            preprocessed_char_count=0,
            reason=SelectionReason.PREPROCESSING_FAILED,
            reason_detail="Preprocessing exception, original fallback",
            winning_blocks=tuple(blocks),
        )
        return blocks, decision, gate

    # Step 5: Quality gate (A/B comparison, OCR 2x, returns winning_blocks)
    gate_result = quality_gate(image, preprocessed, ocr_engine, policy)

    logger.info(
        "Quality gate: %s (%s)",
        gate_result.reason.name,
        gate_result.reason_detail,
    )

    # Step 6: Return winning blocks from gate (no re-OCR)
    blocks = list(gate_result.winning_blocks)  # type: ignore[arg-type]
    return blocks, decision, gate_result
