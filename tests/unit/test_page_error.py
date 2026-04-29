"""Tests for the PageError domain model and its surfacing on ParseResult.

Validates P0-1:
- PageError is a frozen, hashable dataclass with the documented fields.
- ParseResult.page_errors defaults to () for backward compatibility.
- ParseResult correctly carries a non-empty page_errors tuple when supplied.
"""

from __future__ import annotations

import dataclasses

import pytest

from docforge.domain.enums import DocumentComplexity
from docforge.domain.models import (
    Metadata,
    NoiseStats,
    PageError,
    ParseResult,
    ParseStats,
)
from docforge.domain.value_objects import DocumentProfile


def _make_minimal_result(page_errors: tuple[PageError, ...] = ()) -> ParseResult:
    metadata = Metadata(
        source="test.pdf",
        source_type="digital_pdf",
        pages=3,
        parsed_at="2026-04-29T00:00:00+09:00",
        parser_version="1.0.0",
        ocr_used=False,
        tables_extracted=0,
        tables_need_review=0,
        noise_removed=NoiseStats(),
    )
    return ParseResult(
        pages=(),
        markdown="",
        metadata=metadata,
        stats=ParseStats(total_pages=3, parsed_pages=0),
        profile=DocumentProfile(
            complexity=DocumentComplexity.TEXT_ONLY,
            recommended_parser="pymupdf",
            total_pages=3,
            text_pages=3,
            image_only_pages=0,
            total_chars=0,
            has_tables=False,
            avg_chars_per_page=0.0,
            image_area_ratio=0.0,
        ),
        page_errors=page_errors,
    )


class TestPageError:
    def test_is_frozen(self) -> None:
        err = PageError(page_number=4, error_type="ProcessingError", message="boom")
        with pytest.raises(dataclasses.FrozenInstanceError):
            err.page_number = 5  # type: ignore[misc]

    def test_required_fields(self) -> None:
        err = PageError(page_number=4, error_type="OCRError", message="timeout")
        assert err.page_number == 4
        assert err.error_type == "OCRError"
        assert err.message == "timeout"
        assert err.traceback is None

    def test_traceback_optional(self) -> None:
        err = PageError(
            page_number=5,
            error_type="ProcessingError",
            message="x",
            traceback="Traceback (most recent call last):\n...",
        )
        assert err.traceback is not None
        assert err.traceback.startswith("Traceback")

    def test_equality(self) -> None:
        a = PageError(page_number=4, error_type="E", message="m")
        b = PageError(page_number=4, error_type="E", message="m")
        assert a == b


class TestParseResultPageErrors:
    def test_defaults_to_empty_tuple(self) -> None:
        """Backward compat: existing code that builds ParseResult without
        page_errors must still work."""
        result = _make_minimal_result()
        assert result.page_errors == ()
        assert isinstance(result.page_errors, tuple)

    def test_carries_supplied_errors(self) -> None:
        errors = (
            PageError(page_number=4, error_type="ProcessingError", message="fail4"),
            PageError(page_number=5, error_type="ProcessingError", message="fail5"),
            PageError(page_number=6, error_type="OCRError", message="fail6"),
        )
        result = _make_minimal_result(page_errors=errors)
        assert len(result.page_errors) == 3
        assert [e.page_number for e in result.page_errors] == [4, 5, 6]
        assert result.page_errors[2].error_type == "OCRError"

    def test_page_errors_visible_after_construction(self) -> None:
        """The whole point of P0-1: errors must be reachable from ParseResult."""
        result = _make_minimal_result(page_errors=(
            PageError(page_number=4, error_type="X", message="m"),
        ))
        # ParseResult must expose page_errors as a public attribute (not buried).
        assert hasattr(result, "page_errors")
        assert result.page_errors[0].page_number == 4
