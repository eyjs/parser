"""Per-page type classification: DIGITAL / SCANNED / MIXED / NOISE / COVER / TOC.

Phase B-3 split COVER and TOC out of the legacy NOISE bucket. The legacy
``classify_page`` entry point keeps its signature (no blocks argument) so
all existing tests/callers stay green; a new ``classify_page_with_blocks``
adds the cover/TOC heuristics that need the per-block layout/font info.
"""

from __future__ import annotations

import re
from typing import Iterable

from docforge.domain.enums import PageType
from docforge.domain.models import TextBlock
from docforge.infrastructure.config import ParserConfig
from docforge.processing.text_quality_utils import is_garbled_text as _is_garbled_text
from docforge.processing.text_quality_utils import is_pua_garbled as _is_pua_garbled


# Precompiled TOC patterns for page-level detection
_TOC_DOT_PATTERN = re.compile(r"\.{3,}")
_TOC_ELLIPSIS_PATTERN = re.compile(r"…{2,}")
_TOC_LINE_PATTERN = re.compile(r"^.{2,50}\s*\.{3,}\s*\d{1,4}\s*$")
_TOC_SPACE_PATTERN = re.compile(r"^.{2,50}\s+\d{1,4}\s*$")

_TOC_PATTERNS = [_TOC_DOT_PATTERN, _TOC_ELLIPSIS_PATTERN, _TOC_LINE_PATTERN, _TOC_SPACE_PATTERN]

# TOC keyword markers (Korean/English)
_TOC_KEYWORDS = ("목 차", "목차", "차 례", "차례", "Contents", "CONTENTS")

# Cover heuristic thresholds
_COVER_MAX_PAGE_IDX = 2          # 0-based: pages 1..3
_COVER_MAX_BLOCKS = 10
_COVER_MIN_AVG_FONT = 14.0
_COVER_MIN_CENTER_RATIO = 0.5

# TOC heuristic thresholds
_TOC_MIN_SHORT_RATIO = 0.6
_TOC_MIN_BLOCKS = 5
_TOC_SHORT_LEN = 30
_TOC_RIGHT_NUM_RE = re.compile(r"\d{1,4}\s*$")


def classify_page(
    char_count: int,
    has_images: bool,
    image_area_ratio: float,
    raw_text: str,
    config: ParserConfig,
) -> PageType:
    """Classify a single page into a PageType (legacy signature).

    Kept for backward compatibility — does not detect COVER/TOC because
    those heuristics require block-level info. Callers wanting the
    expanded classification should use :func:`classify_page_with_blocks`.
    """
    # Empty page
    if char_count < 5 and not has_images:
        return PageType.NOISE

    # TOC page detection (legacy: leader-dot heuristic only)
    if _is_toc_page(raw_text, config.toc_threshold):
        return PageType.NOISE

    # Garbled text detection. Two distinct cases:
    #   (a) PUA / undecodable glyphs — PyMuPDF could not decode the embedded
    #       font, so the text layer is genuinely unusable. Always needs OCR.
    #   (b) Korean "fragmentation" heuristic firing on text that WAS cleanly
    #       decoded. This is unreliable on dense tabular Korean (coverage
    #       tables, parenthesised insurance terms) and false-positives there.
    #       Only trust it as SCANNED when the page lacks a substantial clean
    #       text layer; a text-rich page (>= garble_text_trust_chars cleanly
    #       decoded chars) is digital regardless of the fuzzy score.
    if char_count >= config.min_chars_per_page and _is_pua_garbled(raw_text):
        return PageType.SCANNED
    if (
        config.min_chars_per_page <= char_count < config.garble_text_trust_chars
        and _is_garbled_text(raw_text)
    ):
        return PageType.SCANNED

    # Scanned page (very little text but images present)
    if char_count < config.min_chars_per_page and has_images:
        return PageType.SCANNED

    # Scanned page (very little text, no images but renderable)
    if char_count < config.min_chars_per_page:
        return PageType.SCANNED

    # Mixed page (enough text but significant image area)
    if char_count >= config.min_chars_per_page and image_area_ratio > config.image_area_table_hint:
        return PageType.MIXED

    # Digital page (enough text, minimal images)
    if char_count >= config.min_chars_per_page:
        return PageType.DIGITAL

    # Fallback: too little text, no images
    return PageType.NOISE


