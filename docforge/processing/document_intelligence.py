"""Document Intelligence Layer -- pre-parse document scan.

Scans the entire document using only metadata + text extraction
(no image rendering / get_pixmap) to build a per-page strategy map.
Performance target: <= 30 seconds for a 314-page document.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from docforge.domain.value_objects import DocumentStrategyReport, PageStrategy
from docforge.processing.text_quality_utils import garbled_ratio

if TYPE_CHECKING:
    import fitz  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (from requirement.md)
# ---------------------------------------------------------------------------
_TEXT_DENSITY_LOW = 0.05
_GARBLED_RATIO_HIGH = 0.15
_TABLE_COUNT_THRESHOLD = 0
_IMAGE_COUNT_THRESHOLD = 3
_NOISE_TEXT_MIN = 10

# Quick classify keywords
_TOC_KEYWORDS = ("목 차", "목차", "차 례", "차례", "Contents", "CONTENTS")
_TOC_DOT_PATTERN = re.compile(r"\.{3,}")


class DocumentIntelligence:
    """Pre-parse document analyser.

    Generates a :class:`DocumentStrategyReport` containing per-page
    :class:`PageStrategy` objects that guide the execution phase.
    """

    def analyze(self, doc: fitz.Document) -> DocumentStrategyReport:
        """Scan all pages and return a strategy report.

        Uses only ``get_text()`` / ``get_text("dict")`` / ``get_images()``
        -- never ``get_pixmap()`` -- so the scan stays fast.
        """
        strategies: list[PageStrategy] = []
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            try:
                strategy = self._analyze_page(page, page_idx)
            except Exception:
                logger.warning(
                    "DocumentIntelligence: page %d analysis failed, defaulting to pymupdf_text",
                    page_idx,
                    exc_info=True,
                )
                strategy = PageStrategy(
                    page_index=page_idx,
                    primary_method="pymupdf_text",
                    fallback_chain=("apple_vision_ocr", "vlm_full"),
                    block_quality_threshold=0.60,
                    surya_needed=False,
                    estimated_complexity="simple",
                )
            strategies.append(strategy)

        return self._build_report(strategies)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _analyze_page(self, page: fitz.Page, page_index: int) -> PageStrategy:
        """Determine strategy for a single page."""
        raw_text = page.get_text().strip()
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        images = page.get_images()
        image_count = len(images)

        # Metrics
        text_density = len(raw_text) / page_area if page_area > 0 else 0.0
        g_ratio = garbled_ratio(raw_text) if raw_text else 0.0

        # Table count estimation from text dict
        text_dict = page.get_text("dict")
        table_count = _estimate_table_count(text_dict)

        # Quick classify for skip candidates
        page_kind = _quick_classify(raw_text, text_density, image_count)

        # Primary method decision
        if page_kind == "skip":
            primary_method = "skip"
        elif text_density < _TEXT_DENSITY_LOW and image_count > 0:
            primary_method = "apple_vision_ocr"
        elif g_ratio > _GARBLED_RATIO_HIGH:
            primary_method = "apple_vision_ocr"
        else:
            primary_method = "pymupdf_text"

        # Surya needed
        surya_needed = table_count > _TABLE_COUNT_THRESHOLD or image_count > _IMAGE_COUNT_THRESHOLD

        # Fallback chain
        fallback_chain = _build_fallback_chain(primary_method)

        # Complexity
        estimated_complexity = _estimate_complexity(table_count, image_count)

        # Block quality threshold (default 0.60)
        block_quality_threshold = 0.60

        return PageStrategy(
            page_index=page_index,
            primary_method=primary_method,
            fallback_chain=fallback_chain,
            block_quality_threshold=block_quality_threshold,
            surya_needed=surya_needed,
            estimated_complexity=estimated_complexity,
        )

    def _build_report(
        self, strategies: list[PageStrategy],
    ) -> DocumentStrategyReport:
        """Aggregate page strategies into a report."""
        strategy_counts: dict[str, int] = {}
        surya_count = 0
        for s in strategies:
            strategy_counts[s.primary_method] = (
                strategy_counts.get(s.primary_method, 0) + 1
            )
            if s.surya_needed:
                surya_count += 1

        return DocumentStrategyReport(
            pages=tuple(strategies),
            total_pages=len(strategies),
            strategy_counts=strategy_counts,
            surya_page_count=surya_count,
            generated_at=datetime.now().isoformat(),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _estimate_table_count(text_dict: dict) -> int:
    """Estimate number of tables from ``get_text("dict")`` output.

    Heuristic: count blocks whose lines have many spans with short text
    and consistent vertical alignment -- indicative of tabular structure.
    Also counts horizontal-rule patterns (``---|---``).
    """
    table_indicators = 0
    blocks = text_dict.get("blocks", [])
    for block in blocks:
        if block.get("type") != 0:  # type 0 = text, 1 = image
            continue
        lines = block.get("lines", [])
        if len(lines) < 2:
            continue
        # Check for multi-span lines (tabular hint)
        multi_span_lines = sum(
            1 for line in lines if len(line.get("spans", [])) >= 3
        )
        if multi_span_lines >= 2:
            table_indicators += 1
    return table_indicators


def _quick_classify(
    raw_text: str, text_density: float, image_count: int,
) -> str:
    """Fast page classification for skip candidates.

    Returns:
        "noise" | "cover" | "toc" | "normal"
    """
    stripped = raw_text.strip()

    # Noise: very little text and no images
    if len(stripped) < _NOISE_TEXT_MIN and image_count == 0:
        return "skip"

    # TOC: keyword match
    if any(kw in stripped for kw in _TOC_KEYWORDS):
        return "skip"

    # TOC: leader-dot heuristic
    lines = [l.strip() for l in stripped.split("\n") if l.strip()]
    if len(lines) >= 5:
        dot_lines = sum(1 for l in lines if _TOC_DOT_PATTERN.search(l))
        if dot_lines / len(lines) > 0.3:
            return "skip"

    # Cover: very low density + single image (first pages only)
    # Note: page_index not available here, so skip cover detection.
    # Cover pages are handled by page_classifier at runtime.

    return "normal"


def _build_fallback_chain(primary_method: str) -> tuple[str, ...]:
    """Build fallback chain based on primary method."""
    if primary_method == "pymupdf_text":
        return ("apple_vision_ocr", "vlm_full")
    if primary_method == "apple_vision_ocr":
        return ("vlm_full",)
    # vlm_full or skip: no fallback
    return ()


def _estimate_complexity(table_count: int, image_count: int) -> str:
    """Classify page complexity."""
    has_tables = table_count > _TABLE_COUNT_THRESHOLD
    has_images = image_count > _IMAGE_COUNT_THRESHOLD
    if has_tables and has_images:
        return "mixed"
    if has_tables:
        return "table_heavy"
    if has_images:
        return "image_heavy"
    return "simple"


__all__ = ["DocumentIntelligence"]
