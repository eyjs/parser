"""Tests for Excel reader adapter."""

from __future__ import annotations

import io

import pytest

from docforge.adapters.excel_reader import ExcelParseResult, parse_excel_bytes


def _create_test_xlsx(
    sheets: dict[str, list[list]],
) -> bytes:
    """Create a minimal .xlsx file in memory for testing."""
    import openpyxl

    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


class TestParseExcelBytes:
    """parse_excel_bytes 기본 동작."""

    def test_basic_xlsx(self):
        data = _create_test_xlsx({
            "Sheet1": [
                ["name", "age", "city"],
                ["Alice", 30, "Seoul"],
                ["Bob", 25, "Busan"],
            ],
        })
        result = parse_excel_bytes(data, filename="test.xlsx")

        assert isinstance(result, ExcelParseResult)
        assert "Alice" in result.markdown
        assert "Bob" in result.markdown
        assert "| name | age | city |" in result.markdown
        assert result.metadata["sheets"] == 1
        assert result.metadata["total_rows"] == 3  # header + 2 data

    def test_multi_sheet(self):
        data = _create_test_xlsx({
            "Users": [
                ["name", "role"],
                ["Alice", "admin"],
            ],
            "Products": [
                ["id", "name"],
                ["1", "Widget"],
            ],
        })
        result = parse_excel_bytes(data, filename="multi.xlsx")

        assert "## Users" in result.markdown
        assert "## Products" in result.markdown
        assert result.metadata["sheets"] == 2

    def test_empty_sheet_skipped(self):
        data = _create_test_xlsx({
            "Empty": [],
            "Data": [
                ["col1", "col2"],
                ["a", "b"],
            ],
        })
        result = parse_excel_bytes(data, filename="partial.xlsx")

        assert "## Data" in result.markdown
        assert "## Empty" not in result.markdown
        assert result.metadata["sheets"] == 1

    def test_blank_header_auto_naming(self):
        data = _create_test_xlsx({
            "Sheet1": [
                ["name", "", "city"],
                ["Alice", "30", "Seoul"],
            ],
        })
        result = parse_excel_bytes(data, filename="blank_header.xlsx")

        assert "Column_2" in result.markdown

    def test_korean_data(self):
        data = _create_test_xlsx({
            "시트1": [
                ["이름", "나이"],
                ["홍길동", 30],
                ["김철수", 25],
            ],
        })
        result = parse_excel_bytes(data, filename="korean.xlsx")

        assert "홍길동" in result.markdown
        assert "## 시트1" in result.markdown

    def test_stats_has_parse_time(self):
        data = _create_test_xlsx({
            "Sheet1": [["a", "b"], ["1", "2"]],
        })
        result = parse_excel_bytes(data)

        assert "parse_time_ms" in result.stats
        assert result.stats["parse_time_ms"] >= 0

    def test_result_is_frozen(self):
        data = _create_test_xlsx({
            "Sheet1": [["a", "b"], ["1", "2"]],
        })
        result = parse_excel_bytes(data)

        with pytest.raises(AttributeError):
            result.markdown = "changed"  # type: ignore[misc]

    def test_max_sheets_limit(self):
        sheets = {}
        for i in range(5):
            sheets[f"Sheet{i}"] = [["col"], [f"val{i}"]]

        data = _create_test_xlsx(sheets)
        result = parse_excel_bytes(data, max_sheets=2)

        assert result.metadata["sheets"] == 2

    def test_empty_rows_skipped(self):
        data = _create_test_xlsx({
            "Sheet1": [
                ["name", "age"],
                ["Alice", 30],
                [None, None],  # empty row
                ["Bob", 25],
            ],
        })
        result = parse_excel_bytes(data)

        # empty row should be skipped, so 3 rows (header + 2 data)
        assert result.metadata["total_rows"] == 3
