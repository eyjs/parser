"""Korean legal document structure recognition.

Hierarchy: pyeon(h1) > jang(h2) > jeol(h3) > gwan(h3) > jo(h4) > hang > ho > mok
"""

from __future__ import annotations

import re

from docforge.domain.enums import BlockType
from docforge.infrastructure.config import ParserConfig


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

# Numbering hierarchy for heading-level adjustment when font is heading-like.
# Higher-depth patterns get deeper heading levels to preserve parent→child structure.
_NUMBERING_DEPTH: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^[가나다라마바사아자차카타파하]\.\s+"), 2),   # 가. 나. → parent + 2
    (re.compile(r"^\(\d+\)\s*"), 2),                           # (1) (2) → parent + 2
    (re.compile(r"^[가나다라마바사아자차카타파하]\)\s*"), 3),   # 가) 나) → parent + 3
]


def classify_block(
    text: str,
    font_size: float = 0.0,
    is_bold: bool = False,
    avg_font_size: float = 0.0,
    config: ParserConfig | None = None,
) -> tuple[BlockType, int]:
    """Classify a text block into its structural type and heading level.

    Returns:
        Tuple of (BlockType, heading_level). heading_level is 0 for non-headings.
    """
    if config is None:
        config = ParserConfig()

    stripped = text.strip()
    if not stripped:
        return BlockType.TEXT, 0

    # Pattern-based heading detection (highest priority)
    for pattern, level, block_type in _HEADING_PATTERNS:
        if pattern.match(stripped):
            return block_type, level

    # Font-based heading detection (when no structural pattern matches)
    if avg_font_size > 0:
        base_level = 0
        if font_size > avg_font_size * config.heading_bold_ratio and is_bold:
            base_level = 2
        elif font_size > avg_font_size * config.heading_size_ratio:
            base_level = 3

        if base_level > 0:
            for pattern, depth_offset in _NUMBERING_DEPTH:
                if pattern.match(stripped):
                    return BlockType.HEADING, min(base_level + depth_offset, 6)
            return BlockType.HEADING, base_level

    # Clause (circled numbers)
    if _CLAUSE_PATTERN.match(stripped):
        return BlockType.CLAUSE, 0

    # Subclause (numbered or Korean letter with dot)
    for pattern in _SUBCLAUSE_PATTERNS:
        if pattern.match(stripped):
            return BlockType.SUBCLAUSE, 0

    # Item (Korean letter with closing paren)
    for pattern in _ITEM_PATTERNS:
        if pattern.match(stripped):
            return BlockType.ITEM, 0

    return BlockType.TEXT, 0
