"""Alternative heading detection for OCR pages (Apple Vision font_size=0.0).

Apple Vision does not report per-block font sizes, so all ``TextBlock``
instances from OCR pages carry ``font.size == 0.0``.  The standard
heading classification in ``text_structurer.py`` relies on font size and
bold attributes, making it blind on OCR pages.

This module provides ``detect_headings_ocr`` which combines **four
complementary signals** to estimate heading probability:

1. Surya Title label (if layout detection ran)
2. BBox height ratio vs median
3. Regex pattern (Korean legal numbering: "제N조", "제N편", etc.)
4. Text length + vertical position on page

Each signal contributes a 0.0-1.0 score; the weighted sum determines
whether a block is re-classified as ``BlockType.HEADING``.
"""

from __future__ import annotations

import re
import statistics
import logging

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)

# ---- Regex patterns for Korean legal / structured numbering ----
_HEADING_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    # 제N편 -> level 1
    (re.compile(r"^제\s*\d+\s*편"), 1),
    # 제N장 -> level 2
    (re.compile(r"^제\s*\d+\s*장"), 2),
    # 제N절 -> level 3
    (re.compile(r"^제\s*\d+\s*절"), 3),
    # 제N관 -> level 3
    (re.compile(r"^제\s*\d+\s*관"), 3),
    # 제N조 (with optional title) -> level 4
    (re.compile(r"^제\s*\d+\s*조"), 4),
    # Arabic numbering: "1.", "2.", ... at start
    (re.compile(r"^\d+\.\s"), 3),
    # Korean consonant numbering: "가.", "나.", ...
    (re.compile(r"^[가-하]\.\s"), 3),
]

# Weight configuration
# Regex is the strongest standalone signal for Korean legal docs —
# patterns like "제N조/장/편/절" are definitive heading markers.
_W_SURYA = 0.3
_W_HEIGHT = 0.15
_W_REGEX = 0.35
_W_POSITION = 0.2

_HEADING_THRESHOLD = 0.4


def _signal_surya_title(
    bbox: BBox,
    layout_blocks: list[LayoutBlock] | None,
    iou_threshold: float = 0.3,
) -> float:
    """Return 1.0 if any layout block labelled 'Title' overlaps the bbox."""
    if not layout_blocks:
        return 0.0
    for lb in layout_blocks:
        if lb.label == "Title" and bbox.iou(lb.bbox) >= iou_threshold:
            return 1.0
    return 0.0


def _signal_height_ratio(
    bbox: BBox,
    median_height: float,
) -> float:
    """Score based on how much taller this block is vs the page median."""
    if median_height <= 0:
        return 0.0
    ratio = bbox.height / median_height
    if ratio >= 1.5:
        return 1.0
    if ratio >= 1.2:
        return (ratio - 1.0) / 0.5  # linear scale 1.0->0.0, 1.5->1.0
    return 0.0


def _signal_regex(text: str) -> tuple[float, int]:
    """Return (score, heading_level) from regex pattern matching."""
    stripped = text.strip()
    for pattern, level in _HEADING_PATTERNS:
        if pattern.search(stripped):
            return 1.0, level
    return 0.0, 0


def _signal_position_length(
    text: str,
    bbox: BBox,
    page_height: float,
) -> float:
    """Score based on text length and vertical position on the page."""
    score = 0.0
    text_len = len(text.strip())
    if text_len == 0:
        return 0.0

    # Short text bonus
    if text_len < 30:
        score += 0.5
    elif text_len < 50:
        score += 0.25

    # Upper-page bonus (top 30%)
    if page_height > 0 and bbox.y0 < page_height * 0.3:
        score += 0.5

    return min(score, 1.0)


def detect_headings_ocr(
    blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock] | None,
    page_height: float,
) -> list[TextBlock]:
    """Re-classify heading candidates on OCR pages (font_size=0.0 blocks).

    Only processes blocks where ``font.size == 0.0``.  Blocks with a
    real font size are returned unchanged (they use the standard
    ``text_structurer`` path).

    Returns a new list of ``TextBlock`` instances -- originals are never
    mutated.
    """
    if not blocks:
        return []

    # Compute median bbox height across all blocks for relative comparison
    heights = [b.bbox.height for b in blocks if b.bbox.height > 0]
    median_h = statistics.median(heights) if heights else 0.0

    result: list[TextBlock] = []
    for block in blocks:
        # Skip blocks that already have real font size info
        if block.font.size > 0.0:
            result.append(block)
            continue

        # Compute four signals
        s_surya = _signal_surya_title(block.bbox, layout_blocks)
        s_height = _signal_height_ratio(block.bbox, median_h)
        s_regex, regex_level = _signal_regex(block.text)
        s_position = _signal_position_length(block.text, block.bbox, page_height)

        score = (
            s_surya * _W_SURYA
            + s_height * _W_HEIGHT
            + s_regex * _W_REGEX
            + s_position * _W_POSITION
        )

        if score > _HEADING_THRESHOLD:
            # Determine heading level
            if regex_level > 0:
                level = regex_level
            elif s_surya > 0:
                level = 2  # Surya Title without regex -> default level 2
            else:
                level = 2  # generic heading

            logger.info(
                "heading_detector: '%s' -> HEADING (level=%d, score=%.2f, "
                "surya=%.1f, height=%.1f, regex=%.1f, pos=%.1f)",
                block.text[:40],
                level,
                score,
                s_surya,
                s_height,
                s_regex,
                s_position,
            )
            result.append(
                TextBlock(
                    text=block.text,
                    bbox=block.bbox,
                    font=block.font,
                    block_type=BlockType.HEADING,
                    heading_level=level,
                    confidence=block.confidence,
                    block_id=block.block_id,
                    parent_id=block.parent_id,
                ),
            )
        else:
            result.append(block)

    return result


__all__ = ["detect_headings_ocr"]
