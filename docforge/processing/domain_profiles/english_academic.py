"""English academic document profile (stub).

Recognises the common structural cues for academic English papers and
reports: ``Chapter``, ``Section``, ``Figure``, ``Table`` headings plus
numeric section numbering (``1.``, ``1.1``, ``1.1.1``).

Intentionally minimal — extension points (Appendix, References, etc.)
should be added as future iterations validate the heuristics on real
corpora.
"""

from __future__ import annotations

import re

from docforge.domain.enums import BlockType


# Heading patterns ordered by priority (highest first).
_HEADING_PATTERNS: list[tuple[re.Pattern[str], int, BlockType]] = [
    (re.compile(r"^part\s+[ivxlcdm\d]+\b", re.IGNORECASE), 1, BlockType.HEADING),
    (re.compile(r"^chapter\s+\d+\b", re.IGNORECASE), 1, BlockType.HEADING),
    (re.compile(r"^section\s+\d+\b", re.IGNORECASE), 2, BlockType.HEADING),
    # Numeric section numbering: "1.", "1.1", "1.1.1" — depth controls level.
    (re.compile(r"^\d+\.\d+\.\d+\s+\S"), 4, BlockType.HEADING),
    (re.compile(r"^\d+\.\d+\s+\S"), 3, BlockType.HEADING),
    (re.compile(r"^\d+\.\s+\S"), 2, BlockType.HEADING),
]

# Figure / Table captions become item-level annotations.
_CAPTION_PATTERNS = [
    re.compile(r"^figure\s+\d+", re.IGNORECASE),
    re.compile(r"^table\s+\d+", re.IGNORECASE),
]

# Bullet / enumerated list items.
_ITEM_PATTERNS = [
    re.compile(r"^[\-\*•]\s+"),
    re.compile(r"^\([a-z]\)\s+", re.IGNORECASE),
    re.compile(r"^[a-z]\)\s+", re.IGNORECASE),
]

_MAX_HEADING_LENGTH = 120


class EnglishAcademicProfile:
    """Profile for English academic / technical documents (stub)."""

    def name(self) -> str:
        return "english_academic"

    def classify(
        self,
        text: str,
        font_size: float,
        is_bold: bool,
        avg_font_size: float,
        heading_bold_ratio: float,
        heading_size_ratio: float,
    ) -> tuple[BlockType, int]:
        stripped = text.strip()
        if not stripped:
            return BlockType.TEXT, 0

        for pattern, level, block_type in _HEADING_PATTERNS:
            if pattern.match(stripped):
                return block_type, level

        for pattern in _CAPTION_PATTERNS:
            if pattern.match(stripped):
                return BlockType.ITEM, 0

        for pattern in _ITEM_PATTERNS:
            if pattern.match(stripped):
                return BlockType.ITEM, 0

        if avg_font_size > 0:
            if (
                font_size > avg_font_size * heading_bold_ratio
                and is_bold
                and len(stripped) <= _MAX_HEADING_LENGTH
            ):
                return BlockType.HEADING, 2
            if font_size > avg_font_size * heading_size_ratio:
                return BlockType.HEADING, 3

        return BlockType.TEXT, 0
