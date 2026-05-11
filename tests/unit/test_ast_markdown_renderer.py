"""Unit tests for docforge.processing.ast_markdown_renderer."""

from __future__ import annotations

import pytest

from docforge.domain.ast_nodes import (
    DocumentAST,
    FigureNode,
    HeadingNode,
    ParagraphNode,
    SectionNode,
    TableNode,
)
from docforge.domain.models import Table, TableCell
from docforge.domain.value_objects import BBox
from docforge.processing.ast_markdown_renderer import render


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section(
    heading_text: str | None = None,
    heading_level: int = 1,
    children: tuple = (),
    node_id: str = "sec001",
) -> SectionNode:
    heading = None
    if heading_text is not None:
        heading = HeadingNode(
            node_id=f"{node_id}_h",
            text=heading_text,
            level=heading_level,
        )
    return SectionNode(node_id=node_id, heading=heading, children=children)


def _paragraph(text: str, node_id: str = "p001") -> ParagraphNode:
    return ParagraphNode(node_id=node_id, text=text)


def _ast(root: SectionNode) -> DocumentAST:
    return DocumentAST(root=root, source_file="test.pdf", page_count=1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHeadingRendering:
    """Heading levels produce correct # prefixes.

    The renderer skips the root section's heading (is_root=True), so each
    test section must be a *child* of the root, not the root itself.
    """

    def test_h1(self):
        section = _section(
            heading_text="Title",
            heading_level=1,
            children=(_paragraph("Body."),),
        )
        ast = _ast(SectionNode(node_id="root", children=(section,)))
        md = render(ast)
        assert "# Title" in md
        assert "## Title" not in md

    def test_h2(self):
        section = _section(
            heading_text="Subtitle",
            heading_level=2,
            children=(_paragraph("Content."),),
        )
        ast = _ast(SectionNode(node_id="root", children=(section,)))
        md = render(ast)
        assert "## Subtitle" in md

    def test_h3(self):
        section = _section(
            heading_text="Sub-subtitle",
            heading_level=3,
            children=(_paragraph("Details."),),
        )
        ast = _ast(SectionNode(node_id="root", children=(section,)))
        md = render(ast)
        assert "### Sub-subtitle" in md

    def test_heading_level_clamped_to_6(self):
        section = _section(
            heading_text="Deep",
            heading_level=7,
            children=(_paragraph("Very deep."),),
        )
        ast = _ast(SectionNode(node_id="root", children=(section,)))
        md = render(ast)
        # Level clamped to 6
        assert "###### Deep" in md
        assert "####### Deep" not in md


class TestParagraphRendering:
    def test_simple_paragraph(self):
        ast = _ast(SectionNode(
            node_id="root",
            children=(_paragraph("Hello world."),),
        ))
        md = render(ast)
        assert "Hello world." in md

    def test_empty_paragraph_skipped(self):
        ast = _ast(SectionNode(
            node_id="root",
            children=(_paragraph(""),),
        ))
        md = render(ast)
        assert md == ""


class TestNestedSections:
    def test_nested_section_rendering(self):
        inner = _section(
            heading_text="Inner",
            heading_level=2,
            children=(_paragraph("Inner text."),),
            node_id="inner",
        )
        outer = _section(
            heading_text="Outer",
            heading_level=1,
            children=(_paragraph("Outer text."), inner),
            node_id="outer",
        )
        ast = _ast(SectionNode(node_id="root", children=(outer,)))
        md = render(ast)

        assert "# Outer" in md
        assert "## Inner" in md
        assert "Outer text." in md
        assert "Inner text." in md

        # Outer appears before Inner
        assert md.index("# Outer") < md.index("## Inner")


class TestTableRendering:
    def test_simple_table(self):
        table = Table(
            cells=(
                TableCell(text="Name", row=0, col=0),
                TableCell(text="Value", row=0, col=1),
                TableCell(text="Alpha", row=1, col=0),
                TableCell(text="100", row=1, col=1),
            ),
            rows=2,
            cols=2,
            bbox=BBox(x0=50, y0=100, x1=500, y1=200),
        )
        table_node = TableNode(node_id="t001", table=table)
        ast = _ast(SectionNode(
            node_id="root",
            children=(table_node,),
        ))
        md = render(ast)
        assert "| Name | Value |" in md
        assert "| --- | --- |" in md
        assert "| Alpha | 100 |" in md

    def test_empty_table_skipped(self):
        table = Table(
            cells=(),
            rows=0,
            cols=0,
            bbox=BBox(x0=50, y0=100, x1=500, y1=200),
        )
        table_node = TableNode(node_id="t002", table=table)
        ast = _ast(SectionNode(
            node_id="root",
            children=(table_node,),
        ))
        md = render(ast)
        assert md == ""


class TestFigureRendering:
    def test_figure_with_caption(self):
        fig = FigureNode(
            node_id="fig001",
            bbox=BBox(x0=100, y0=200, x1=400, y1=400),
            data=b"\x89PNG",
            format="png",
            caption="Figure 1: Overview",
            alt_text=None,
            page_num=1,
        )
        ast = _ast(SectionNode(node_id="root", children=(fig,)))
        md = render(ast)
        assert "![Figure 1: Overview]" in md
        assert "placeholder://image/fig001?page=1" in md

    def test_figure_with_alt_text_emits_text(self):
        """When alt_text is present, emit extracted text instead of image tag."""
        fig = FigureNode(
            node_id="fig002",
            bbox=BBox(x0=100, y0=200, x1=400, y1=400),
            data=b"\x89PNG",
            format="png",
            caption=None,
            alt_text="Extracted text from image",
            page_num=2,
        )
        ast = _ast(SectionNode(node_id="root", children=(fig,)))
        md = render(ast)
        assert "Extracted text from image" in md
        assert "![" not in md


class TestFullDocumentRender:
    """Snapshot-style test for a complete small document."""

    def test_full_render(self):
        table = Table(
            cells=(
                TableCell(text="Col A", row=0, col=0),
                TableCell(text="Col B", row=0, col=1),
                TableCell(text="val1", row=1, col=0),
                TableCell(text="val2", row=1, col=1),
            ),
            rows=2,
            cols=2,
            bbox=BBox(x0=50, y0=300, x1=500, y1=400),
        )
        doc = _ast(SectionNode(
            node_id="root",
            children=(
                _section(
                    heading_text="Introduction",
                    heading_level=1,
                    children=(
                        _paragraph("This is the intro.", node_id="p1"),
                        _section(
                            heading_text="Background",
                            heading_level=2,
                            children=(
                                _paragraph("Some background.", node_id="p2"),
                            ),
                            node_id="bg",
                        ),
                    ),
                    node_id="intro",
                ),
                _section(
                    heading_text="Results",
                    heading_level=1,
                    children=(
                        _paragraph("Here are the results.", node_id="p3"),
                        TableNode(node_id="t1", table=table),
                    ),
                    node_id="results",
                ),
            ),
        ))

        md = render(doc)

        # Structure checks
        assert "# Introduction" in md
        assert "## Background" in md
        assert "# Results" in md
        assert "| Col A | Col B |" in md

        # Order checks
        lines = md.split("\n")
        intro_idx = next(i for i, l in enumerate(lines) if "# Introduction" in l)
        bg_idx = next(i for i, l in enumerate(lines) if "## Background" in l)
        results_idx = next(i for i, l in enumerate(lines) if "# Results" in l)
        assert intro_idx < bg_idx < results_idx
