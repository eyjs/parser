"""Caption matcher (Phase B-2).

Pairs each :class:`ParsedImage` with the most likely caption text block
on the same page, using:

  * Korean / English caption pattern bonus (``그림 N``, ``표 N``, ``Figure N``).
  * Layout-detector ``Caption`` label bonus (when available).
  * Vertical proximity score (closer = higher).

The result is a new list of :class:`ParsedImage` with their ``caption``
field populated where a candidate scored above zero. Inputs are never
mutated (frozen dataclasses).
"""

from __future__ import annotations

import re
from dataclasses import replace

from docforge.domain.models import ParsedImage, TextBlock

CAPTION_PATTERN = re.compile(
    r"[\[<]?(그림|표|Fig|Figure|Table)\s*\d+[>\]]?",
    re.IGNORECASE,
)
PROXIMITY_PT: float = 100.0
_PATTERN_BONUS = 10.0
_LAYOUT_LABEL_BONUS = 20.0
_MIN_SCORE_TO_ATTACH = 1.0


def match_captions(
    images: list[ParsedImage],
    text_blocks: list[TextBlock],
    layout_label_map: dict[str, str] | None = None,
) -> list[ParsedImage]:
    """Return new ``ParsedImage`` list with ``caption`` populated.

    Args:
        images: Images extracted for the current page.
        text_blocks: Same-page text blocks.
        layout_label_map: Optional ``block_id -> layout_label`` map from
            :func:`docforge.processing.layout_router.build_layout_label_map`.
            Boosts blocks whose layout label is ``"Caption"``.
    """
    if not images:
        return []
    label_map = layout_label_map or {}

    out: list[ParsedImage] = []
    for image in images:
        candidates = _score_candidates(image, text_blocks, label_map)
        if not candidates:
            out.append(image)
            continue
        best_block, best_score = max(candidates, key=lambda x: x[1])
        if best_score < _MIN_SCORE_TO_ATTACH:
            out.append(image)
            continue
        out.append(replace(image, caption=best_block.text.strip()))
    return out


# -- internals ------------------------------------------------------------


def _score_candidates(
    image: ParsedImage,
    blocks: list[TextBlock],
    label_map: dict[str, str],
) -> list[tuple[TextBlock, float]]:
    out: list[tuple[TextBlock, float]] = []
    for block in blocks:
        gap = _vertical_gap(image, block)
        if gap > PROXIMITY_PT:
            continue
        score = (PROXIMITY_PT - gap) / PROXIMITY_PT
        if CAPTION_PATTERN.search(block.text):
            score += _PATTERN_BONUS
        if block.block_id and label_map.get(block.block_id) == "Caption":
            score += _LAYOUT_LABEL_BONUS
        out.append((block, score))
    return out


def _vertical_gap(image: ParsedImage, block: TextBlock) -> float:
    """Vertical pt distance — 0 when the block overlaps the image."""
    img = image.bbox
    blk = block.bbox
    if blk.y0 >= img.y1:
        return blk.y0 - img.y1  # block below
    if img.y0 >= blk.y1:
        return img.y0 - blk.y1  # block above
    return 0.0  # overlap


__all__ = ["CAPTION_PATTERN", "PROXIMITY_PT", "match_captions"]
