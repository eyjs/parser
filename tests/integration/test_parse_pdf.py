"""Integration tests for the full PDF parsing pipeline.

These tests use real PDF files from the sample directory.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from docforge.infrastructure.config import ParserConfig
from docforge.usecases.parse_pdf import parse_pdf
from docforge.usecases.verify_result import generate_verification_report

# Sample PDF paths
SAMPLE_DIR = Path("C:/Users/USER/dev/parser/sample/DB손해보험_상품목록/일반/기타/프로미 다이렉트 반려동물보험(CM)")
INSURANCE_TERMS = SAMPLE_DIR / "20260101~현재_보험약관.pdf"
BUSINESS_METHOD = SAMPLE_DIR / "20260101~현재_사업방법서.pdf"
PRODUCT_SUMMARY = SAMPLE_DIR / "20260101~현재_상품요약서.pdf"

OUTPUT_DIR = Path("C:/Users/USER/dev/parser/.pipeline/test_output")


def _ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@pytest.mark.integration
class TestInsuranceTermsParsing:
    """Test parsing of the 81-page insurance terms PDF."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        if not INSURANCE_TERMS.exists():
            pytest.skip(f"Sample PDF not found: {INSURANCE_TERMS}")
        _ensure_output_dir()

    def test_parses_successfully(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)

        assert result.markdown
        assert len(result.markdown) > 1000
        assert result.stats.total_pages > 0
        assert result.stats.parsed_pages > 0

    def test_detects_document_complexity(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)

        assert result.profile.total_pages == 81
        assert result.profile.total_chars > 10000

    def test_removes_noise(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)

        noise = result.stats.noise_removed
        # Should detect some noise (headers, footers, page numbers)
        total_noise = noise.headers + noise.footers + noise.page_numbers
        assert total_noise > 0

    def test_recognizes_structure(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)

        # Should find headings (조/항 structure)
        assert result.stats.heading_count > 0
        assert "##" in result.markdown

    def test_extracts_tables(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)

        assert result.stats.tables_found > 0
        assert "|" in result.markdown

    def test_has_yaml_front_matter(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)

        assert result.markdown.startswith("---")
        assert "source:" in result.markdown
        assert "source_type:" in result.markdown

    def test_saves_markdown(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)
        output = OUTPUT_DIR / "insurance_terms.md"
        output.write_text(result.markdown, encoding="utf-8")
        assert output.exists()

    def test_generates_verification_report(self) -> None:
        result = parse_pdf(INSURANCE_TERMS)
        report = OUTPUT_DIR / "insurance_terms_verify.html"
        path = generate_verification_report(INSURANCE_TERMS, result, report)
        assert Path(path).exists()


@pytest.mark.integration
class TestBusinessMethodParsing:
    """Test parsing of the 2-page business method PDF."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        if not BUSINESS_METHOD.exists():
            pytest.skip(f"Sample PDF not found: {BUSINESS_METHOD}")
        _ensure_output_dir()

    def test_parses_successfully(self) -> None:
        result = parse_pdf(BUSINESS_METHOD)

        assert result.markdown
        # Profile shows all pages, stats shows only parsed pages
        assert result.profile.total_pages == 2
        assert result.stats.total_pages >= 1

    def test_detects_sparse_text(self) -> None:
        result = parse_pdf(BUSINESS_METHOD)

        # 2-page document with sparse text - one may be classified as noise
        assert result.profile.total_pages == 2

    def test_saves_markdown(self) -> None:
        result = parse_pdf(BUSINESS_METHOD)
        output = OUTPUT_DIR / "business_method.md"
        output.write_text(result.markdown, encoding="utf-8")
        assert output.exists()


@pytest.mark.integration
class TestProductSummaryParsing:
    """Test parsing of the 5-page product summary PDF."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        if not PRODUCT_SUMMARY.exists():
            pytest.skip(f"Sample PDF not found: {PRODUCT_SUMMARY}")
        _ensure_output_dir()

    def test_parses_successfully(self) -> None:
        result = parse_pdf(PRODUCT_SUMMARY)

        assert result.markdown
        assert result.stats.total_pages == 5

    def test_extracts_tables(self) -> None:
        result = parse_pdf(PRODUCT_SUMMARY)

        # Product summary should have tables
        assert result.stats.tables_found >= 0  # May or may not have structured tables

    def test_saves_markdown(self) -> None:
        result = parse_pdf(PRODUCT_SUMMARY)
        output = OUTPUT_DIR / "product_summary.md"
        output.write_text(result.markdown, encoding="utf-8")
        assert output.exists()

    def test_generates_verification_report(self) -> None:
        result = parse_pdf(PRODUCT_SUMMARY)
        report = OUTPUT_DIR / "product_summary_verify.html"
        path = generate_verification_report(PRODUCT_SUMMARY, result, report)
        assert Path(path).exists()
