"""Document type auto-detection router.

Classifies a PDF document into one of three types based on keyword
frequency and metadata heuristics:

  - ``GENERAL``:       default; no strong domain signal
  - ``LEGAL_KOREAN``:  Korean legal/insurance/contract documents
  - ``ACADEMIC``:      English/Korean academic papers

Sprint 7 delivers detection logic only; pipeline integration is
deferred to Sprint 8 (C4 domain_hint auto-setting).
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fitz  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class DocumentType(str, Enum):
    """Supported document type categories."""

    GENERAL = "general"
    LEGAL_KOREAN = "legal_korean"
    ACADEMIC = "academic"


# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------

# Korean legal keywords — drawn from domain_profiles/korean_legal.py
_LEGAL_KOREAN_KEYWORDS: tuple[str, ...] = (
    "약관",
    "조항",
    "제1조",
    "제2조",
    "제3조",
    "계약",
    "법률",
    "시행령",
    "시행규칙",
    "갑",
    "을",
    "보험",
    "보험금",
    "피보험자",
    "보험계약자",
    "면책",
    "해지",
    "배상",
    "손해",
    "조항",
    "별표",
    "부칙",
)

# Academic keywords (English + Korean)
_ACADEMIC_KEYWORDS: tuple[str, ...] = (
    "abstract",
    "references",
    "introduction",
    "methodology",
    "conclusion",
    "et al.",
    "doi",
    "fig.",
    "table",
    "acknowledgment",
    "acknowledgement",
    "bibliography",
    "hypothesis",
    "literature review",
    "results",
    "discussion",
    "appendix",
    # Korean academic
    "초록",
    "참고문헌",
    "서론",
    "결론",
    "연구방법",
)

# Minimum keyword density to trigger classification
LEGAL_THRESHOLD = 0.002
ACADEMIC_THRESHOLD = 0.002

# Maximum pages to sample for text extraction
_MAX_SAMPLE_PAGES = 10


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class DocumentTypeRouter:
    """Detects document type from a fitz.Document.

    Samples the first N pages, counts keyword matches, and returns
    the type with the highest density above its threshold.
    """

    def detect(self, doc: "fitz.Document") -> DocumentType:
        """Classify the document type.

        Args:
            doc: An open ``fitz.Document`` instance.

        Returns:
            The detected :class:`DocumentType`.
        """
        try:
            return self._detect_impl(doc)
        except Exception:
            logger.warning(
                "DocumentTypeRouter.detect() failed, defaulting to GENERAL",
                exc_info=True,
            )
            return DocumentType.GENERAL

    def _detect_impl(self, doc: "fitz.Document") -> DocumentType:
        total_pages = len(doc)
        if total_pages == 0:
            return DocumentType.GENERAL

        # Sample text from first N pages
        sample_pages = min(_MAX_SAMPLE_PAGES, total_pages)
        text_parts: list[str] = []
        for page_idx in range(sample_pages):
            try:
                page = doc[page_idx]
                text_parts.append(page.get_text())
            except Exception:
                continue

        combined_text = "\n".join(text_parts)
        if not combined_text.strip():
            return DocumentType.GENERAL

        text_lower = combined_text.lower()
        text_length = len(text_lower)

        # Count keyword matches
        legal_count = self._count_keywords(combined_text, text_lower, _LEGAL_KOREAN_KEYWORDS)
        academic_count = self._count_keywords(combined_text, text_lower, _ACADEMIC_KEYWORDS)

        # Compute densities
        legal_density = legal_count / text_length if text_length > 0 else 0.0
        academic_density = academic_count / text_length if text_length > 0 else 0.0

        # Check metadata for additional signals
        metadata_boost = self._check_metadata(doc)
        if metadata_boost == DocumentType.LEGAL_KOREAN:
            legal_density *= 1.5
        elif metadata_boost == DocumentType.ACADEMIC:
            academic_density *= 1.5

        # Pick highest-scoring type above threshold
        scores: list[tuple[DocumentType, float, float]] = [
            (DocumentType.LEGAL_KOREAN, legal_density, LEGAL_THRESHOLD),
            (DocumentType.ACADEMIC, academic_density, ACADEMIC_THRESHOLD),
        ]

        best_type = DocumentType.GENERAL
        best_density = 0.0
        for doc_type, density, threshold in scores:
            if density >= threshold and density > best_density:
                best_type = doc_type
                best_density = density

        logger.debug(
            "DocumentTypeRouter: legal=%.4f academic=%.4f -> %s",
            legal_density, academic_density, best_type.value,
        )
        return best_type

    @staticmethod
    def _count_keywords(
        original_text: str,
        lower_text: str,
        keywords: tuple[str, ...],
    ) -> int:
        """Count total keyword occurrences in text.

        Uses the original text for Korean keywords (case-sensitive)
        and lower-cased text for English keywords.
        """
        count = 0
        for keyword in keywords:
            if keyword == keyword.lower() and any(c.isascii() and c.isalpha() for c in keyword):
                # English keyword: use case-insensitive match
                count += lower_text.count(keyword.lower())
            else:
                # Korean/mixed keyword: case-sensitive
                count += original_text.count(keyword)
        return count

    @staticmethod
    def _check_metadata(doc: "fitz.Document") -> DocumentType | None:
        """Check document metadata for type signals.

        Returns a DocumentType hint if metadata strongly suggests a
        type, or None if inconclusive.
        """
        try:
            metadata = doc.metadata or {}
        except Exception:
            return None

        title = (metadata.get("title") or "").lower()
        subject = (metadata.get("subject") or "").lower()
        combined = f"{title} {subject}"

        legal_signals = ("약관", "보험", "계약", "법률", "contract", "insurance", "terms")
        academic_signals = ("paper", "thesis", "dissertation", "journal", "논문", "학술")

        if any(sig in combined for sig in legal_signals):
            return DocumentType.LEGAL_KOREAN
        if any(sig in combined for sig in academic_signals):
            return DocumentType.ACADEMIC

        return None


__all__ = ["DocumentType", "DocumentTypeRouter"]
