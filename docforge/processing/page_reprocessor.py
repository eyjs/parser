"""Page reprocessor — quality-based retry with escalated strategies (A4).

After the initial parallel page processing completes, this module
identifies low-confidence pages and reprocesses them with progressively
more aggressive strategies (e.g. forced OCR, full layout detection).

The reprocessor selects the best result across attempts based on
``PageConfidence.overall`` and returns it in place of the original.

Design constraints:
  * Pure functions: no side effects beyond logging.
  * Immutable domain: ``PageResult`` is frozen; we return new instances.
  * Opt-out: caller checks ``config.page_reprocess_enabled`` before calling.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docforge.usecases.page_processor import PageResult

logger = logging.getLogger(__name__)


def should_reprocess(result: "PageResult", threshold: float) -> bool:
    """Return True if the page's confidence is below *threshold*.

    Pages with no content (None) or no confidence score are skipped
    (they are already error/noise pages).
    """
    if result.page_content is None:
        return False
    confidence = result.page_content.confidence
    if confidence is None:
        return False
    return confidence.overall < threshold


def escalate_strategy(attempt: int) -> dict[str, object]:
    """Return escalated processing hints for the given retry attempt (1-based).

    Attempt 1: Force OCR on all blocks (re-extract via image pipeline).
    Attempt 2: Force OCR + enable layout detection on all pages.

    Returns a dict of keyword overrides that the caller can merge
    into the ``PageProcessor`` construction or ``process()`` call.
    """
    if attempt <= 1:
        return {
            "force_ocr": True,
            "layout_detection_all_pages": False,
        }
    # attempt >= 2
    return {
        "force_ocr": True,
        "layout_detection_all_pages": True,
    }


def select_best_result(results: list["PageResult"]) -> "PageResult":
    """Return the result with the highest overall confidence.

    If all results lack confidence (all None), the first result is
    returned unchanged. Ties are broken by position (earlier is preferred).
    """
    if not results:
        raise ValueError("select_best_result requires at least one result")

    best = results[0]
    best_score = _overall_score(best)

    for candidate in results[1:]:
        score = _overall_score(candidate)
        if score > best_score:
            best = candidate
            best_score = score

    return best


def _overall_score(result: "PageResult") -> float:
    """Extract the overall confidence score, defaulting to -1 for missing."""
    if result.page_content is None:
        return -1.0
    if result.page_content.confidence is None:
        return -1.0
    return result.page_content.confidence.overall
