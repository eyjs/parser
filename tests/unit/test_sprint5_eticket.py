"""Sprint 5: e-ticket parsing improvements — unit tests.

Tests for:
1. Mid-line bullet conversion (task-001)
2. Word-grid table reconstruction + Strategy 4 (task-002)
3. Table.source field + filter bypass (task-003)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from docforge.adapters.pdfplumber_tables import (
    PdfplumberTableExtractor,
    _assign_words_to_columns,
    _detect_column_boundaries,
    _group_words_by_row,
    _has_airline_pattern,
)
from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import (
    PageContent,
    Table,
    TableCell,
    TextBlock,
)
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.markdown_assembler import (
    _convert_unicode_bullets,
    assemble_page,
    table_to_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(
    text: str,
    y0: float = 100.0,
    block_type: BlockType = BlockType.TEXT,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=50.0, y0=y0, x1=500.0, y1=y0 + 12.0),
        font=FontInfo(name="Arial", size=10.0, is_bold=False),
        block_type=block_type,
    )


def _make_word(text: str, x0: float, x1: float, top: float, bottom: float) -> dict:
    """Create a pdfplumber-style word dict."""
    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
    }


# ---------------------------------------------------------------------------
# Task-001: Mid-line bullet conversion
# ---------------------------------------------------------------------------

class TestMidLineBulletConversion:
    """Test that bullets embedded mid-line are split and converted."""

    def test_mid_line_bullet_split(self) -> None:
        """Bullets in the middle of a line should be split to separate list items."""
        text = "안내사항 •항목1 •항목2"
        result = _convert_unicode_bullets(text)
        assert "- 항목1" in result
        assert "- 항목2" in result
        assert "•" not in result

    def test_line_start_bullet_unchanged(self) -> None:
        """Bullets already at line start should still convert normally."""
        text = "•첫번째\n•두번째"
        result = _convert_unicode_bullets(text)
        assert "- 첫번째" in result
        assert "- 두번째" in result
        assert "•" not in result

    def test_sub_bullet_mid_line(self) -> None:
        """Sub-bullets (circle) mid-line should be split and indented."""
        text = "설명 ○하위1 ○하위2"
        result = _convert_unicode_bullets(text)
        assert "  - 하위1" in result
        assert "  - 하위2" in result
        assert "○" not in result

    def test_mixed_bullets(self) -> None:
        """Main and sub-bullets mixed in same text."""
        text = "•메인항목 ○하위항목"
        result = _convert_unicode_bullets(text)
        assert "- 메인항목" in result
        assert "  - 하위항목" in result

    def test_no_bullets_unchanged(self) -> None:
        """Text without bullets should pass through unchanged."""
        text = "이것은 일반 텍스트입니다."
        result = _convert_unicode_bullets(text)
        assert result == text

    def test_filled_circle_bullet_mid_line(self) -> None:
        """Filled circle (●) mid-line should also be split."""
        text = "안내 ●항목A ●항목B"
        result = _convert_unicode_bullets(text)
        assert "- 항목A" in result
        assert "- 항목B" in result
        assert "●" not in result

    def test_bullet_in_assembled_page(self) -> None:
        """End-to-end: mid-line bullets in a TextBlock are converted in assembled page."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(
                _make_block("드리는 말씀 •여행 중 여권을 분실하지 않도록 •탑승 시간을 확인하세요"),
            ),
            tables=(),
            raw_text="",
            height=800.0,
        )
        md = assemble_page(page, 10.0, config)
        assert "- 여행 중" in md
        assert "- 탑승 시간을" in md
        assert "•" not in md


# ---------------------------------------------------------------------------
# Task-002: Word-grid reconstruction
# ---------------------------------------------------------------------------

class TestAirlinePatternDetection:
    """Test airline pattern detection."""

    def test_positive_airline_pattern(self) -> None:
        """Words containing airline patterns should be detected."""
        words = [
            _make_word("OZ", 10, 30, 100, 112),
            _make_word("545", 32, 55, 100, 112),
            _make_word("ICN", 100, 130, 100, 112),
            _make_word("PRG", 200, 230, 100, 112),
            _make_word("TERMINAL", 300, 380, 100, 112),
            _make_word("ECONOMY", 400, 470, 100, 112),
        ]
        assert _has_airline_pattern(words)

    def test_negative_general_text(self) -> None:
        """General text without airline patterns should not trigger."""
        words = [
            _make_word("보험계약자는", 10, 80, 100, 112),
            _make_word("보험료를", 90, 140, 100, 112),
            _make_word("납입하고", 150, 200, 100, 112),
        ]
        assert not _has_airline_pattern(words)

    def test_empty_words(self) -> None:
        """Empty word list should return False."""
        assert not _has_airline_pattern([])

    def test_korean_airline_terms(self) -> None:
        """Korean airline terms should also be detected."""
        words = [
            _make_word("편명", 10, 40, 100, 112),
            _make_word("출발", 50, 80, 100, 112),
            _make_word("도착", 90, 120, 100, 112),
            _make_word("탑승", 130, 160, 100, 112),
        ]
        assert _has_airline_pattern(words)


