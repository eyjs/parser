"""Tests for CSV reader adapter."""

from __future__ import annotations

import pytest

from docforge.adapters.csv_reader import CsvParseResult, parse_csv_bytes


class TestParseCsvBytes:
    """parse_csv_bytes 기본 동작."""

    def test_basic_csv(self):
        csv_data = b"name,age,city\nAlice,30,Seoul\nBob,25,Busan\n"
        result = parse_csv_bytes(csv_data, filename="test.csv")

        assert isinstance(result, CsvParseResult)
        assert "Alice" in result.markdown
        assert "Bob" in result.markdown
        assert "| name | age | city |" in result.markdown
        assert result.metadata["rows"] == 2
        assert result.metadata["cols"] == 3
        assert result.metadata["truncated"] is False

    def test_korean_data(self):
        csv_data = "이름,나이,도시\n홍길동,30,서울\n김철수,25,부산\n".encode("utf-8")
        result = parse_csv_bytes(csv_data, filename="korean.csv")

        assert "홍길동" in result.markdown
        assert "김철수" in result.markdown
        assert "이름" in result.markdown

    def test_semicolon_delimiter(self):
        csv_data = b"name;age;city\nAlice;30;Seoul\nBob;25;Busan\n"
        result = parse_csv_bytes(csv_data, filename="semi.csv")

        assert "Alice" in result.markdown
        assert "| name | age | city |" in result.markdown

    def test_empty_csv(self):
        result = parse_csv_bytes(b"", filename="empty.csv")

        assert result.markdown == ""
        assert result.metadata["rows"] == 0
        assert result.metadata["cols"] == 0

    def test_max_rows_truncation(self):
        lines = ["col1,col2"]
        for i in range(20):
            lines.append(f"val{i},data{i}")
        csv_data = "\n".join(lines).encode("utf-8")

        result = parse_csv_bytes(csv_data, filename="big.csv", max_rows=5)

        assert result.metadata["truncated"] is True
        assert "잘렸습니다" in result.markdown

    def test_pipe_escape(self):
        csv_data = b"name,value\ntest|name,100\n"
        result = parse_csv_bytes(csv_data, filename="pipe.csv")

        assert "test\\|name" in result.markdown

    def test_column_count_normalization_short_row(self):
        """Short rows get padded with empty cells."""
        csv_data = b"a,b,c\n1,2\n4,5,6\n"
        result = parse_csv_bytes(csv_data, filename="short.csv")

        # row with 2 cols should be padded to 3
        lines = result.markdown.split("\n")
        data_lines = [l for l in lines if l.startswith("|") and "---" not in l]
        # header + 2 data rows
        assert len(data_lines) == 3

    def test_column_count_normalization_long_row(self):
        """Long rows get trimmed to header column count."""
        csv_data = b"a,b\n1,2,3,4\n5,6\n"
        result = parse_csv_bytes(csv_data, filename="long.csv")

        lines = result.markdown.split("\n")
        data_lines = [l for l in lines if l.startswith("|") and "---" not in l]
        # All rows should have 2 columns
        for line in data_lines:
            # count pipes: | col1 | col2 | => 3 pipes
            assert line.count("|") == 3

    def test_stats_has_parse_time(self):
        csv_data = b"a,b\n1,2\n"
        result = parse_csv_bytes(csv_data)

        assert "parse_time_ms" in result.stats
        assert result.stats["parse_time_ms"] >= 0

    def test_result_is_frozen(self):
        csv_data = b"a,b\n1,2\n"
        result = parse_csv_bytes(csv_data)

        with pytest.raises(AttributeError):
            result.markdown = "changed"  # type: ignore[misc]

    def test_filename_in_markdown_header(self):
        csv_data = b"a,b\n1,2\n"
        result = parse_csv_bytes(csv_data, filename="report.csv")

        assert "## report.csv" in result.markdown

    def test_no_filename_no_header(self):
        csv_data = b"a,b\n1,2\n"
        result = parse_csv_bytes(csv_data, filename="")

        assert "##" not in result.markdown
