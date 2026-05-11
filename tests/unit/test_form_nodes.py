"""Unit tests for FormNode/KeyValueNode AST nodes and form rendering."""

from __future__ import annotations

import pytest

from docforge.domain.ast_nodes import (
    ASTNodeType,
    DocumentAST,
    FormNode,
    KeyValueNode,
    ParagraphNode,
    SectionNode,
    TableNode,
)
from docforge.domain.models import Table, TableCell, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.ast_builder import build, _is_form_like, _table_to_form_node
from docforge.processing.ast_markdown_renderer import render


# ---------------------------------------------------------------------------
# KeyValueNode Tests
# ---------------------------------------------------------------------------


class TestKeyValueNode:
    def test_creation(self):
        kv = KeyValueNode(node_id="kv001", key="Name", value="John Doe")
        assert kv.node_id == "kv001"
        assert kv.key == "Name"
        assert kv.value == "John Doe"
        assert kv.node_type == ASTNodeType.KEY_VALUE

    def test_frozen(self):
        kv = KeyValueNode(node_id="kv001", key="Name", value="John Doe")
        with pytest.raises(AttributeError):
            kv.key = "Changed"  # type: ignore[misc]

    def test_empty_key_and_value(self):
        kv = KeyValueNode(node_id="kv002", key="", value="")
        assert kv.key == ""
        assert kv.value == ""

    def test_korean_key_value(self):
        kv = KeyValueNode(node_id="kv003", key="성명", value="홍길동")
        assert kv.key == "성명"
        assert kv.value == "홍길동"


# ---------------------------------------------------------------------------
# FormNode Tests
# ---------------------------------------------------------------------------


class TestFormNode:
    def test_creation(self):
        fields = (
            KeyValueNode(node_id="f1", key="Name", value="Alice"),
            KeyValueNode(node_id="f2", key="Date", value="2026-01-01"),
        )
        form = FormNode(node_id="form001", fields=fields)
        assert form.node_id == "form001"
        assert len(form.fields) == 2
        assert form.node_type == ASTNodeType.FORM

    def test_frozen(self):
        form = FormNode(node_id="form001", fields=())
        with pytest.raises(AttributeError):
            form.fields = ()  # type: ignore[misc]

    def test_empty_fields(self):
        form = FormNode(node_id="form002", fields=())
        assert len(form.fields) == 0

    def test_fields_immutable_tuple(self):
        fields = (KeyValueNode(node_id="f1", key="K", value="V"),)
        form = FormNode(node_id="form003", fields=fields)
        assert isinstance(form.fields, tuple)


# ---------------------------------------------------------------------------
# Form Detection Heuristic Tests
# ---------------------------------------------------------------------------


class TestIsFormLike:
    def _make_table(self, rows_data: list[tuple[str, str]]) -> Table:
        """Helper to build a 2-column Table from (left, right) text pairs."""
        cells = []
        for i, (left, right) in enumerate(rows_data):
            cells.append(TableCell(text=left, row=i, col=0))
            cells.append(TableCell(text=right, row=i, col=1))
        return Table(
            cells=tuple(cells),
            rows=len(rows_data),
            cols=2,
            bbox=BBox(50, 50, 500, 200),
        )

    def test_clear_form(self):
        table = self._make_table([
            ("성명:", "홍길동"),
            ("생년월일:", "1990-01-01"),
            ("연락처:", "010-1234-5678"),
            ("주소:", "서울특별시 강남구"),
        ])
        assert _is_form_like(table) is True

    def test_data_table(self):
        """A regular data table with long cells should NOT be form-like."""
        table = self._make_table([
            ("이 약관은 보험계약에 관한 사항을 정한 것입니다.", "보험계약자는 약관에 따라 보험금을 지급받습니다."),
            ("약관의 규정에 따라 보험료를 납입합니다.", "보험계약은 청약일로부터 효력을 발생합니다."),
        ])
        assert _is_form_like(table) is False

    def test_single_column(self):
        table = Table(
            cells=(TableCell(text="Only one column", row=0, col=0),),
            rows=1,
            cols=1,
            bbox=BBox(0, 0, 100, 50),
        )
        assert _is_form_like(table) is False

    def test_three_columns(self):
        cells = tuple(
            TableCell(text=f"Cell {r}{c}", row=r, col=c)
            for r in range(3) for c in range(3)
        )
        table = Table(cells=cells, rows=3, cols=3, bbox=BBox(0, 0, 200, 100))
        assert _is_form_like(table) is False

    def test_single_row(self):
        table = self._make_table([("Key:", "Value")])
        # rows < 2 should not be considered form
        assert _is_form_like(table) is False

    def test_short_labels_without_colon(self):
        table = self._make_table([
            ("이름", "홍길동"),
            ("나이", "30"),
            ("직업", "개발자"),
        ])
        assert _is_form_like(table) is True

    def test_mixed_rows(self):
        """Table with some form-like rows and some data rows."""
        table = self._make_table([
            ("성명:", "홍길동"),
            ("이 약관은 보험계약에 관한 사항입니다", "보험계약자는 약관에 따라"),
            ("연락처:", "010-1234-5678"),
            ("긴 텍스트가 들어간 셀로서 라벨이 아닌 경우입니다", "역시 긴 텍스트"),
            ("주소:", "서울시"),
        ])
        # 3 out of 5 rows are form-like (60%), exactly at the threshold
        assert _is_form_like(table) is True


