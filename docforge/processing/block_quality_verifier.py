"""Block-level Quality Verifier -- NormalizedBlock quality scoring.

Responsibility split:
  - confidence_scorer.py  -> page-level composite confidence
  - block_quality_verifier.py -> block-level quality (this module)

Uses text_quality_utils.garbled_ratio() as single source -- no
duplicate garbled-text logic.
"""

from __future__ import annotations

import logging

from docforge.domain.enums import BlockType
from docforge.domain.models import NormalizedBlock
from docforge.domain.value_objects import BlockQualityResult
from docforge.processing.text_quality_utils import garbled_ratio

logger = logging.getLogger(__name__)

# BlockType-specific score thresholds (requirement.md)
_TYPE_THRESHOLDS: dict[BlockType, float] = {
    BlockType.TABLE: 0.75,
    BlockType.TEXT: 0.60,
    BlockType.HEADING: 0.60,
    BlockType.CLAUSE: 0.60,
    BlockType.SUBCLAUSE: 0.60,
    BlockType.ITEM: 0.60,
    BlockType.FOOTNOTE: 0.60,
    BlockType.FIGURE: 0.50,
    BlockType.CHART: 0.50,
    BlockType.CAPTION: 0.60,
    BlockType.UNKNOWN: 0.80,
}

_DEFAULT_THRESHOLD = 0.60
_CONFIDENCE_LOW = 0.6
_GARBLED_SEVERE = 0.50
_GARBLED_MODERATE = 0.20
_MIN_TEXT_LENGTH = 5

# Block types that represent image regions
_IMAGE_BLOCK_TYPES = frozenset({BlockType.FIGURE, BlockType.CHART})


class BlockQualityVerifier:
    """Score individual NormalizedBlocks and identify retry candidates."""

    def score(
        self,
        block: NormalizedBlock,
        threshold_override: float | None = None,
    ) -> BlockQualityResult:
        """Compute quality score for a single block.

        Args:
            block: The block to evaluate.
            threshold_override: If provided, overrides the BlockType-based
                threshold (typically from PageStrategy.block_quality_threshold).

        Returns:
            Frozen BlockQualityResult.
        """
        text = block.text or ""
        g_ratio = garbled_ratio(text)
        threshold = threshold_override or _TYPE_THRESHOLDS.get(
            block.block_type, _DEFAULT_THRESHOLD,
        )

        # Determine recommended_fallback
        recommended_fallback = self._recommend_fallback(
            text, g_ratio, block.block_type,
        )

        # Is garbled?
        is_garbled = g_ratio > _GARBLED_MODERATE

        # Compute composite score (0.0 -- 1.0)
        quality_score = block.confidence * (1.0 - g_ratio)
        quality_score = max(0.0, min(1.0, quality_score))

        # Needs retry?
        needs_retry = (
            quality_score < threshold
            or block.confidence < _CONFIDENCE_LOW
            or is_garbled
        )

        # Only recommend fallback when retry is actually needed
        effective_fallback = recommended_fallback if needs_retry else "none"

        return BlockQualityResult(
            score=quality_score,
            is_garbled=is_garbled,
            garbled_ratio=g_ratio,
            needs_retry=needs_retry,
            recommended_fallback=effective_fallback,
        )

    def score_blocks(
        self,
        blocks: list[NormalizedBlock],
        threshold_override: float | None = None,
    ) -> list[BlockQualityResult]:
        """Score a list of blocks in batch."""
        return [self.score(b, threshold_override) for b in blocks]

    def filter_retry_candidates(
        self,
        blocks: list[NormalizedBlock],
        results: list[BlockQualityResult],
    ) -> list[tuple[NormalizedBlock, BlockQualityResult]]:
        """Return (block, result) pairs where needs_retry is True."""
        return [
            (block, result)
            for block, result in zip(blocks, results)
            if result.needs_retry
        ]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _recommend_fallback(
        text: str, g_ratio: float, block_type: BlockType,
    ) -> str:
        """Decide which fallback method to recommend.

        Decision tree (from requirement.md):
          - garbled_ratio > 0.50 OR text < 5 chars  -> "vlm"
          - garbled_ratio > 0.20                     -> "ocr"
          - no text + image block type               -> "vlm"
          - otherwise                                -> "none"
        """
        stripped = text.strip()
        if g_ratio > _GARBLED_SEVERE or len(stripped) < _MIN_TEXT_LENGTH:
            return "vlm"
        if g_ratio > _GARBLED_MODERATE:
            return "ocr"
        if not stripped and block_type in _IMAGE_BLOCK_TYPES:
            return "vlm"
        return "none"


__all__ = ["BlockQualityVerifier"]