def classify_page_with_blocks(
    page_idx: int,
    char_count: int,
    has_images: bool,
    image_area_ratio: float,
    raw_text: str,
    blocks: Iterable[TextBlock],
    page_width: float,
    page_height: float,
    config: ParserConfig,
) -> PageType:
    """Classify with cover/TOC awareness.

    Decision order:
      1. COVER (page_idx <= 2 + few large-font blocks)
      2. TOC (keywords or short-row + right-aligned numbers)
      3. Fall back to legacy NOISE/SCANNED/MIXED/DIGITAL pipeline.
    """
    blocks_list = list(blocks)

    if is_cover_page(page_idx, blocks_list, page_width, page_height):
        return PageType.COVER

    if is_toc_page(blocks_list, raw_text):
        return PageType.TOC

    return classify_page(
        char_count=char_count,
        has_images=has_images,
        image_area_ratio=image_area_ratio,
        raw_text=raw_text,
        config=config,
    )


def is_cover_page(
    page_idx: int,
    blocks: list[TextBlock],
    page_width: float,
    page_height: float,
) -> bool:
    """Return True when the page looks like a document cover.

    Heuristic — must satisfy ALL:
      * page_idx in [0, 1, 2] (1-based pages 1..3)
      * len(blocks) <= 10
      * average font size >= 14pt
      * >= 50% of blocks are roughly center-aligned horizontally
    """
    if page_idx > _COVER_MAX_PAGE_IDX:
        return False
    if not blocks or len(blocks) > _COVER_MAX_BLOCKS:
        return False

    avg_font = sum(b.font.size for b in blocks) / len(blocks)
    if avg_font < _COVER_MIN_AVG_FONT:
        return False

    if page_width <= 0:
        return False

    page_center = page_width / 2.0
    # Use 25% of page width as the "centered" tolerance window
    tolerance = page_width * 0.25
    centered = sum(
        1 for b in blocks if abs(b.bbox.center_x - page_center) <= tolerance
    )
    return centered / len(blocks) >= _COVER_MIN_CENTER_RATIO


def _has_toc_keyword(raw_text: str) -> bool:
    """Check for TOC keywords that indicate a genuine table-of-contents page.

    Navigation links like "☞ 목차로 돌아가기" appear on most pages —
    strip them before checking so they don't cause false positives.
    """
    cleaned = re.sub(r"목차로\s*돌아가기", "", raw_text)
    return any(keyword in cleaned for keyword in _TOC_KEYWORDS)


def is_toc_page(blocks: list[TextBlock], raw_text: str) -> bool:
    """Return True when the page looks like a table of contents.

    Triggers if any keyword marker is present OR the structural
    short-row + right-aligned-number heuristic fires.
    """
    if _has_toc_keyword(raw_text):
        return True

    if not blocks or len(blocks) < _TOC_MIN_BLOCKS:
        return False

    short_rows = sum(
        1 for b in blocks if len(b.text.strip()) < _TOC_SHORT_LEN
    )
    if short_rows / len(blocks) < _TOC_MIN_SHORT_RATIO:
        return False

    right_numbered = sum(
        1 for b in blocks if _TOC_RIGHT_NUM_RE.search(b.text.strip())
    )
    return right_numbered / len(blocks) >= 0.4



def _is_toc_page(raw_text: str, threshold: float) -> bool:
    """Check if more than threshold fraction of lines are TOC entries."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    if not lines:
        return False

    toc_count = 0
    for line in lines:
        for pattern in _TOC_PATTERNS:
            if pattern.search(line):
                toc_count += 1
                break

    return toc_count / len(lines) > threshold
