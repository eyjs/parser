"""Physical line break to logical paragraph merger.

PDF wraps text at page width, splitting sentences across lines. This module
merges them back into logical paragraphs for better RAG chunking.
"""

from __future__ import annotations

import re

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.text_structurer import classify_block


def merge_lines(
    blocks: list[TextBlock],
    avg_font_size: float,
    avg_line_gap: float,
    config: ParserConfig,
) -> list[TextBlock]:
    """Merge consecutive text blocks into logical paragraphs.

    Args:
        blocks: Text blocks sorted by y-coordinate (reading order).
        avg_font_size: Document-average font size.
        avg_line_gap: Average vertical gap between consecutive lines.
        config: Parser configuration.

    Returns:
        Merged text blocks.
    """
    if not blocks:
        return []

    result: list[TextBlock] = []
    current_texts: list[str] = [blocks[0].text.strip()]
    current_bbox = blocks[0].bbox
    current_font = blocks[0].font
    current_type = blocks[0].block_type
    current_level = blocks[0].heading_level

    for i in range(1, len(blocks)):
        prev_block = blocks[i - 1]
        curr_block = blocks[i]

        if _should_split(prev_block, curr_block, avg_font_size, avg_line_gap, config):
            # Flush the accumulated paragraph
            merged_text = _join_texts(current_texts)
            if merged_text:
                result.append(TextBlock(
                    text=merged_text,
                    bbox=BBox(
                        x0=current_bbox.x0,
                        y0=current_bbox.y0,
                        x1=max(current_bbox.x1, prev_block.bbox.x1),
                        y1=prev_block.bbox.y1,
                    ),
                    font=current_font,
                    block_type=current_type,
                    heading_level=current_level,
                ))

            # Start new paragraph
            current_texts = [curr_block.text.strip()]
            current_bbox = curr_block.bbox
            current_font = curr_block.font
            current_type = curr_block.block_type
            current_level = curr_block.heading_level
        else:
            # Merge into current paragraph
            current_texts.append(curr_block.text.strip())

    # Flush the last paragraph
    merged_text = _join_texts(current_texts)
    if merged_text:
        last_block = blocks[-1]
        result.append(TextBlock(
            text=merged_text,
            bbox=BBox(
                x0=current_bbox.x0,
                y0=current_bbox.y0,
                x1=max(current_bbox.x1, last_block.bbox.x1),
                y1=last_block.bbox.y1,
            ),
            font=current_font,
            block_type=current_type,
            heading_level=current_level,
        ))

    return result


# Regex for Korean syllable range (U+AC00..U+D7A3)
_HANGUL_SYLLABLE_RE = re.compile(r"[가-힣]")
# Sentence terminators
_SENTENCE_END_RE = re.compile(r"[.。?!]\s*$")
# Open bracket/paren at end of line — continuation expected
_OPEN_BRACKET_RE = re.compile(r"[\(（\[「『【]\s*$")
# Close bracket/paren at start of line — continuation of previous
_CLOSE_BRACKET_RE = re.compile(r"^\s*[\)）\]」』】]")
# Amount/date pattern at end of line (e.g., "100,000원", "2024.01.01")
_AMOUNT_DATE_END_RE = re.compile(r"(?:\d[\d,.]+\s*원|\d{4}[./-]\d{1,2}[./-]\d{1,2})\s*$")
# Dash continuation (e.g., "보험금 -" or "- 해당 없음")
_DASH_CONTINUATION_RE = re.compile(r"[-–—]\s*$")
# Line ending with a comma — strong continuation signal
_COMMA_END_RE = re.compile(r",\s*$")


