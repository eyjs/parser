"""Text quality utilities -- garbled text detection.

Extracted from page_classifier.py to enable reuse by both
page_classifier and block_quality_verifier. Single source of truth
for garbled text detection -- duplicate implementations are forbidden.
"""

from __future__ import annotations


def _readable_stats(text: str) -> tuple[int, int]:
    """Count readable and total non-whitespace characters.

    Returns:
        (readable_count, total_count)
    """
    readable_count = 0
    total_count = 0
    for ch in text:
        if ch.isspace():
            continue
        total_count += 1
        if (
            '가' <= ch <= '힣'  # Korean syllables
            or 'ㄱ' <= ch <= 'ㆎ'  # Korean jamo
            or 'A' <= ch <= 'Z'
            or 'a' <= ch <= 'z'
            or '0' <= ch <= '9'
            or ch in '.,;:!?()-/\\[]{}@#$%&*+=<>~`\'"'
            or '①' <= ch <= '⑳'  # circled numbers
            or ch in '·…―'
        ):
            readable_count += 1
    return readable_count, total_count


def is_garbled_text(raw_text: str) -> bool:
    """Detect text that was extracted but is unreadable (custom font encoding).

    Returns True when readable character ratio falls below 0.3.
    Empty or whitespace-only strings return False.

    This is the canonical implementation -- page_classifier.py and
    block_quality_verifier.py both import from here.
    """
    stripped = raw_text.strip()
    if not stripped:
        return False
    readable_count, total_count = _readable_stats(stripped)
    if total_count == 0:
        return False
    return readable_count / total_count < 0.3


def garbled_ratio(raw_text: str) -> float:
    """Return the ratio of garbled (non-readable) characters (0.0 -- 1.0).

    0.0 = fully normal, 1.0 = fully garbled.
    Empty or whitespace-only strings return 0.0.
    """
    stripped = raw_text.strip()
    if not stripped:
        return 0.0
    readable_count, total_count = _readable_stats(stripped)
    if total_count == 0:
        return 0.0
    return 1.0 - (readable_count / total_count)


__all__ = [
    "is_garbled_text",
    "garbled_ratio",
]
