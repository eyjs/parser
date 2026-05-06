"""OCR result correction with Korean-specific rules.

Corrects common OCR misrecognitions and validates character patterns
specific to Korean insurance documents.
"""

from __future__ import annotations

import re

import logging

from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.domain.enums import BlockType
from docforge.infrastructure.config import ParserConfig

logger = logging.getLogger(__name__)

# Circled number unicode range for restoration
_CIRCLED_NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# Pattern for article references (제N조)
_ARTICLE_PATTERN = re.compile(r"제\s*(\d+)\s*조")

# Bracket pairs for validation
_BRACKET_PAIRS = [("(", ")"), ("「", "」"), ("【", "】")]


def correct_blocks(
    blocks: list[TextBlock],
    config: ParserConfig,
) -> list[TextBlock]:
    """Apply OCR corrections to a list of text blocks.

    Args:
        blocks: Text blocks from OCR recognition.
        config: Parser configuration with correction maps.

    Returns:
        Corrected text blocks (new instances, originals unchanged).
    """
    corrected: list[TextBlock] = []
    for block in blocks:
        new_text = _correct_text(block.text, config)
        new_confidence = block.confidence

        if block.confidence < config.ocr_confidence_fail:
            logger.warning("OCR recognition failed (conf=%.2f): %s", block.confidence, new_text[:80])
        elif block.confidence < config.ocr_confidence_low:
            logger.info("Low OCR confidence (conf=%.2f): %s", block.confidence, new_text[:80])

        corrected.append(TextBlock(
            text=new_text,
            bbox=block.bbox,
            font=block.font,
            block_type=block.block_type,
            heading_level=block.heading_level,
            confidence=new_confidence,
        ))

    return corrected


def _correct_text(text: str, config: ParserConfig) -> str:
    """Apply all correction rules to a text string."""
    result = text

    # Apply OCR correction map
    for wrong, correct in config.ocr_correction_map.items():
        result = result.replace(wrong, correct)

    # Restore broken circled numbers
    result = _restore_circled_numbers(result)

    # Validate bracket pairs
    result = _fix_brackets(result)

    return result


def _restore_circled_numbers(text: str) -> str:
    """Restore OCR-broken circled numbers to Unicode circled characters.

    Common OCR breaks: (1) -> ①, (2) -> ②, etc. when in clause context.
    """
    # Pattern: standalone (N) at line start or after whitespace, where N is 1-20
    def replace_match(m: re.Match[str]) -> str:
        num = int(m.group(1))
        if 1 <= num <= 20:
            return _CIRCLED_NUMS[num - 1]
        return m.group(0)

    # Only replace when it looks like a clause marker (start of line or after newline)
    result = re.sub(r"(?:^|\n)\s*\((\d{1,2})\)\s", lambda m: "\n" + replace_match(m) + " ", text)
    return result


def _fix_brackets(text: str) -> str:
    """Validate and fix unmatched bracket pairs.

    Only fixes obvious cases where a closing bracket is missing at end.
    """
    for open_br, close_br in _BRACKET_PAIRS:
        open_count = text.count(open_br)
        close_count = text.count(close_br)

        if open_count > close_count:
            # Add missing closing brackets at the end
            text = text + close_br * (open_count - close_count)

    return text