def _join_texts(texts: list[str]) -> str:
    """Join text fragments with smart spacing for Korean/English mixed text.

    Rules:
    1. Korean-Korean without sentence terminator: join without space (mid-word break)
    2. Korean-Korean with sentence terminator: join with space
    3. English-English or English-Korean: join with space
    4. Open bracket at end: join without space to close bracket
    5. Comma at end: join with space (list continuation)
    """
    if not texts:
        return ""

    parts = [t for t in texts if t]
    if not parts:
        return ""

    result = parts[0]
    for i in range(1, len(parts)):
        prev = result
        curr = parts[i]
        if not prev or not curr:
            result = result + curr
            continue

        prev_ends_hangul = bool(_HANGUL_SYLLABLE_RE.match(prev[-1]))
        curr_starts_hangul = bool(_HANGUL_SYLLABLE_RE.match(curr[0]))
        prev_ends_sentence = bool(_SENTENCE_END_RE.search(prev))

        # Bracket continuation: join without extra space
        if _OPEN_BRACKET_RE.search(prev) or _CLOSE_BRACKET_RE.match(curr):
            result = result + curr
        elif prev_ends_hangul and curr_starts_hangul and not prev_ends_sentence:
            # Mid-word Korean line break: join without space
            result = result + curr
        else:
            result = result + " " + curr

    return result


def _should_split(
    prev: TextBlock,
    curr: TextBlock,
    avg_font_size: float,
    avg_line_gap: float,
    config: ParserConfig,
) -> bool:
    """Determine if a split should happen between prev and curr blocks.

    Priority:
    1. Strong merge signals (brackets, comma continuation) -> merge
    2. Structure patterns (headings, clauses) -> split
    3. Layout signals (gap, indent, font change) -> split
    4. Linguistic merge signals (postpositions, conjunctions) -> merge
    5. Default: split (conservative)
    """
    curr_text = curr.text.strip()
    prev_text = prev.text.strip()

    if not curr_text or not prev_text:
        return True

    # --- Strong merge conditions (highest priority) ---

    # Previous line has open bracket without matching close -> continuation
    if _OPEN_BRACKET_RE.search(prev_text):
        return False

    # Current line starts with close bracket -> continuation
    if _CLOSE_BRACKET_RE.match(curr_text):
        return False

    # Previous line ends with comma -> list/enumeration continuation
    if _COMMA_END_RE.search(prev_text):
        return False

    # Previous line ends with dash -> continuation
    if _DASH_CONTINUATION_RE.search(prev_text):
        return False

    # --- Split conditions (any match -> new block) ---

    # Previous block is a heading — next block is always separate
    prev_type, _ = classify_block(prev_text, prev.font.size, prev.font.is_bold, avg_font_size)
    if prev_type == BlockType.HEADING:
        return True

    # Structure pattern detected in next line
    block_type, level = classify_block(curr_text, curr.font.size, curr.font.is_bold, avg_font_size)
    if block_type == BlockType.HEADING:
        return True
    if block_type == BlockType.CLAUSE:
        return True
    if block_type in (BlockType.SUBCLAUSE, BlockType.ITEM):
        return True

    # Large vertical gap
    if avg_line_gap > 0:
        gap = curr.bbox.y0 - prev.bbox.y1
        if gap > avg_line_gap * config.line_gap_multiplier:
            return True

    # Significant indent change
    indent_diff = abs(curr.bbox.x0 - prev.bbox.x0)
    if indent_diff > config.indent_tolerance:
        return True

    # Font size/style change (heading -> body transition)
    if abs(curr.font.size - prev.font.size) > 1.0:
        return True
    if curr.font.is_bold != prev.font.is_bold:
        return True

    # --- Merge conditions (any match -> same paragraph) ---

    # Next line starts with a Korean postposition (indicates continuation)
    for pp in config.korean_postpositions:
        if curr_text.startswith(pp):
            return False

    # Next line starts with a conjunction
    for conj in config.korean_conjunctions:
        if curr_text.startswith(conj):
            return False

    # Previous line ends with amount/date -> likely followed by description
    if _AMOUNT_DATE_END_RE.search(prev_text):
        return False

    # Previous line doesn't end with sentence terminator
    if not prev_text.endswith((".", "。", "?", "!", ":", ";")):
        return False

    # Previous line ends with continuation suffix
    for suffix in config.korean_continuation_suffixes:
        if prev_text.endswith(suffix):
            return False

    # Same font properties -> likely continuation
    if (abs(curr.font.size - prev.font.size) < 0.5
            and curr.font.is_bold == prev.font.is_bold):
        return False

    # Default: split (conservative)
    return True