class TestGroupWordsByRow:
    """Test word-to-row grouping by y-coordinate."""

    def test_same_row_words(self) -> None:
        """Words on the same y-line should be grouped together."""
        words = [
            _make_word("A", 10, 20, 100.0, 112.0),
            _make_word("B", 50, 60, 100.5, 112.5),  # within tolerance
            _make_word("C", 100, 110, 101.0, 113.0),
        ]
        rows = _group_words_by_row(words, y_tolerance=3.0)
        assert len(rows) == 1
        assert len(rows[0]) == 3

    def test_multiple_rows(self) -> None:
        """Words on different y-lines should be in different rows."""
        words = [
            _make_word("Row1A", 10, 40, 100, 112),
            _make_word("Row1B", 50, 80, 100, 112),
            _make_word("Row2A", 10, 40, 130, 142),
            _make_word("Row2B", 50, 80, 130, 142),
        ]
        rows = _group_words_by_row(words, y_tolerance=3.0)
        assert len(rows) == 2
        assert len(rows[0]) == 2
        assert len(rows[1]) == 2

    def test_sorted_by_x_within_row(self) -> None:
        """Words within a row should be sorted by x-coordinate."""
        words = [
            _make_word("C", 100, 110, 100, 112),
            _make_word("A", 10, 20, 100, 112),
            _make_word("B", 50, 60, 100, 112),
        ]
        rows = _group_words_by_row(words, y_tolerance=3.0)
        texts = [w["text"] for w in rows[0]]
        assert texts == ["A", "B", "C"]

    def test_empty_words(self) -> None:
        """Empty list returns empty result."""
        assert _group_words_by_row([], y_tolerance=3.0) == []


class TestDetectColumnBoundaries:
    """Test column boundary detection from word positions."""

    def test_three_column_layout(self) -> None:
        """Three clearly separated column groups should yield 2 boundaries."""
        rows = [
            [
                _make_word("A", 10, 50, 100, 112),
                _make_word("B", 200, 250, 100, 112),
                _make_word("C", 400, 450, 100, 112),
            ],
            [
                _make_word("D", 15, 55, 130, 142),
                _make_word("E", 205, 255, 130, 142),
                _make_word("F", 405, 455, 130, 142),
            ],
        ]
        boundaries = _detect_column_boundaries(rows, page_width=600.0)
        assert len(boundaries) == 2

    def test_no_clear_columns(self) -> None:
        """Continuous text with no gaps yields no boundaries."""
        rows = [
            [
                _make_word("A", 10, 25, 100, 112),
                _make_word("B", 26, 40, 100, 112),
                _make_word("C", 41, 55, 100, 112),
            ],
        ]
        boundaries = _detect_column_boundaries(rows, page_width=600.0)
        assert len(boundaries) == 0


class TestAssignWordsToColumns:
    """Test word-to-column assignment."""

    def test_three_column_assignment(self) -> None:
        """Words should be assigned to correct column slots."""
        words = [
            _make_word("OZ 545", 10, 70, 100, 112),
            _make_word("ICN", 200, 230, 100, 112),
            _make_word("14:00", 400, 440, 100, 112),
        ]
        boundaries = [150.0, 350.0]  # 3 columns
        cells = _assign_words_to_columns(words, boundaries)
        assert len(cells) == 3
        assert "OZ 545" in cells[0]
        assert "ICN" in cells[1]
        assert "14:00" in cells[2]

    def test_empty_columns_get_empty_string(self) -> None:
        """Columns with no words should have empty string cells."""
        words = [
            _make_word("Only", 10, 50, 100, 112),
        ]
        boundaries = [150.0, 350.0]  # 3 columns
        cells = _assign_words_to_columns(words, boundaries)
        assert cells[0] == "Only"
        assert cells[1] == ""
        assert cells[2] == ""


