"""Tests for pdfplumber table extraction filtering."""

from docforge.adapters.pdfplumber_tables import PdfplumberTableExtractor
from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig


def _make_table(
    rows: int,
    cols: int,
    x0: float = 20.0,
    y0: float = 20.0,
    x1: float = 580.0,
    y1: float = 780.0,
    cell_text: str = "sample text",
) -> Table:
    """Helper to create a Table for filter testing."""
    cells: list[TableCell] = []
    for r in range(rows):
        for c in range(cols):
            cells.append(TableCell(text=cell_text, row=r, col=c))
    return Table(
        cells=tuple(cells),
        rows=rows,
        cols=cols,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
    )


class TestLayoutTableFiltering:
    """Test that layout tables are filtered out."""

    def setup_method(self) -> None:
        self.extractor = PdfplumberTableExtractor(ParserConfig())
        self.page_width = 600.0
        self.page_height = 800.0

    def test_filters_full_page_table(self) -> None:
        """A table covering >=80% of the page should be filtered."""
        table = _make_table(5, 3, x0=10, y0=10, x1=590, y1=790)
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 0

    def test_keeps_normal_table(self) -> None:
        """A table covering <80% of the page should be kept."""
        table = _make_table(5, 3, x0=50, y0=200, x1=550, y1=500)
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 1

    def test_filters_high_column_count(self) -> None:
        """A table with >=10 columns should be filtered."""
        table = _make_table(3, 15, x0=50, y0=200, x1=550, y1=400)
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 0

    def test_filters_fragmented_cells(self) -> None:
        """A table with avg cell text <3 chars should be filtered."""
        table = _make_table(5, 4, x0=50, y0=200, x1=550, y1=400, cell_text="ab")
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 0

    def test_keeps_table_with_enough_text(self) -> None:
        """A small table with sufficient cell text should be kept."""
        table = _make_table(3, 3, x0=50, y0=200, x1=400, y1=350, cell_text="content here")
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 1

    def test_mixed_tables_partial_filter(self) -> None:
        """Only layout tables should be filtered from a mixed list."""
        good_table = _make_table(3, 3, x0=50, y0=200, x1=400, y1=350, cell_text="good data")
        bad_table = _make_table(5, 15, x0=10, y0=10, x1=590, y1=790)
        result = self.extractor._filter_layout_tables(
            [good_table, bad_table], self.page_width, self.page_height,
        )
        assert len(result) == 1
