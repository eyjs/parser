"""Tests for parsing quality metrics calculation."""

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import (
    NoiseStats,
    PageContent,
    Table,
    TableCell,
    TextBlock,
)
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.quality_metrics import calculate_metrics, detect_anomalies


def _make_page(
    page_num: int,
    block_count: int = 3,
    table_count: int = 0,
    tables_need_review: int = 0,
) -> PageContent:
    """Helper to create a PageContent with dummy data."""
    blocks = tuple(
        TextBlock(
            text=f"Block {i} content",
            bbox=BBox(x0=50.0, y0=100.0 + i * 20, x1=500.0, y1=112.0 + i * 20),
            font=FontInfo(name="Arial", size=10.0, is_bold=False),
        )
        for i in range(block_count)
    )

    tables = tuple(
        Table(
            cells=(TableCell(text="cell", row=0, col=0),),
            rows=2,
            cols=2,
            bbox=BBox(x0=50.0, y0=400.0, x1=500.0, y1=500.0),
            needs_review=(i < tables_need_review),
        )
        for i in range(table_count)
    )

    return PageContent(
        page_num=page_num,
        page_type=PageType.DIGITAL,
        blocks=blocks,
        tables=tables,
        raw_text="Sample text content",
    )


class TestMetricsCalculation:
    """Test quality metrics calculation."""

    def test_basic_metrics(self) -> None:
        pages = [_make_page(1, block_count=5), _make_page(2, block_count=3)]
        markdown = "## Heading\n\nSome content\n\nMore content\n"
        noise = NoiseStats(headers=10, footers=10, page_numbers=2)

        stats = calculate_metrics(pages, markdown, noise, 1000.0)

        assert stats.total_pages == 2
        assert stats.parsed_pages == 2
        assert stats.text_blocks == 8
        assert stats.heading_count == 1
        assert stats.parse_time_ms == 1000.0
        assert stats.noise_removed.headers == 10

    def test_table_metrics(self) -> None:
        pages = [_make_page(1, table_count=3, tables_need_review=1)]
        markdown = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        noise = NoiseStats()

        stats = calculate_metrics(pages, markdown, noise, 500.0)

        assert stats.tables_found == 3
        assert stats.tables_need_review == 1


class TestAnomalyDetection:
    """Test quality anomaly detection."""

    def test_no_headings_warning(self) -> None:
        pages = [_make_page(1)]
        markdown = "Just plain text\n"
        noise = NoiseStats()
        stats = calculate_metrics(pages, markdown, noise, 100.0)

        warnings = detect_anomalies(stats)
        codes = [w.code for w in warnings]
        assert "NO_HEADINGS" in codes

    def test_high_empty_ratio_warning(self) -> None:
        pages = [_make_page(1)]
        markdown = "\n\n\n\ntext\n\n\n\n\n\n"
        noise = NoiseStats()
        stats = calculate_metrics(pages, markdown, noise, 100.0)

        warnings = detect_anomalies(stats)
        codes = [w.code for w in warnings]
        assert "HIGH_EMPTY_RATIO" in codes

    def test_tables_need_review(self) -> None:
        pages = [_make_page(1, table_count=2, tables_need_review=1)]
        markdown = "## Heading\nContent\n"
        noise = NoiseStats()
        stats = calculate_metrics(pages, markdown, noise, 100.0)

        warnings = detect_anomalies(stats)
        codes = [w.code for w in warnings]
        assert "TABLES_NEED_REVIEW" in codes

    def test_clean_result_no_warnings(self) -> None:
        pages = [_make_page(1), _make_page(2)]
        markdown = "## Heading\n\nContent line one\nContent line two\n"
        noise = NoiseStats()
        stats = calculate_metrics(pages, markdown, noise, 100.0)

        warnings = detect_anomalies(stats)
        # Should only have NO_HEADINGS if heading_count > 0
        assert stats.heading_count >= 1
        # No warnings about empty ratio or low parse rate
        codes = [w.code for w in warnings]
        assert "HIGH_EMPTY_RATIO" not in codes
        assert "LOW_PARSE_RATE" not in codes
