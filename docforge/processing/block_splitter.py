"""Split text blocks where heading patterns are followed by body text.

PyMuPDF sometimes returns a single TextBlock containing both a heading
(e.g. "나. 보험기간") and its body text concatenated without line breaks.
This module detects such cases and splits them into separate blocks.

Uses morpheme analysis (via Protocol injection) for scoring-based split
selection. No direct dependency on any morpheme library.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox

if TYPE_CHECKING:
    from docforge.domain.ports import MorphemeAnalyzer, MorphemeToken

logger = logging.getLogger(__name__)


# Patterns that indicate a heading prefix followed by a short title.
_HEADING_PREFIX = re.compile(
    r"^("
    r"제\s*\d+조(?:의\d+)?(?:\s*[\(（].*?[\)）])?\s+"
    r"|\d+\.\s+"
    r"|[가나다라마바사아자차카타파하]\.\s+"
    r"|\(\d+\)\s+"
    r")"
)

_MIN_BODY_LENGTH = 10
_MIN_HEADING_LENGTH = 5
_MAX_HEADING_LENGTH = 35


def split_heading_body(
    blocks: list[TextBlock],
    morpheme_analyzer: MorphemeAnalyzer | None = None,
) -> list[TextBlock]:
    """Split blocks where a heading prefix is concatenated with body text.

    When morpheme_analyzer is None or unavailable, returns blocks unchanged.
    Returns a new list with split blocks inserted in place.
    """
    if morpheme_analyzer is None or not morpheme_analyzer.is_available():
        return list(blocks)

    result: list[TextBlock] = []

    for block in blocks:
        parts = _try_split(block.text.strip(), morpheme_analyzer)
        if parts is not None:
            heading_text, body_text = parts
            logger.debug(
                "Split block: heading=%r body=%r",
                heading_text[:40],
                body_text[:40],
            )
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


def _try_split(
    text: str,
    analyzer: MorphemeAnalyzer,
) -> tuple[str, str] | None:
    """Try to split text into heading + body using scoring-based selection.

    Algorithm:
    1. Match _HEADING_PREFIX to get prefix_end
    2. Tokenize text after prefix with morpheme analyzer
    3. Generate split candidates at each token boundary (start + length)
    4. Filter: heading 5-35 chars, body >= 10 chars
    5. Score each candidate
    6. Pick highest score, ties broken by shorter heading
    7. No candidates or score <= 0 -> return None
    """
    prefix_match = _HEADING_PREFIX.match(text)
    if prefix_match is None:
        return None

    prefix_end = prefix_match.end()
    rest = text[prefix_end:]

    if not rest:
        return None

    tokens = analyzer.tokenize(rest)
    if not tokens:
        return None

    candidates = _generate_candidates(text, prefix_end, tokens)
    if not candidates:
        return None

    best = _select_best_candidate(candidates)
    if best is None:
        return None

    heading = text[:best.split_pos].strip()
    body = text[best.split_pos:].strip()
    return heading, body


class _SplitCandidate:
    """Internal: a candidate split position with its score."""

    __slots__ = ("split_pos", "heading_len", "body_len", "score")

    def __init__(
        self,
        split_pos: int,
        heading_len: int,
        body_len: int,
        score: int,
    ) -> None:
        self.split_pos = split_pos
        self.heading_len = heading_len
        self.body_len = body_len
        self.score = score


def _generate_candidates(
    text: str,
    prefix_end: int,
    tokens: list[MorphemeToken],
) -> list[_SplitCandidate]:
    """Generate and score split candidates from token boundaries."""
    candidates: list[_SplitCandidate] = []

    for i, token in enumerate(tokens):
        # Split position in original text = prefix_end + token boundary in rest
        split_pos = prefix_end + token.start + token.length
        heading_len = split_pos
        body_len = len(text) - split_pos

        # Filter: heading length 5-35 chars, body >= 10 chars
        if heading_len < _MIN_HEADING_LENGTH or heading_len > _MAX_HEADING_LENGTH:
            continue
        if body_len < _MIN_BODY_LENGTH:
            continue

        score = _score_candidate(token, tokens, i, heading_len, body_len)
        candidates.append(_SplitCandidate(
            split_pos=split_pos,
            heading_len=heading_len,
            body_len=body_len,
            score=score,
        ))

    return candidates


def _score_candidate(
    token_before: MorphemeToken,
    tokens: list[MorphemeToken],
    token_index: int,
    heading_len: int,
    body_len: int,
) -> int:
    """Score a split candidate based on morpheme properties.

    Scoring rules:
    - Token before split is noun (NN*): +3
    - Token before split is ETN (nominalizing): +2
    - Token after split starts with noun (NN*): +1
    - heading < body length: +1
    """
    score = 0

    # Token before split is noun (NNG, NNP, NNB)
    if token_before.tag.startswith("NN"):
        score += 3

    # Token before split is ETN (명사형전성어미)
    if token_before.tag == "ETN":
        score += 2

    # Token after split starts with noun
    next_index = token_index + 1
    if next_index < len(tokens) and tokens[next_index].tag.startswith("NN"):
        score += 1

    # Heading shorter than body (length balance)
    if heading_len < body_len:
        score += 1

    return score


def _select_best_candidate(
    candidates: list[_SplitCandidate],
) -> _SplitCandidate | None:
    """Select the best candidate: highest score, ties broken by shorter heading.

    Returns None if no candidate has score > 0.
    """
    valid = [c for c in candidates if c.score > 0]
    if not valid:
        return None

    # Sort by score descending, then heading_len ascending (shorter heading wins ties)
    valid.sort(key=lambda c: (-c.score, c.heading_len))

    best = valid[0]
    logger.debug(
        "Best split at pos=%d, score=%d, heading_len=%d, body_len=%d",
        best.split_pos, best.score, best.heading_len, best.body_len,
    )
    return best
