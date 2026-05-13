"""Tests for DocumentTypeRouter (Sprint 7 P1).

Verifies classification of 3 document types: GENERAL, LEGAL_KOREAN,
ACADEMIC, plus edge cases (empty document, corrupt metadata).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from docforge.processing.document_type_router import (
    DocumentType,
    DocumentTypeRouter,
)


def _make_fitz_doc(
    pages_text: list[str],
    metadata: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock fitz.Document with given page texts."""
    doc = MagicMock()
    doc.__len__ = lambda self: len(pages_text)
    doc.metadata = metadata or {}

    pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text.return_value = text
        pages.append(page)

    doc.__getitem__ = lambda self, idx: pages[idx]
    return doc


class TestDocumentTypeRouterLegalKorean:
    """LEGAL_KOREAN detection via keyword frequency."""

    def test_legal_keywords_detected(self) -> None:
        text = (
            "제1조 (목적) 이 약관은 보험계약에 관한 사항을 규정합니다.\n"
            "제2조 (정의) 보험계약자는 계약을 체결하고 보험료를 납입하는 자를 말합니다.\n"
            "제3조 (보험금의 지급) 피보험자가 보험기간 중 사망한 경우 보험금을 지급합니다.\n"
            "보험계약의 해지 및 면책 조항에 관한 사항은 별표를 참조하세요.\n"
        ) * 5  # Repeat to ensure sufficient density
        doc = _make_fitz_doc([text])
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.LEGAL_KOREAN

    def test_legal_metadata_boost(self) -> None:
        text = "제1조 약관 보험 계약"
        doc = _make_fitz_doc([text], metadata={"title": "보험 약관"})
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.LEGAL_KOREAN


class TestDocumentTypeRouterAcademic:
    """ACADEMIC detection via keyword frequency."""

    def test_academic_keywords_detected(self) -> None:
        text = (
            "Abstract\n"
            "This paper presents a novel methodology for text extraction. "
            "In the introduction, we discuss the literature review and hypothesis. "
            "Our results show significant improvement over baseline methods. "
            "The discussion section analyzes the findings in detail. "
            "In conclusion, we demonstrate the effectiveness of our approach.\n"
            "References\n"
            "[1] Smith et al., Journal of AI, 2024, doi:10.1234/example\n"
            "[2] Kim et al., ACL Proceedings, 2023, doi:10.5678/sample\n"
        ) * 5  # Repeat to ensure sufficient density
        doc = _make_fitz_doc([text])
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.ACADEMIC

    def test_academic_metadata_boost(self) -> None:
        text = "Abstract Introduction References"
        doc = _make_fitz_doc([text] * 3, metadata={"title": "A Research Paper on NLP"})
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.ACADEMIC


class TestDocumentTypeRouterGeneral:
    """GENERAL detection when no domain signal is strong enough."""

    def test_generic_text(self) -> None:
        text = (
            "회사 소개서\n"
            "우리 회사는 2020년에 설립되었습니다.\n"
            "주요 사업 분야는 소프트웨어 개발입니다.\n"
            "직원 수는 약 100명이며 서울에 본사를 두고 있습니다.\n"
        )
        doc = _make_fitz_doc([text])
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.GENERAL

    def test_empty_document(self) -> None:
        doc = _make_fitz_doc([])
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.GENERAL

    def test_empty_text_pages(self) -> None:
        doc = _make_fitz_doc(["", "", ""])
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.GENERAL

    def test_exception_returns_general(self) -> None:
        """If detection fails entirely, gracefully return GENERAL."""
        doc = MagicMock()
        doc.__len__ = MagicMock(side_effect=RuntimeError("corrupt"))
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.GENERAL


class TestDocumentTypeRouterEdgeCases:
    """Edge case handling."""

    def test_mixed_signals_picks_stronger(self) -> None:
        """When both legal and academic signals exist, pick the stronger one."""
        # Heavy legal, light academic
        text = (
            "제1조 약관 보험 계약 해지 면책 피보험자 보험계약자\n"
            "제2조 보험금 손해배상 갑 을 계약\n"
            "abstract references\n"
        ) * 5
        doc = _make_fitz_doc([text])
        router = DocumentTypeRouter()
        result = router.detect(doc)
        assert result == DocumentType.LEGAL_KOREAN

    def test_corrupt_metadata_handled(self) -> None:
        doc = _make_fitz_doc(["normal text"])
        doc.metadata = None  # corrupt
        router = DocumentTypeRouter()
        # Should not raise
        result = router.detect(doc)
        assert isinstance(result, DocumentType)
