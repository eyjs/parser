"""Unified signal-based block classifier.

Replaces the dual-path classification (text_structurer + heading_detector)
with a single scoring pipeline. Each **signal** independently produces a
0.0--1.0 score; the ``BlockClassifier`` collects those scores, applies a
weighted sum, and maps the result to a ``BlockType`` + heading level.

Design principles
-----------------
* **No hardcoded patterns** -- document-specific regex lives in domain
  profiles, not here.  Signals are structural/visual heuristics.
* **Single path** -- OCR and digital pages flow through the same pipeline.
* **Graceful degradation** -- missing data (e.g. font_size=0 on OCR pages)
  causes the corresponding signal to return 0.0, not crash.

Public API
----------
* ``BlockClassifier`` -- the main entry point.
* ``classify_block_signal()`` -- convenience function matching the legacy
  ``classify_block()`` call signature for backward compatibility.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Signal(Protocol):
    """A single classification signal.

    Every signal receives a ``SignalContext`` and returns a float in
    [0.0, 1.0] where higher values indicate stronger heading likelihood.
    """

    @property
    def name(self) -> str: ...

    def compute(self, ctx: "SignalContext") -> float: ...


@dataclass(frozen=True)
class SignalContext:
    """Immutable bag of data available to all signals.

    Fields that are unavailable default to sentinel values (0.0, None, "")
    so signals can degrade gracefully.
    """

    text: str = ""
    font_size: float = 0.0
    is_bold: bool = False
    avg_font_size: float = 0.0
    font_size_std: float = 0.0
    bbox: BBox = field(default_factory=lambda: BBox(0, 0, 0, 0))
    median_bbox_height: float = 0.0
    prev_bbox: BBox | None = None
    page_height: float = 0.0
    page_width: float = 0.0
    layout_label: str = ""


# ---------------------------------------------------------------------------
# Signal Implementations
# ---------------------------------------------------------------------------


class FontDeviationSignal:
    """How far the block's font size deviates from the document average
    in standard-deviation units.  Larger fonts score higher."""

    @property
    def name(self) -> str:
        return "font_deviation"

    def compute(self, ctx: SignalContext) -> float:
        if ctx.font_size <= 0 or ctx.avg_font_size <= 0:
            return 0.0
        std = ctx.font_size_std if ctx.font_size_std > 0 else ctx.avg_font_size * 0.15
        deviation = (ctx.font_size - ctx.avg_font_size) / std
        if deviation >= 2.0:
            return 1.0
        if deviation >= 1.0:
            return 0.5 + 0.5 * (deviation - 1.0)
        if deviation >= 0.5:
            return 0.25 * (deviation - 0.5) / 0.5
        return 0.0


class FontWeightSignal:
    """Bold text scores 1.0; non-bold scores 0.0."""

    @property
    def name(self) -> str:
        return "font_weight"

    def compute(self, ctx: SignalContext) -> float:
        return 1.0 if ctx.is_bold else 0.0


class BBoxHeightRatioSignal:
    """Block bbox height relative to page median height."""

    @property
    def name(self) -> str:
        return "bbox_height_ratio"

    def compute(self, ctx: SignalContext) -> float:
        if ctx.median_bbox_height <= 0:
            return 0.0
        ratio = ctx.bbox.height / ctx.median_bbox_height
        if ratio >= 1.5:
            return 1.0
        if ratio >= 1.2:
            return (ratio - 1.0) / 0.5
        return 0.0


class VerticalGapSignal:
    """Normalized vertical gap between this block and the previous one.
    Larger gaps suggest section boundaries."""

    @property
    def name(self) -> str:
        return "vertical_gap"

    def compute(self, ctx: SignalContext) -> float:
        if ctx.prev_bbox is None or ctx.page_height <= 0:
            return 0.0
        gap = max(0.0, ctx.bbox.y0 - ctx.prev_bbox.y1)
        normalized = gap / ctx.page_height
        if normalized >= 0.05:
            return 1.0
        if normalized >= 0.02:
            return (normalized - 0.02) / 0.03
        return 0.0


class TextLengthSignal:
    """Short text is more likely a heading.  Scores inversely with length."""

    @property
    def name(self) -> str:
        return "text_length"

    def compute(self, ctx: SignalContext) -> float:
        length = len(ctx.text.strip())
        if length == 0:
            return 0.0
        if length <= 10:
            return 1.0
        if length <= 30:
            return 0.7
        if length <= 60:
            return 0.3
        if length <= 100:
            return 0.1
        return 0.0


class HasNumberingSignal:
    """Detects structured numbering patterns (language-agnostic set)."""

    _PATTERNS: list[re.Pattern[str]] = [
        # Korean legal numbering
        re.compile(r"^제\s*\d+\s*(?:편|장|절|관|조)"),
        # Circled numbers
        re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]"),
        # Arabic numbering: "1.", "1.1", "1.1.1"
        re.compile(r"^\d+(?:\.\d+)*\.\s"),
        # Korean consonant numbering: "가.", "나."
        re.compile(r"^[가-하]\.\s"),
        # Parenthesized: "(1)", "(a)"
        re.compile(r"^\(\d+\)\s"),
        re.compile(r"^\([a-z]\)\s", re.IGNORECASE),
        # Chapter/Section (English)
        re.compile(r"^(?:chapter|section|part)\s+\d+", re.IGNORECASE),
    ]

    @property
    def name(self) -> str:
        return "has_numbering"

    def compute(self, ctx: SignalContext) -> float:
        stripped = ctx.text.strip()
        for pattern in self._PATTERNS:
            if pattern.match(stripped):
                return 1.0
        return 0.0


class LayoutLabelSignal:
    """Score from layout engine (Surya/Docling) label."""

    _HEADING_LABELS = frozenset({"Title", "Section-header", "title", "section-header"})

    @property
    def name(self) -> str:
        return "layout_label"

    def compute(self, ctx: SignalContext) -> float:
        if not ctx.layout_label:
            return 0.0
        if ctx.layout_label in self._HEADING_LABELS:
            return 1.0
        return 0.0


class EndsWithoutPeriodSignal:
    """Headings typically do not end with a period."""

    @property
    def name(self) -> str:
        return "ends_without_period"

    def compute(self, ctx: SignalContext) -> float:
        stripped = ctx.text.strip()
        if not stripped:
            return 0.0
        if stripped[-1] in ".。":
            return 0.0
        return 1.0


class WidthRatioSignal:
    """Block width relative to page width.  Headings are often narrower."""

    @property
    def name(self) -> str:
        return "width_ratio"

    def compute(self, ctx: SignalContext) -> float:
        if ctx.page_width <= 0 or ctx.bbox.width <= 0:
            return 0.0
        ratio = ctx.bbox.width / ctx.page_width
        if ratio <= 0.3:
            return 1.0
        if ratio <= 0.5:
            return 0.6
        if ratio <= 0.7:
            return 0.3
        return 0.0


# ---------------------------------------------------------------------------
# Default signal set
# ---------------------------------------------------------------------------

ALL_SIGNALS: list[Signal] = [
    FontDeviationSignal(),
    FontWeightSignal(),
    BBoxHeightRatioSignal(),
    VerticalGapSignal(),
    TextLengthSignal(),
    HasNumberingSignal(),
    LayoutLabelSignal(),
    EndsWithoutPeriodSignal(),
    WidthRatioSignal(),
]

# Default weights -- tuned for general documents.
# Domain profiles can override these.
DEFAULT_WEIGHTS: dict[str, float] = {
    "font_deviation": 0.20,
    "font_weight": 0.10,
    "bbox_height_ratio": 0.10,
    "vertical_gap": 0.08,
    "text_length": 0.10,
    "has_numbering": 0.18,
    "layout_label": 0.12,
    "ends_without_period": 0.05,
    "width_ratio": 0.07,
}

# Heading threshold: weighted sum above this -> classify as HEADING
HEADING_THRESHOLD = 0.35


# ---------------------------------------------------------------------------
# Heading Level Determination
# ---------------------------------------------------------------------------

# Korean legal numbering -> explicit heading level
_LEVEL_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^제\s*\d+\s*편"), 1),
    (re.compile(r"^제\s*\d+\s*장"), 2),
    (re.compile(r"^제\s*\d+\s*절"), 3),
    (re.compile(r"^제\s*\d+\s*관"), 3),
    (re.compile(r"^제\s*\d+\s*조"), 4),
    (re.compile(r"^(?:chapter|part)\s+\d+", re.IGNORECASE), 1),
    (re.compile(r"^section\s+\d+", re.IGNORECASE), 2),
]

# Decimal subsection depth -> heading level
_DECIMAL_DEPTH_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\.\s")


def _determine_heading_level(ctx: SignalContext, font_score: float) -> int:
    """Determine heading level from signals.

    Priority:
    1. Explicit numbering pattern -> fixed level
    2. Decimal subsection depth
    3. Font deviation magnitude -> level 2--4
    4. Layout label "Title" -> level 1
    5. Default -> level 2
    """
    stripped = ctx.text.strip()

    # 1. Explicit numbering
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.match(stripped):
            return level

    # 2. Decimal subsection
    m = _DECIMAL_DEPTH_PATTERN.match(stripped)
    if m:
        depth = m.group(1).count(".")
        return min(2 + depth, 6)

    # 3. Font deviation
    if font_score >= 0.8:
        return 2
    if font_score >= 0.5:
        return 3
    if font_score >= 0.25:
        return 4

    # 4. Layout label
    if ctx.layout_label in ("Title", "title"):
        return 1

    return 2


# ---------------------------------------------------------------------------
# BlockClassifier
# ---------------------------------------------------------------------------


@dataclass
class SignalScore:
    """Named score from one signal evaluation."""

    name: str
    raw: float
    weight: float

    @property
    def weighted(self) -> float:
        return self.raw * self.weight


class BlockClassifier:
    """Unified block classifier using weighted signal scoring.

    Usage::

        classifier = BlockClassifier()
        block_type, heading_level = classifier.classify(ctx)
    """

    def __init__(
        self,
        signals: list[Signal] | None = None,
        weights: dict[str, float] | None = None,
        heading_threshold: float = HEADING_THRESHOLD,
    ) -> None:
        self._signals = signals or list(ALL_SIGNALS)
        self._weights = weights or dict(DEFAULT_WEIGHTS)
        self._heading_threshold = heading_threshold

    def classify(self, ctx: SignalContext) -> tuple[BlockType, int]:
        """Classify a block into (BlockType, heading_level).

        heading_level is 0 for non-heading blocks.
        """
        stripped = ctx.text.strip()
        if not stripped:
            return BlockType.TEXT, 0

        scores = self._compute_scores(ctx)
        total = sum(s.weighted for s in scores)

        # Find the font_deviation raw score for level determination
        font_score = 0.0
        for s in scores:
            if s.name == "font_deviation":
                font_score = s.raw
                break

        if total >= self._heading_threshold:
            level = _determine_heading_level(ctx, font_score)
            logger.debug(
                "block_classifier: '%s' -> HEADING (level=%d, score=%.3f, "
                "signals=%s)",
                stripped[:40],
                level,
                total,
                {s.name: f"{s.raw:.2f}" for s in scores if s.raw > 0},
            )
            return BlockType.HEADING, level

        return BlockType.TEXT, 0

    def compute_heading_score(self, ctx: SignalContext) -> float:
        """Return the raw heading score without classification."""
        scores = self._compute_scores(ctx)
        return sum(s.weighted for s in scores)

    def _compute_scores(self, ctx: SignalContext) -> list[SignalScore]:
        """Evaluate all signals and return named scores."""
        result: list[SignalScore] = []
        for signal in self._signals:
            weight = self._weights.get(signal.name, 0.0)
            try:
                raw = signal.compute(ctx)
            except Exception:
                logger.warning(
                    "Signal %s failed, defaulting to 0.0",
                    signal.name,
                    exc_info=True,
                )
                raw = 0.0
            raw = max(0.0, min(1.0, raw))
            result.append(SignalScore(name=signal.name, raw=raw, weight=weight))
        return result


# ---------------------------------------------------------------------------
# Batch classification helpers
# ---------------------------------------------------------------------------


def build_context_for_block(
    block: TextBlock,
    *,
    avg_font_size: float = 0.0,
    font_size_std: float = 0.0,
    median_bbox_height: float = 0.0,
    prev_bbox: BBox | None = None,
    page_height: float = 0.0,
    page_width: float = 0.0,
    layout_label: str = "",
) -> SignalContext:
    """Build a SignalContext from a TextBlock + page-level stats."""
    return SignalContext(
        text=block.text,
        font_size=block.font.size,
        is_bold=block.font.is_bold,
        avg_font_size=avg_font_size,
        font_size_std=font_size_std,
        bbox=block.bbox,
        median_bbox_height=median_bbox_height,
        prev_bbox=prev_bbox,
        page_height=page_height,
        page_width=page_width,
        layout_label=layout_label,
    )


def classify_blocks(
    blocks: list[TextBlock],
    *,
    avg_font_size: float = 0.0,
    page_height: float = 0.0,
    page_width: float = 0.0,
    layout_blocks: list[LayoutBlock] | None = None,
    classifier: BlockClassifier | None = None,
) -> list[TextBlock]:
    """Classify a list of TextBlocks using the unified signal classifier.

    Returns a new list of TextBlock with updated block_type and heading_level.
    Original blocks are never mutated.
    """
    if not blocks:
        return []

    if classifier is None:
        classifier = BlockClassifier()

    # Precompute page-level statistics
    font_sizes = [b.font.size for b in blocks if b.font.size > 0]
    font_std = statistics.stdev(font_sizes) if len(font_sizes) >= 2 else 0.0

    heights = [b.bbox.height for b in blocks if b.bbox.height > 0]
    median_h = statistics.median(heights) if heights else 0.0

    # Build layout label lookup
    label_map: dict[str, str] = {}
    if layout_blocks:
        for block in blocks:
            best_iou = 0.0
            best_label = ""
            for lb in layout_blocks:
                iou = block.bbox.iou(lb.bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_label = lb.label
            if best_iou >= 0.3:
                label_map[id(block)] = best_label

    result: list[TextBlock] = []
    prev_bbox: BBox | None = None

    for block in blocks:
        ctx = build_context_for_block(
            block,
            avg_font_size=avg_font_size,
            font_size_std=font_std,
            median_bbox_height=median_h,
            prev_bbox=prev_bbox,
            page_height=page_height,
            page_width=page_width,
            layout_label=label_map.get(id(block), ""),
        )

        block_type, heading_level = classifier.classify(ctx)

        result.append(TextBlock(
            text=block.text,
            bbox=block.bbox,
            font=block.font,
            block_type=block_type,
            heading_level=heading_level,
            confidence=block.confidence,
            block_id=block.block_id,
            parent_id=block.parent_id,
        ))

        prev_bbox = block.bbox

    return result


# ---------------------------------------------------------------------------
# Legacy-compatible convenience function
# ---------------------------------------------------------------------------


def classify_block_signal(
    text: str,
    font_size: float = 0.0,
    is_bold: bool = False,
    avg_font_size: float = 0.0,
    *,
    bbox: BBox | None = None,
    page_height: float = 0.0,
    page_width: float = 0.0,
    layout_label: str = "",
    classifier: BlockClassifier | None = None,
) -> tuple[BlockType, int]:
    """Convenience wrapper matching the legacy classify_block signature
    with optional signal-enriched parameters."""
    if classifier is None:
        classifier = BlockClassifier()

    ctx = SignalContext(
        text=text,
        font_size=font_size,
        is_bold=is_bold,
        avg_font_size=avg_font_size,
        bbox=bbox or BBox(0, 0, 0, 0),
        page_height=page_height,
        page_width=page_width,
        layout_label=layout_label,
    )
    return classifier.classify(ctx)


__all__ = [
    "Signal",
    "SignalContext",
    "BlockClassifier",
    "SignalScore",
    "classify_blocks",
    "classify_block_signal",
    "build_context_for_block",
    "ALL_SIGNALS",
    "DEFAULT_WEIGHTS",
    "HEADING_THRESHOLD",
    # Individual signals
    "FontDeviationSignal",
    "FontWeightSignal",
    "BBoxHeightRatioSignal",
    "VerticalGapSignal",
    "TextLengthSignal",
    "HasNumberingSignal",
    "LayoutLabelSignal",
    "EndsWithoutPeriodSignal",
    "WidthRatioSignal",
]
