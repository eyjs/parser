"""Korean legal document profile.

Hierarchy: pyeon(h1) > jang(h2) > jeol(h3) > gwan(h3) > jo(h4) > hang > ho > mok

This module owns every regex previously hard-coded in
``text_structurer.py``. The structurer now delegates to this profile
through the ``DomainProfile`` Protocol.
"""

from __future__ import annotations

import re

from docforge.domain.enums import BlockType


# Structure patterns ordered by priority (highest first)
_HEADING_PATTERNS: list[tuple[re.Pattern[str], int, BlockType]] = [
    (re.compile(r"^제\s*\d+\s*편\b"), 1, BlockType.HEADING),
    (re.compile(r"^제\s*\d+\s*장\b"), 2, BlockType.HEADING),
    (re.compile(r"^제\s*\d+\s*절\b"), 3, BlockType.HEADING),
    (re.compile(r"^제\s*\d+\s*관\b"), 3, BlockType.HEADING),
    (re.compile(r"^제\s*\d+조(?:의\d+)?(?:\s*[\(（].*?[\)）])?\s"), 4, BlockType.HEADING),
    (re.compile(r"^제\s*\d+조(?:의\d+)?(?:\s*[\(（].*?[\)）])?\s*$"), 4, BlockType.HEADING),
]

# Clause pattern (circled numbers)
_CLAUSE_PATTERN = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*")

# Subclause patterns
_SUBCLAUSE_PATTERNS = [
    re.compile(r"^\d+\.\s+"),
    re.compile(r"^[가나다라마바사아자차카타파하]\.\s+"),
]

# Item patterns (mok)
_ITEM_PATTERNS = [
    re.compile(r"^[가나다라마바사아자차카타파하]\)\s*"),
    re.compile(r"^[ⅰ-ⅹ]\)\s*"),
]

# Decimal subsection patterns (e.g. "2.1", "3.2.1") — treated as sub-headings
_DECIMAL_SUBSECTION_PATTERN = re.compile(r"^\d+\.\d+(?:\.\d+)?\s+")

# Numbering hierarchy for heading-level adjustment when font is heading-like.
_NUMBERING_DEPTH: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^\d+\.\d+\.\d+\s+"), 3),
    (re.compile(r"^\d+\.\d+\s+"), 1),
    (re.compile(r"^[가나다라마바사아자차카타파하]\.\s+"), 2),
    (re.compile(r"^\(\d+\)\s*"), 2),
]

# Any recognized numbering prefix — used to gate font-based heading promotion.
_ANY_NUMBERING_PREFIX = re.compile(
    r"^("
    r"\d+\.\d+(?:\.\d+)?\s"
    r"|\d+\.\s"
    r"|[가나다라마바사아자차카타파하]\.\s"
    r"|\(\d+\)\s"
    r")"
)

_MAX_HEADING_LENGTH = 80


class KoreanLegalProfile:
    """Profile for Korean legal/insurance documents."""

    def name(self) -> str:
        return "korean_legal"

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

        if _CLAUSE_PATTERN.match(stripped):
            return BlockType.CLAUSE, 0
        for pattern in _ITEM_PATTERNS:
            if pattern.match(stripped):
                return BlockType.ITEM, 0

        if _DECIMAL_SUBSECTION_PATTERN.match(stripped) and len(stripped) <= _MAX_HEADING_LENGTH:
            prefix = stripped.split()[0]
            depth = prefix.count(".")
            return BlockType.HEADING, min(3 + depth, 6)

        if avg_font_size > 0:
            base_level = 0
            if font_size > avg_font_size * heading_bold_ratio and is_bold:
                base_level = 2
            elif font_size > avg_font_size * heading_size_ratio:
                base_level = 3

            if base_level > 0:
                for pattern, depth_offset in _NUMBERING_DEPTH:
                    if pattern.match(stripped):
                        return BlockType.HEADING, min(base_level + depth_offset, 6)
                if _ANY_NUMBERING_PREFIX.match(stripped):
                    return BlockType.HEADING, base_level
                if is_bold and len(stripped) <= _MAX_HEADING_LENGTH:
                    return BlockType.HEADING, base_level

        for pattern in _SUBCLAUSE_PATTERNS:
            if pattern.match(stripped):
                return BlockType.SUBCLAUSE, 0

        return BlockType.TEXT, 0
