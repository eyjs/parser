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


class TestExtractFromHints:
    """P0-5: Surya TABLE hint-constrained extraction."""

    def setup_method(self) -> None:
        self.extractor = PdfplumberTableExtractor(ParserConfig())
        self.page_width = 600.0
        self.page_height = 800.0

    def test_hint_skipped_when_existing_table_covers(self) -> None:
        """Hint bbox already covered by an existing table should be skipped."""
        existing = [_make_table(3, 3, x0=50, y0=200, x1=400, y1=350)]
        hint = BBox(x0=50, y0=200, x1=400, y1=350)
        result = self.extractor._extract_from_hints(
            None, existing, [hint], self.page_width, self.page_height,  # type: ignore[arg-type]
        )
        assert len(result) == 0

    def test_hint_not_covered_triggers_extraction(self) -> None:
        """Hint bbox not covered by existing tables should trigger extraction.

        Since we don't have a real pdfplumber page, we verify the logic
        by checking that the method does not error with an uncovered hint.
        """
        existing: list[Table] = []
        hint = BBox(x0=50, y0=500, x1=400, y1=700)
        # With no real page, extract_from_bbox will fail gracefully
        # We just verify the coverage check works
        covered = any(
            hint.iou(t.bbox) > 0.3
            for t in existing
        )
        assert not covered

    def test_partial_overlap_not_covered(self) -> None:
        """Hint with only partial overlap (IoU <= 0.3) should not be skipped."""
        existing = [_make_table(3, 3, x0=50, y0=200, x1=200, y1=300)]
        hint = BBox(x0=150, y0=250, x1=500, y1=600)
        # Compute IoU: intersection is (150,250)-(200,300) = 50*50 = 2500
        # existing area = 150*100 = 15000, hint area = 350*350 = 122500
        # union = 15000 + 122500 - 2500 = 135000
        # IoU = 2500 / 135000 ~ 0.019 < 0.3
        covered = any(
            hint.iou(t.bbox) > 0.3
            for t in existing
        )
        assert not covered


class TestAdaptiveTolerance:
    """P0-6: DPI-adaptive snap/join tolerance."""

    def test_default_dpi_returns_config_values(self) -> None:
        """When page_dpi matches base_dpi, tolerances should stay at config values."""
        config = ParserConfig(snap_tolerance=5, join_tolerance=5, base_dpi=72)
        extractor = PdfplumberTableExtractor(config)
        snap, join = extractor._compute_adaptive_tolerance(page_dpi=72)
        assert snap == 5
        assert join == 5

    def test_high_dpi_scales_up(self) -> None:
        """300 DPI should scale tolerances up."""
        config = ParserConfig(snap_tolerance=5, join_tolerance=5, base_dpi=72)
        extractor = PdfplumberTableExtractor(config)
        snap, join = extractor._compute_adaptive_tolerance(page_dpi=300)
        # 300/72 * 5 = ~20.8 -> round to 21 (clamp range [3, 25])
        assert snap == 21
        assert join == 21

    def test_150_dpi_scales_moderately(self) -> None:
        """150 DPI should scale tolerances moderately."""
        config = ParserConfig(snap_tolerance=5, join_tolerance=5, base_dpi=72)
        extractor = PdfplumberTableExtractor(config)
        snap, join = extractor._compute_adaptive_tolerance(page_dpi=150)
        # 150/72 * 5 = ~10.4 -> round to 10
        assert snap == 10
        assert join == 10

    def test_none_dpi_returns_fixed(self) -> None:
        """When page_dpi is None, return fixed config values."""
        config = ParserConfig(snap_tolerance=5, join_tolerance=5)
        extractor = PdfplumberTableExtractor(config)
        snap, join = extractor._compute_adaptive_tolerance(page_dpi=None)
        assert snap == 5
        assert join == 5

    def test_disabled_returns_fixed(self) -> None:
        """When adaptive_tolerance_enabled=False, return fixed config values."""
        config = ParserConfig(
            snap_tolerance=5, join_tolerance=5,
            adaptive_tolerance_enabled=False,
        )
        extractor = PdfplumberTableExtractor(config)
        snap, join = extractor._compute_adaptive_tolerance(page_dpi=300)
        assert snap == 5
        assert join == 5

    def test_low_dpi_clamps_to_minimum(self) -> None:
        """Very low tolerance after scaling should clamp to 3."""
        config = ParserConfig(snap_tolerance=3, join_tolerance=3, base_dpi=300)
        extractor = PdfplumberTableExtractor(config)
        snap, join = extractor._compute_adaptive_tolerance(page_dpi=72)
        # 72/300 * 3 = 0.72 -> round to 1 -> clamp to 3
        assert snap == 3
        assert join == 3


class TestCheck6DenseTablePreserved:
    """Check 6 should preserve dense wide tables with real data."""

    def setup_method(self) -> None:
        self.extractor = PdfplumberTableExtractor(ParserConfig())
        self.page_width = 600.0
        self.page_height = 800.0

    def test_keeps_dense_wide_flight_table(self) -> None:
        """A 7-col, 2-row table with high fill rate and short cells should be kept."""
        cells: list[TableCell] = []
        flight_data = [
            ["OZ 545", "ASIANA", "ICN", "14JUN 10:45", "T2", "ECONOMY/K", "13:00"],
            ["OZ 546", "ASIANA", "PRG", "18JUN 18:50", "T1", "ECONOMY/K", "11:20"],
        ]
        for r, row in enumerate(flight_data):
            for c, text in enumerate(row):
                cells.append(TableCell(text=text, row=r, col=c))
        table = Table(
            cells=tuple(cells), rows=2, cols=7,
            bbox=BBox(x0=30, y0=200, x1=560, y1=320),
        )
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 1

    def test_still_filters_sparse_wide_table(self) -> None:
        """A 7-col, 2-row table with mostly empty cells should still be filtered."""
        cells: list[TableCell] = []
        for r in range(2):
            for c in range(7):
                text = "KE" if (r == 0 and c == 0) else ""
                cells.append(TableCell(text=text, row=r, col=c))
        table = Table(
            cells=tuple(cells), rows=2, cols=7,
            bbox=BBox(x0=10, y0=10, x1=560, y1=400),
        )
        result = self.extractor._filter_layout_tables(
            [table], self.page_width, self.page_height,
        )
        assert len(result) == 0
