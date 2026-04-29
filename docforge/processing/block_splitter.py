"""Split text blocks where heading patterns are followed by body text.

PyMuPDF sometimes returns a single TextBlock containing both a heading
(e.g. "나. 보험기간") and its body text concatenated without line breaks.
This module detects such cases and splits them into separate blocks.
"""

from __future__ import annotations

import logging
import re

from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)


# Patterns that indicate a heading prefix followed by a short title.
# Capture group 1 = heading portion, rest = body text.
_HEADING_PREFIX = re.compile(
    r"^("
    r"제\s*\d+조(?:의\d+)?(?:\s*[\(（].*?[\)）])?\s+"
    r"|\d+\.\s+"
    r"|[가나다라마바사아자차카타파하]\.\s+"
    r"|\(\d+\)\s+"
    r")"
)

# Common title-ending words in Korean legal/insurance docs.
# When these appear followed immediately by hangul (no space), it's a concatenation point.
_TITLE_END_SPLIT = re.compile(
    r"(사항|기간|대상|범위|기타|채널|방법|내용|원칙|항목|목적|책임|의무|조건|절차|기준)"
    r"(?=[가-힣])"
)

_MIN_BODY_LENGTH = 10


def split_heading_body(blocks: list[TextBlock]) -> list[TextBlock]:
    """Split blocks where a heading prefix is concatenated with body text.

    Returns a new list with split blocks inserted in place.
    """
    result: list[TextBlock] = []

    for block in blocks:
        parts = _try_split(block.text.strip())
        if parts is not None:
            heading_text, body_text = parts
            logger.debug("Split block: heading=%r body=%r", heading_text[:40], body_text[:40])
            mid_y = block.bbox.y0 + (block.bbox.y1 - block.bbox.y0) * 0.4

            result.append(TextBlock(
                text=heading_text,
                bbox=BBox(
                    x0=block.bbox.x0, y0=block.bbox.y0,
                    x1=block.bbox.x1, y1=mid_y,
                ),
                font=block.font,
                block_type=block.block_type,
                heading_level=block.heading_level,
                confidence=block.confidence,
            ))
            result.append(TextBlock(
                text=body_text,
                bbox=BBox(
                    x0=block.bbox.x0, y0=mid_y,
                    x1=block.bbox.x1, y1=block.bbox.y1,
                ),
                font=block.font,
                block_type=block.block_type,
                heading_level=0,
                confidence=block.confidence,
            ))
        else:
            result.append(block)

    return result


def _try_split(text: str) -> tuple[str, str] | None:
    """Try to split text into heading + body. Returns None if no split found."""
    m = _HEADING_PREFIX.match(text)
    if m is None:
        return None

    prefix_end = m.end()
    rest = text[prefix_end:]

    match = _TITLE_END_SPLIT.search(rest)
    if match is None:
        return None

    split_pos = prefix_end + match.end()
    heading = text[:split_pos].strip()
    body = text[split_pos:].strip()

    if len(body) >= _MIN_BODY_LENGTH:
        return heading, body

    return None