class TestReconstructWordGrid:
    """Test the full _reconstruct_word_grid method."""

    def setup_method(self) -> None:
        self.config = ParserConfig()
        self.extractor = PdfplumberTableExtractor(self.config)

    def test_creates_table_from_airline_words(self) -> None:
        """Word grid with airline patterns should produce a table."""
        mock_page = MagicMock()
        mock_page.width = 600.0
        mock_page.height = 800.0
        mock_page.extract_words.return_value = [
            _make_word("OZ", 10, 30, 100, 112),
            _make_word("545", 32, 55, 100, 112),
            _make_word("ICN", 200, 230, 100, 112),
            _make_word("PRG", 400, 430, 100, 112),
            _make_word("TERMINAL", 10, 80, 130, 142),
            _make_word("2", 85, 95, 130, 142),
            _make_word("ECONOMY", 200, 270, 130, 142),
            _make_word("GATE", 400, 440, 130, 142),
            _make_word("12", 445, 460, 130, 142),
        ]

        tables = self.extractor._reconstruct_word_grid(mock_page, 600.0, 800.0)
        assert len(tables) == 1
        table = tables[0]
        assert table.source == "word_grid"
        assert table.rows >= 2
        assert table.cols >= 3

    def test_no_table_for_general_text(self) -> None:
        """General text (no airline patterns) should not produce a table."""
        mock_page = MagicMock()
        mock_page.extract_words.return_value = [
            _make_word("보험계약자는", 10, 80, 100, 112),
            _make_word("보험료를", 90, 140, 100, 112),
            _make_word("납입하고", 150, 200, 120, 132),
        ]

        tables = self.extractor._reconstruct_word_grid(mock_page, 600.0, 800.0)
        assert tables == []

    def test_no_table_for_few_columns(self) -> None:
        """Two columns (below minimum 3) should not produce a table."""
        mock_page = MagicMock()
        mock_page.extract_words.return_value = [
            _make_word("OZ 545", 10, 70, 100, 112),
            _make_word("ICN", 80, 110, 100, 112),
            _make_word("TERMINAL", 10, 80, 130, 142),
            _make_word("ECONOMY", 80, 150, 130, 142),
        ]

        tables = self.extractor._reconstruct_word_grid(mock_page, 600.0, 800.0)
        assert tables == []

    def test_extract_words_failure_graceful(self) -> None:
        """extract_words raising an exception should return empty list."""
        mock_page = MagicMock()
        mock_page.extract_words.side_effect = RuntimeError("fail")

        tables = self.extractor._reconstruct_word_grid(mock_page, 600.0, 800.0)
        assert tables == []