class TestTableToFormNode:
    def test_basic_conversion(self):
        cells = (
            TableCell(text="성명:", row=0, col=0),
            TableCell(text="홍길동", row=0, col=1),
            TableCell(text="나이:", row=1, col=0),
            TableCell(text="30", row=1, col=1),
        )
        table = Table(cells=cells, rows=2, cols=2, bbox=BBox(0, 0, 200, 80))
        form = _table_to_form_node(table, page_num=1)

        assert isinstance(form, FormNode)
        assert len(form.fields) == 2
        assert form.fields[0].key == "성명"
        assert form.fields[0].value == "홍길동"
        assert form.fields[1].key == "나이"
        assert form.fields[1].value == "30"

    def test_colon_stripped_from_key(self):
        cells = (
            TableCell(text="Name:", row=0, col=0),
            TableCell(text="Alice", row=0, col=1),
            TableCell(text="Email：", row=1, col=0),
            TableCell(text="alice@example.com", row=1, col=1),
        )
        table = Table(cells=cells, rows=2, cols=2, bbox=BBox(0, 0, 200, 80))
        form = _table_to_form_node(table, page_num=1)

        assert form.fields[0].key == "Name"
        assert form.fields[1].key == "Email"

    def test_empty_cells_skipped(self):
        cells = (
            TableCell(text="", row=0, col=0),
            TableCell(text="", row=0, col=1),
            TableCell(text="Key:", row=1, col=0),
            TableCell(text="Value", row=1, col=1),
        )
        table = Table(cells=cells, rows=2, cols=2, bbox=BBox(0, 0, 200, 80))
        form = _table_to_form_node(table, page_num=1)
        assert len(form.fields) == 1


# ---------------------------------------------------------------------------
# Form Rendering Tests
# ---------------------------------------------------------------------------


class TestFormRendering:
    def test_render_form_node(self):
        fields = (
            KeyValueNode(node_id="f1", key="Name", value="Alice"),
            KeyValueNode(node_id="f2", key="Date", value="2026-01-01"),
        )
        form = FormNode(node_id="form001", fields=fields)
        section = SectionNode(
            node_id="sec001",
            heading=None,
            children=(form,),
        )
        ast = DocumentAST(root=section, source_file="test.pdf", page_count=1)
        md = render(ast)
        assert "**Name**: Alice" in md
        assert "**Date**: 2026-01-01" in md

    def test_render_empty_form(self):
        form = FormNode(node_id="form002", fields=())
        section = SectionNode(
            node_id="sec002",
            heading=None,
            children=(form,),
        )
        ast = DocumentAST(root=section, source_file="test.pdf", page_count=1)
        md = render(ast)
        assert md == ""  # Empty form produces no output

    def test_render_key_value_in_section(self):
        kv = KeyValueNode(node_id="kv001", key="Status", value="Active")
        form = FormNode(node_id="form003", fields=(kv,))
        section = SectionNode(
            node_id="sec003",
            heading=None,
            children=(form,),
        )
        ast = DocumentAST(root=section, source_file="test.pdf", page_count=1)
        md = render(ast)
        assert "**Status**: Active" in md

    def test_render_form_with_korean(self):
        fields = (
            KeyValueNode(node_id="f1", key="성명", value="홍길동"),
            KeyValueNode(node_id="f2", key="연락처", value="010-1234-5678"),
        )
        form = FormNode(node_id="form004", fields=fields)
        section = SectionNode(
            node_id="sec004",
            heading=None,
            children=(form,),
        )
        ast = DocumentAST(root=section, source_file="test.pdf", page_count=1)
        md = render(ast)
        assert "**성명**: 홍길동" in md
        assert "**연락처**: 010-1234-5678" in md

    def test_form_mixed_with_paragraphs(self):
        paragraph = ParagraphNode(node_id="p001", text="Some introductory text.")
        fields = (
            KeyValueNode(node_id="f1", key="Key", value="Value"),
        )
        form = FormNode(node_id="form005", fields=fields)
        section = SectionNode(
            node_id="sec005",
            heading=None,
            children=(paragraph, form),
        )
        ast = DocumentAST(root=section, source_file="test.pdf", page_count=1)
        md = render(ast)
        assert "Some introductory text." in md
        assert "**Key**: Value" in md


# ---------------------------------------------------------------------------
# AST Node Type Tests
# ---------------------------------------------------------------------------


class TestASTNodeTypes:
    def test_form_node_type(self):
        assert ASTNodeType.FORM == "form"
        assert ASTNodeType.FORM.value == "form"

    def test_key_value_node_type(self):
        assert ASTNodeType.KEY_VALUE == "key_value"
        assert ASTNodeType.KEY_VALUE.value == "key_value"
