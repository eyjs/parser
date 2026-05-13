"""C3 Sprint 7: Single-pass learn_noise_and_stats verification.

Verifies that ``learn_noise_and_stats`` iterates pages exactly once
(not 2x as the old separate learn_noise + doc_stats did) and produces
equivalent results.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, call

import pytest

from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig


def _make_block(text: str, cy: float, font_size: float) -> TextBlock:
    """Create a minimal TextBlock with the given center_y and font size."""
    return TextBlock(
        text=text,
        bbox=BBox(0.0, cy - 5, 100.0, cy + 5),
        font=FontInfo(name="Test", size=font_size, is_bold=False),
        block_type=__import__(
            "docforge.domain.enums", fromlist=["BlockType"],
        ).BlockType.TEXT,
        heading_level=0,
    )


def _make_mock_reader(total_pages: int, blocks_per_page: list[list[TextBlock]]):
    """Create a mock reader that tracks page access counts."""
    reader = MagicMock()

    call_count = {"extract_text_blocks": 0, "get_page_dimensions": 0}

    def extract_text_blocks(doc, page_idx):
        call_count["extract_text_blocks"] += 1
        if page_idx < len(blocks_per_page):
            return blocks_per_page[page_idx]
        return []

    def get_page_dimensions(doc, page_idx):
        call_count["get_page_dimensions"] += 1
        return (595.0, 842.0)  # A4 size

    def get_line_gaps(doc, page_idx):
        return [5.0, 6.0]

    reader.extract_text_blocks = MagicMock(side_effect=extract_text_blocks)
    reader.get_page_dimensions = MagicMock(side_effect=get_page_dimensions)
    reader.get_line_gaps = MagicMock(side_effect=get_line_gaps)
    reader.get_font_sizes = MagicMock(return_value=[10.0, 12.0, 14.0])
    reader._call_count = call_count

    return reader


class TestLearnNoiseAndStatsSinglePass:
    """C3: learn_noise_and_stats iterates pages exactly once."""

    def test_page_access_count_equals_total_pages(self) -> None:
        """Each page should be accessed exactly once for text blocks."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        total_pages = 5
        blocks_per_page = [
            [_make_block(f"line {i}", cy=100.0 + i * 20, font_size=10.0)]
            for i in range(total_pages)
        ]
        reader = _make_mock_reader(total_pages, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        learn_noise_and_stats(reader, doc, total_pages, config)

        # extract_text_blocks called exactly total_pages times (NOT 2x)
        assert reader.extract_text_blocks.call_count == total_pages

    def test_page_dimensions_accessed_once_per_page(self) -> None:
        """get_page_dimensions should be called exactly once per page."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        total_pages = 8
        blocks_per_page = [
            [_make_block("text", cy=200.0, font_size=12.0)]
            for _ in range(total_pages)
        ]
        reader = _make_mock_reader(total_pages, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        learn_noise_and_stats(reader, doc, total_pages, config)

        assert reader.get_page_dimensions.call_count == total_pages

    def test_line_gaps_sampled_only_first_10_pages(self) -> None:
        """Line gaps should only be collected from first min(10, total) pages."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        total_pages = 20
        blocks_per_page = [
            [_make_block("text", cy=200.0, font_size=10.0)]
            for _ in range(total_pages)
        ]
        reader = _make_mock_reader(total_pages, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        learn_noise_and_stats(reader, doc, total_pages, config)

        # get_line_gaps should be called for first 10 pages only
        assert reader.get_line_gaps.call_count == 10

    def test_line_gaps_all_pages_when_fewer_than_10(self) -> None:
        """When total_pages < 10, all pages contribute line gaps."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        total_pages = 3
        blocks_per_page = [
            [_make_block("text", cy=200.0, font_size=10.0)]
            for _ in range(total_pages)
        ]
        reader = _make_mock_reader(total_pages, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        learn_noise_and_stats(reader, doc, total_pages, config)

        assert reader.get_line_gaps.call_count == total_pages

    def test_returns_correct_avg_font_size(self) -> None:
        """Avg font size is computed from all blocks across all pages."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        blocks_per_page = [
            [_make_block("a", cy=100.0, font_size=10.0)],
            [_make_block("b", cy=100.0, font_size=20.0)],
        ]
        reader = _make_mock_reader(2, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        _, avg_font_size, _ = learn_noise_and_stats(reader, doc, 2, config)

        assert avg_font_size == pytest.approx(15.0)

    def test_returns_correct_avg_line_gap(self) -> None:
        """Avg line gap is computed from sampled pages."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        blocks_per_page = [
            [_make_block("text", cy=100.0, font_size=10.0)]
            for _ in range(3)
        ]
        reader = _make_mock_reader(3, blocks_per_page)
        # Each page returns [5.0, 6.0] from get_line_gaps
        doc = MagicMock()
        config = ParserConfig()

        _, _, avg_line_gap = learn_noise_and_stats(reader, doc, 3, config)

        # 3 pages * [5.0, 6.0] = [5,6,5,6,5,6] => avg = 5.5
        assert avg_line_gap == pytest.approx(5.5)

    def test_empty_document_returns_defaults(self) -> None:
        """Zero-page document returns default font size and line gap."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        reader = _make_mock_reader(0, [])
        doc = MagicMock()
        config = ParserConfig()

        patterns, avg_font_size, avg_line_gap = learn_noise_and_stats(
            reader, doc, 0, config,
        )

        assert avg_font_size == 10.0  # default
        assert avg_line_gap == 5.0    # default
        assert reader.extract_text_blocks.call_count == 0

    def test_zero_font_size_blocks_excluded(self) -> None:
        """Blocks with font_size=0 should not contribute to avg_font_size."""
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        blocks_per_page = [
            [
                _make_block("visible", cy=100.0, font_size=12.0),
                _make_block("invisible", cy=200.0, font_size=0.0),
            ],
        ]
        reader = _make_mock_reader(1, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        _, avg_font_size, _ = learn_noise_and_stats(reader, doc, 1, config)

        # Only the 12.0 block counts
        assert avg_font_size == pytest.approx(12.0)

    def test_no_double_iteration(self) -> None:
        """Compared to old learn_noise + doc_stats, access count is halved.

        The old approach would call extract_text_blocks N times in
        learn_noise, then again in doc_stats (via get_font_sizes which
        iterates all pages). The new single-pass calls it exactly N times.
        """
        from docforge.usecases._parse_pdf_helpers import learn_noise_and_stats

        total_pages = 15
        blocks_per_page = [
            [_make_block(f"page {i}", cy=100.0, font_size=10.0 + i)]
            for i in range(total_pages)
        ]
        reader = _make_mock_reader(total_pages, blocks_per_page)
        doc = MagicMock()
        config = ParserConfig()

        learn_noise_and_stats(reader, doc, total_pages, config)

        # Critical assertion: exactly N calls, not 2N
        assert reader.extract_text_blocks.call_count == total_pages
        # get_font_sizes should NOT be called (single-pass collects inline)
        reader.get_font_sizes.assert_not_called()
