"""Tests for cross-page table merging."""

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig
from docforge.processing.table_merger import merge_cross_page_tables


def _make_table(
    rows: int,
    cols: int,
    y0: float = 100.0,
    y1: float = 700.0,
    header_texts: list[str] | None = None,
) -> Table:
    """Helper to create a table with auto-generated cells."""
    cells: list[TableCell] = []

    for r in range(rows):
        for c in range(cols):
            if r == 0 and header_texts:
                text = header_texts[c] if c < len(header_texts) else f"H{c}"
            else:
                text = f"R{r}C{c}"
            cells.append(TableCell(text=text, row=r, col=c))

    return Table(
        cells=tuple(cells),
        rows=rows,
        cols=cols,
        bbox=BBox(x0=50.0, y0=y0, x1=500.0, y1=y1),
    )


class TestCrossPageTableMerging:
    """Test cross-page table detection and merging."""

    def test_no_merge_single_page(self) -> None:
        config = ParserConfig()
        table = _make_table(3, 2)
        result = merge_cross_page_tables(
            [([table], 800.0, 600.0)],
            config,
        )
        assert len(result) == 1
        assert len(result[0]) == 1

    def test_no_merge_different_columns(self) -> None:
        config = ParserConfig()
        t1 = _make_table(3, 2, y0=600.0, y1=790.0)
        t2 = _make_table(3, 3, y0=10.0, y1=200.0)

        result = merge_cross_page_tables(
            [([t1], 800.0, 600.0), ([t2], 800.0, 600.0)],
            config,
        )
        assert len(result[0]) == 1
        assert len(result[1]) == 1

    def test_merges_matching_tables(self) -> None:
        config = ParserConfig()
        headers = ["Name", "Value"]
        t1 = _make_table(3, 2, y0=600.0, y1=790.0, header_texts=headers)
        t2 = _make_table(3, 2, y0=10.0, y1=200.0, header_texts=headers)

        result = merge_cross_page_tables(
            [([t1], 800.0, 600.0), ([t2], 800.0, 600.0)],
            config,
        )
        # First page should have merged table
        assert len(result[0]) == 1
        merged = result[0][0]
        # 3 rows from first + 2 rows from second (header removed)
        assert merged.rows == 5
        # Second page should have no tables (merged into first)
        assert len(result[1]) == 0

    def test_no_merge_when_not_at_boundary(self) -> None:
        config = ParserConfig()
        headers = ["Name", "Value"]
        t1 = _make_table(3, 2, y0=100.0, y1=300.0, header_texts=headers)
        t2 = _make_table(3, 2, y0=400.0, y1=600.0, header_texts=headers)

        result = merge_cross_page_tables(
            [([t1], 800.0, 600.0), ([t2], 800.0, 600.0)],
            config,
        )
        assert len(result[0]) == 1
        assert len(result[1]) == 1
