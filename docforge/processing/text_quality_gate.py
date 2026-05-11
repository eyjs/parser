"""Text Quality Gate -- pre-classification quality scoring and repair.

Runs **before** block classification to detect and handle:
- Mojibake (encoding mismatches like latin1-encoded Korean)
- Unmapped CID glyphs ``(cid:NNN)``
- Low printable-character ratio
- Language inconsistency

Each quality signal produces a 0.0--1.0 score (1.0 = healthy).
The composite ``quality_score`` determines the repair path:

- score >= 0.8: pass through to classifier
- 0.4 <= score < 0.8: attempt repair, then classify
- score < 0.4: mark as low-confidence, attempt OCR fallback

Design principles
-----------------
* **Additive** -- the gate can be bypassed without breaking the pipeline.
* **Immutable** -- original blocks are never mutated; new blocks returned.
* **Composable** -- quality signals can be independently tested.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality Signals
# ---------------------------------------------------------------------------

# CID pattern: "(cid:NNN)" or "(cid:NNN )" with optional whitespace
_CID_PATTERN = re.compile(r"\(cid:\s*\d+\s*\)")

# Common mojibake byte sequences (latin1-interpreted UTF-8 Korean)
_MOJIBAKE_PATTERNS: list[re.Pattern[str]] = [
    # UTF-8 Korean decoded as latin1 produces sequences like \xc3\xa~ etc.
    re.compile(r"[\xc0-\xdf][\x80-\xbf]"),
    # Double-encoded UTF-8
    re.compile(r"\xc3[\xa0-\xbf]"),
    # Replacement character
    re.compile(r"�"),
    # Common mojibake trigram patterns
    re.compile(r"[Ã¢Ã£Ã¤Ã¥Ã¦Ã§Ã¨Ã©]"),
]

# Encoding recovery attempts (source -> target)
_ENCODING_REPAIRS: list[tuple[str, str]] = [
    ("latin1", "utf-8"),
    ("cp1252", "utf-8"),
    ("euc-kr", "utf-8"),
    ("iso-8859-1", "utf-8"),
]


@dataclass(frozen=True)
class QualitySignals:
    """Individual quality signal scores (each 0.0--1.0, higher = better)."""

    encoding_health: float = 1.0
    cid_ratio: float = 1.0
    printable_ratio: float = 1.0
    language_consistency: float = 1.0


@dataclass(frozen=True)
class TextQualityResult:
    """Quality gate output for a single text block."""

    original_text: str
    repaired_text: str | None
    signals: QualitySignals
    quality_score: float  # weighted composite 0.0--1.0
    needs_repair: bool
    repair_applied: bool
    repair_method: str  # "none", "encoding", "ocr_fallback", "confidence_downgrade"
    confidence_penalty: float  # 0.0--1.0, subtracted from block confidence


# ---------------------------------------------------------------------------
# Signal Computation
# ---------------------------------------------------------------------------


def _compute_encoding_health(text: str) -> float:
    """Score encoding health (1.0 = clean, 0.0 = badly encoded).

    Detects mojibake patterns and replacement characters.
    """
    if not text:
        return 1.0

    total_chars = len(text)
    if total_chars == 0:
        return 1.0

    # Count mojibake-like characters
    bad_count = 0
    for pattern in _MOJIBAKE_PATTERNS:
        bad_count += len(pattern.findall(text))

    # Count Unicode replacement characters
    bad_count += text.count("�")

    # Count PUA characters (Private Use Area)
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Co":  # Private Use
            bad_count += 1

    ratio = bad_count / total_chars
    return max(0.0, 1.0 - ratio * 3.0)  # 33% bad -> score 0.0


def _compute_cid_ratio(text: str) -> float:
    """Score CID mapping health (1.0 = no CIDs, 0.0 = all CIDs).

    ``(cid:NNN)`` patterns indicate unmapped font glyphs.
    """
    if not text:
        return 1.0

    matches = _CID_PATTERN.findall(text)
    if not matches:
        return 1.0

    # Estimate character coverage lost to CID references
    cid_chars = sum(len(m) for m in matches)
    total_chars = len(text)
    if total_chars == 0:
        return 1.0

    ratio = cid_chars / total_chars
    return max(0.0, 1.0 - ratio * 2.0)  # 50% CID -> score 0.0


def _compute_printable_ratio(text: str) -> float:
    """Score printable character ratio (1.0 = all printable).

    Counts characters that are printable or whitespace.
    """
    if not text:
        return 1.0

    total = 0
    printable = 0
    for ch in text:
        total += 1
        if ch.isprintable() or ch.isspace():
            printable += 1

    if total == 0:
        return 1.0

    return printable / total


def _compute_language_consistency(text: str, expected_lang: str = "ko") -> float:
    """Score language consistency (1.0 = consistent with expected language).

    Simple heuristic: checks that the dominant script matches expectations.
    For Korean (default), we expect a mix of Hangul + ASCII.
    """
    if not text or len(text.strip()) < 5:
        return 1.0

    total = 0
    hangul = 0
    latin = 0
    cjk = 0

    for ch in text:
        if ch.isspace():
            continue
        total += 1
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3 or 0x3131 <= cp <= 0x318E:
            hangul += 1
        elif 0x0041 <= cp <= 0x007A or 0x00C0 <= cp <= 0x024F:
            latin += 1
        elif 0x4E00 <= cp <= 0x9FFF:
            cjk += 1

    if total == 0:
        return 1.0

    if expected_lang == "ko":
        # Korean documents should have Hangul or mixed Hangul+ASCII
        if hangul > 0:
            return 1.0
        if latin > 0 and hangul == 0 and cjk == 0:
            # Pure latin in a Korean doc is suspicious but not fatal
            return 0.6
        return 0.5

    if expected_lang == "en":
        if latin / total > 0.5:
            return 1.0
        return 0.5

    return 0.8  # Unknown language -- mild confidence


def compute_quality_signals(text: str, expected_lang: str = "ko") -> QualitySignals:
    """Compute all quality signals for a text string."""
    return QualitySignals(
        encoding_health=_compute_encoding_health(text),
        cid_ratio=_compute_cid_ratio(text),
        printable_ratio=_compute_printable_ratio(text),
        language_consistency=_compute_language_consistency(text, expected_lang),
    )


def composite_quality_score(signals: QualitySignals) -> float:
    """Compute weighted composite quality score.

    Weights are tuned to prioritize encoding and CID issues (most
    impactful) over printable ratio and language consistency.
    """
    return (
        signals.encoding_health * 0.35
        + signals.cid_ratio * 0.30
        + signals.printable_ratio * 0.20
        + signals.language_consistency * 0.15
    )


# ---------------------------------------------------------------------------
# Repair Pipeline
# ---------------------------------------------------------------------------


def _try_encoding_repair(text: str) -> str | None:
    """Attempt to fix mojibake by re-encoding through common codec pairs.

    Returns repaired text or None if no improvement found.
    """
    original_health = _compute_encoding_health(text)
    best_text: str | None = None
    best_health = original_health

    for source_enc, target_enc in _ENCODING_REPAIRS:
        try:
            raw_bytes = text.encode(source_enc, errors="ignore")
            candidate = raw_bytes.decode(target_enc, errors="ignore")
            candidate_health = _compute_encoding_health(candidate)
            if candidate_health > best_health + 0.1:
                best_health = candidate_health
                best_text = candidate
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue

    return best_text


def _strip_cid_references(text: str) -> str:
    """Remove ``(cid:NNN)`` patterns from text."""
    return _CID_PATTERN.sub("", text).strip()


# ---------------------------------------------------------------------------
# TextQualityGate
# ---------------------------------------------------------------------------


class TextQualityGate:
    """Pre-classification text quality gate.

    Usage::

        gate = TextQualityGate()
        result = gate.evaluate("Some text with (cid:123) patterns")
        if result.needs_repair and result.repaired_text:
            use_text = result.repaired_text
    """

    def __init__(
        self,
        *,
        pass_threshold: float = 0.8,
        repair_threshold: float = 0.4,
        expected_lang: str = "ko",
    ) -> None:
        self._pass_threshold = pass_threshold
        self._repair_threshold = repair_threshold
        self._expected_lang = expected_lang

    def evaluate(self, text: str) -> TextQualityResult:
        """Evaluate text quality and attempt repair if needed.

        Returns an immutable TextQualityResult with repair details.
        """
        signals = compute_quality_signals(text, self._expected_lang)
        score = composite_quality_score(signals)

        if score >= self._pass_threshold:
            return TextQualityResult(
                original_text=text,
                repaired_text=None,
                signals=signals,
                quality_score=score,
                needs_repair=False,
                repair_applied=False,
                repair_method="none",
                confidence_penalty=0.0,
            )

        # Attempt repair
        repaired: str | None = None
        method = "none"
        penalty = 0.0

        # Strategy 1: Encoding repair (for mojibake)
        if signals.encoding_health < 0.7:
            candidate = _try_encoding_repair(text)
            if candidate is not None:
                repaired = candidate
                method = "encoding"
                penalty = 0.1

        # Strategy 2: Strip CID references
        if repaired is None and signals.cid_ratio < 0.7:
            stripped = _strip_cid_references(text)
            if stripped and len(stripped) > len(text) * 0.3:
                repaired = stripped
                method = "cid_strip"
                penalty = 0.2
            elif stripped:
                # Too much text lost -- keep original but penalize
                repaired = None
                method = "confidence_downgrade"
                penalty = 0.4

        # Strategy 3: Low quality with no repair possible
        if repaired is None and score < self._repair_threshold:
            method = "confidence_downgrade"
            penalty = 0.5

        needs_repair = score < self._pass_threshold
        repair_applied = repaired is not None

        if repair_applied:
            # Re-evaluate repaired text
            new_signals = compute_quality_signals(repaired, self._expected_lang)
            new_score = composite_quality_score(new_signals)
            logger.info(
                "text_quality_gate: repaired via %s (%.2f -> %.2f)",
                method,
                score,
                new_score,
            )
        else:
            new_signals = signals
            new_score = score

        return TextQualityResult(
            original_text=text,
            repaired_text=repaired,
            signals=new_signals if repair_applied else signals,
            quality_score=new_score if repair_applied else score,
            needs_repair=needs_repair,
            repair_applied=repair_applied,
            repair_method=method,
            confidence_penalty=penalty,
        )

    def evaluate_batch(
        self,
        texts: list[str],
    ) -> list[TextQualityResult]:
        """Evaluate a batch of texts."""
        return [self.evaluate(t) for t in texts]


__all__ = [
    "TextQualityGate",
    "TextQualityResult",
    "QualitySignals",
    "compute_quality_signals",
    "composite_quality_score",
]