class TestStrategy4InExtractFromPage:
    """Test that Strategy 4 is triggered when Strategies 1-3 find nothing."""

    def setup_method(self) -> None:
        self.config = ParserConfig()
        self.extractor = PdfplumberTableExtractor(self.config)

    def test_strategy4_triggered_when_no_tables(self) -> None:
        """When all strategies fail, Strategy 4 should be invoked."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.width = 600.0
        mock_page.height = 800.0
        mock_page.find_tables.return_value = []  # Strategy 1/2 fail
        mock_page.extract_words.return_value = [
            _make_word("OZ", 10, 30, 100, 112),
            _make_word("545", 32, 55, 100, 112),
            _make_word("ICN", 200, 230, 100, 112),
            _make_word("PRG", 400, 430, 100, 112),
            _make_word("TERMINAL", 10, 80, 130, 142),
            _make_word("2", 85, 95, 130, 142),
            _make_word("ECONOMY", 200, 270, 130, 142),
            _make_word("GATE", 400, 440, 130, 142),
            _make_word("12", 445, 460, 130, 142),
        ]
        mock_doc.pages = [mock_page]

        tables = self.extractor.extract_from_page(mock_doc, 0)
        # Should find at least one table from word-grid
        assert len(tables) >= 1
        assert tables[0].source == "word_grid"


# ---------------------------------------------------------------------------
# Task-003: Table.source field + filter bypass
# ---------------------------------------------------------------------------

class TestTableSourceField:
    """Test Table.source field behavior."""

    def test_default_source_empty(self) -> None:
        """Default source should be empty string."""
        table = Table(
            cells=(TableCell(text="A", row=0, col=0),),
            rows=1, cols=1,
            bbox=BBox(x0=0, y0=0, x1=100, y1=100),
        )
        assert table.source == ""

    def test_source_word_grid(self) -> None:
        """Should be able to create a Table with source='word_grid'."""
        table = Table(
            cells=(TableCell(text="A", row=0, col=0),),
            rows=1, cols=1,
            bbox=BBox(x0=0, y0=0, x1=100, y1=100),
            source="word_grid",
        )
        assert table.source == "word_grid"


class TestFilterLayoutTablesWordGridBypass:
    """Test that word_grid tables bypass layout filtering."""

    def setup_method(self) -> None:
        self.extractor = PdfplumberTableExtractor(ParserConfig())
        self.page_width = 600.0
        self.page_height = 800.0

    def _make_word_grid_table(
        self,
        rows: int = 3,
        cols: int = 4,
        x0: float = 10.0,
        y0: float = 10.0,
        x1: float = 590.0,
        y1: float = 790.0,
    ) -> Table:
        """Create a word-grid table that would normally be filtered."""
        cells = tuple(
            TableCell(text=f"cell-{r}-{c}", row=r, col=c)
            for r in range(rows) for c in range(cols)
        )
        return Table(
            cells=cells,
            rows=rows,
            cols=cols,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            source="word_grid",
        )

    def test_word_grid_bypasses_full_page_check(self) -> None:
        """word_grid table covering full page should NOT be filtered."""
        table = self._make_word_grid_table()
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 1
        assert result[0].source == "word_grid"

    def test_word_grid_bypasses_single_col_check(self) -> None:
        """word_grid table with 1 column should NOT be filtered."""
        table = self._make_word_grid_table(rows=5, cols=1)
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 1

    def test_normal_table_still_filtered(self) -> None:
        """Normal (non-word_grid) full-page table should still be filtered."""
        cells = tuple(
            TableCell(text="text", row=r, col=c)
            for r in range(3) for c in range(3)
        )
        table = Table(
            cells=cells,
            rows=3, cols=3,
            bbox=BBox(x0=10, y0=10, x1=590, y1=790),
        )
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 0

    def test_mixed_tables_only_normal_filtered(self) -> None:
        """In a mixed list, only non-word_grid tables get filtered."""
        word_grid = self._make_word_grid_table()
        normal = Table(
            cells=(TableCell(text="x", row=0, col=0),),
            rows=1, cols=1,
            bbox=BBox(x0=10, y0=10, x1=590, y1=790),
        )
        result = self.extractor._filter_layout_tables(
            [word_grid, normal], self.page_width, self.page_height,
        )
        # word_grid kept, normal (single col + full page) filtered
        assert len(result) == 1
        assert result[0].source == "word_grid"


# ---------------------------------------------------------------------------
# Regression: Existing behavior unchanged
# ---------------------------------------------------------------------------

class TestBulletRegressions:
    """Ensure existing bullet conversion behavior is not broken."""

    def test_multiline_bullets_all_converted(self) -> None:
        """Pre-existing test: multiline bullets at line start."""
        text = "●첫 번째 항목\n●두 번째 항목\n●세 번째 항목"
        result = _convert_unicode_bullets(text)
        assert result.count("- ") >= 3
        assert "●" not in result

    def test_sub_bullets_multiline(self) -> None:
        """Pre-existing test: multiline sub-bullets at line start."""
        text = "○하위 항목 1\n○하위 항목 2"
        result = _convert_unicode_bullets(text)
        assert result.count("  - ") >= 2
        assert "○" not in result


class TestTableFilterRegressions:
    """Ensure existing table filter behavior is not broken."""

    def setup_method(self) -> None:
        self.extractor = PdfplumberTableExtractor(ParserConfig())

    def test_normal_table_kept(self) -> None:
        """A normal small table should still pass the filter."""
        cells = tuple(
            TableCell(text="content", row=r, col=c)
            for r in range(3) for c in range(3)
        )
        table = Table(
            cells=cells,
            rows=3, cols=3,
            bbox=BBox(x0=50, y0=200, x1=400, y1=400),
        )
        result = self.extractor._filter_layout_tables([table], 600.0, 800.0)
        assert len(result) == 1

    def test_full_page_table_filtered(self) -> None:
        """A full-page layout table should still be filtered."""
        cells = tuple(
            TableCell(text="text", row=r, col=c)
            for r in range(5) for c in range(3)
        )
        table = Table(
            cells=cells,
            rows=5, cols=3,
            bbox=BBox(x0=10, y0=10, x1=590, y1=790),
        )
        result = self.extractor._filter_layout_tables([table], 600.0, 800.0)
        assert len(result) == 0

    def test_word_grid_table_renders_as_markdown_table(self) -> None:
        """A word_grid table should render as a proper markdown table."""
        cells = (
            TableCell(text="Flight", row=0, col=0),
            TableCell(text="From", row=0, col=1),
            TableCell(text="To", row=0, col=2),
            TableCell(text="OZ 545", row=1, col=0),
            TableCell(text="ICN", row=1, col=1),
            TableCell(text="PRG", row=1, col=2),
            TableCell(text="OZ 546", row=2, col=0),
            TableCell(text="PRG", row=2, col=1),
            TableCell(text="ICN", row=2, col=2),
        )
        table = Table(
            cells=cells,
            rows=3, cols=3,
            bbox=BBox(x0=10, y0=100, x1=500, y1=300),
            source="word_grid",
        )
        md = table_to_markdown(table)
        assert "| Flight | From | To |" in md
        assert "| --- | --- | --- |" in md
        assert "| OZ 545 | ICN | PRG |" in md
        assert "| OZ 546 | PRG | ICN |" in md
