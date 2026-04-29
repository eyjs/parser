"""Per-page type classification: DIGITAL / SCANNED / MIXED / NOISE."""

from __future__ import annotations

import re

from docforge.domain.enums import PageType
from docforge.infrastructure.config import ParserConfig


# Precompiled TOC patterns for page-level detection
_TOC_DOT_PATTERN = re.compile(r"\.{3,}")
_TOC_ELLIPSIS_PATTERN = re.compile(r"…{2,}")
_TOC_LINE_PATTERN = re.compile(r"^.{2,50}\s*\.{3,}\s*\d{1,4}\s*$")
_TOC_SPACE_PATTERN = re.compile(r"^.{2,50}\s+\d{1,4}\s*$")

_TOC_PATTERNS = [_TOC_DOT_PATTERN, _TOC_ELLIPSIS_PATTERN, _TOC_LINE_PATTERN, _TOC_SPACE_PATTERN]


def classify_page(
    char_count: int,
    has_images: bool,
    image_area_ratio: float,
    raw_text: str,
    config: ParserConfig,
) -> PageType:
    """Classify a single page into a PageType.

    Args:
        char_count: Number of characters extracted from the page.
        has_images: Whether the page contains embedded images.
        image_area_ratio: Ratio of image area to page area.
        raw_text: Raw text extracted from the page.
        config: Parser configuration.

    Returns:
        The classified PageType.
    """
    # Empty page
    if char_count < 5 and not has_images:
        return PageType.NOISE

    # TOC page detection
    if _is_toc_page(raw_text, config.toc_threshold):
        return PageType.NOISE

    # Garbled text detection — custom font encoding that PyMuPDF can't decode
    if char_count >= config.min_chars_per_page and _is_garbled_text(raw_text):
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


def _is_garbled_text(raw_text: str) -> bool:
    """Detect text that was extracted but is unreadable (custom font encoding)."""
    stripped = raw_text.strip()
    if not stripped:
        return False

    readable_count = 0
    total_count = 0
    for ch in stripped:
        if ch.isspace():
            continue
        total_count += 1
        if (
            '가' <= ch <= '힣'  # Korean syllables
            or 'ㄱ' <= ch <= 'ㆎ'  # Korean jamo
            or 'A' <= ch <= 'Z' or 'a' <= ch <= 'z'
            or '0' <= ch <= '9'
            or ch in '.,;:!?()-/\\[]{}@#$%&*+=<>~`\'"'
            or '①' <= ch <= '⑳'  # circled numbers
            or ch in '·…―'
        ):
            readable_count += 1

    if total_count == 0:
        return False

    return readable_count / total_count < 0.3


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
