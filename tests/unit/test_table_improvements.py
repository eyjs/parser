"""Tests for table extraction improvements — merged cells and leader dots."""

from __future__ import annotations

import pytest

from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.processing.noise_detector import (
    filter_leader_dots_from_table,
    is_leader_dots_row,
)


class TestLeaderDotsDetection:
    def test_detects_leader_dots(self) -> None:
        assert is_leader_dots_row(["제1조 ………………… 3"]) is True

    def test_detects_ellipsis_leader(self) -> None:
        assert is_leader_dots_row(["보험금 지급사유 …… 15"]) is True

    def test_detects_dots_in_cells(self) -> None:
        assert is_leader_dots_row(["제1조", "…………", "3"]) is True

    def test_normal_row_not_detected(self) -> None:
        assert is_leader_dots_row(["보장내용", "보험금", "비고"]) is False

    def test_empty_row_not_detected(self) -> None:
        assert is_leader_dots_row(["", "", ""]) is False

    def test_single_period_not_detected(self) -> None:
        assert is_leader_dots_row(["금액: 100.5원"]) is False


class TestLeaderDotsFiltering:
    def test_filters_dots_rows(self) -> None:
        cells = [
            TableCell(text="항목", row=0, col=0),
            TableCell(text="페이지", row=0, col=1),
            TableCell(text="제1조 ………… 3", row=1, col=0),
            TableCell(text="", row=1, col=1),
            TableCell(text="보장내용", row=2, col=0),
            TableCell(text="100만원", row=2, col=1),
        ]
        filtered, new_rows = filter_leader_dots_from_table(cells, 3, 2)
        assert new_rows == 2
        # Row 0 stays at 0, Row 2 becomes Row 1
        texts = [c.text for c in filtered if hasattr(c, "text")]
        assert "항목" in texts
        assert "보장내용" in texts
        assert "제1조 ………… 3" not in texts

    def test_no_filtering_when_no_dots(self) -> None:
        cells = [
            TableCell(text="보장내용", row=0, col=0),
            TableCell(text="보험금", row=0, col=1),
            TableCell(text="상해", row=1, col=0),
            TableCell(text="100만원", row=1, col=1),
        ]
        filtered, new_rows = filter_leader_dots_from_table(cells, 2, 2)
        assert new_rows == 2
        assert len(filtered) == 4


class TestMergedCells:
    """Test that TableCell colspan/rowspan are preserved correctly."""

    def test_merged_cell_has_colspan(self) -> None:
        cell = TableCell(text="합계", row=0, col=0, colspan=3, rowspan=1)
        assert cell.colspan == 3
        assert cell.rowspan == 1

    def test_merged_cell_has_rowspan(self) -> None:
        cell = TableCell(text="보장내���", row=0, col=0, colspan=1, rowspan=2)
        assert cell.rowspan == 2

    def test_merged_cell_frozen(self) -> None:
        cell = TableCell(text="합계", row=0, col=0, colspan=3)
        with pytest.raises(AttributeError):
            cell.colspan = 1  # type: ignore[misc]
