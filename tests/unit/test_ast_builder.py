"""Unit tests for docforge.processing.ast_builder."""

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
from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import (
    PageConfidence,
    PageContent,
    ParsedImage,
    Table,
    TableCell,
    TextBlock,
)
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.ast_builder import build


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FONT = FontInfo(name="Helvetica", size=12.0, is_bold=False)
_BOLD_FONT = FontInfo(name="Helvetica-Bold", size=14.0, is_bold=True)


def _block(
    text: str,
    y0: float = 0.0,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
    block_id: str | None = None,
    parent_id: str | None = None,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=50.0, y0=y0, x1=500.0, y1=y0 + 14.0),
        font=_BOLD_FONT if block_type == BlockType.HEADING else _FONT,
        block_type=block_type,
        heading_level=heading_level,
        block_id=block_id,
        parent_id=parent_id,
    )


def _page(
    page_num: int = 1,
    blocks: tuple[TextBlock, ...] = (),
    tables: tuple[Table, ...] = (),
    images: tuple[ParsedImage, ...] = (),
    page_type: PageType = PageType.DIGITAL,
) -> PageContent:
    raw = " ".join(b.text for b in blocks)
    return PageContent(
        page_num=page_num,
        page_type=page_type,
        blocks=blocks,
        tables=tables,
        raw_text=raw,
        width=612.0,
        height=792.0,
        images=images,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSimpleDocument:
    """Single heading + paragraphs."""

    def test_single_heading_with_paragraphs(self):
        pages = (
            _page(
                blocks=(
                    _block("Introduction", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="h1"),
                    _block("First paragraph.", y0=80, block_id="p1", parent_id="h1"),
                    _block("Second paragraph.", y0=110, block_id="p2", parent_id="h1"),
                ),
            ),
        )
        ast = build(pages)

        assert isinstance(ast, DocumentAST)
        assert ast.page_count == 1
        assert len(ast.root.children) == 1

        section = ast.root.children[0]
        assert isinstance(section, SectionNode)
        assert section.heading is not None
        assert section.heading.text == "Introduction"
        assert section.heading.level == 1
        assert len(section.children) == 2
        assert all(isinstance(c, ParagraphNode) for c in section.children)

    def test_empty_text_blocks_skipped(self):
        pages = (
            _page(
                blocks=(
                    _block("Title", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="h1"),
                    _block("", y0=80, block_id="empty"),
                    _block("  ", y0=90, block_id="whitespace"),
                    _block("Content.", y0=110, block_id="p1"),
                ),
            ),
        )
        ast = build(pages)
        section = ast.root.children[0]
        assert isinstance(section, SectionNode)
        # Only "Content." should be a child (empty/whitespace skipped)
        paragraph_children = [c for c in section.children if isinstance(c, ParagraphNode)]
        assert len(paragraph_children) == 1
        assert paragraph_children[0].text == "Content."


class TestMultiLevelHeadings:
    """Nested heading hierarchy (h1 -> h2 -> h3)."""

    def test_nested_sections(self):
        pages = (
            _page(
                blocks=(
                    _block("Chapter 1", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="ch1"),
                    _block("Intro text.", y0=80, block_id="p1", parent_id="ch1"),
                    _block("Section 1.1", y0=120, block_type=BlockType.HEADING, heading_level=2, block_id="s11"),
                    _block("Section content.", y0=150, block_id="p2", parent_id="s11"),
                    _block("Section 1.1.1", y0=190, block_type=BlockType.HEADING, heading_level=3, block_id="s111"),
                    _block("Deep content.", y0=220, block_id="p3", parent_id="s111"),
                ),
            ),
        )
        ast = build(pages)

        # Root has one top-level section (Chapter 1)
        assert len(ast.root.children) == 1
        ch1 = ast.root.children[0]
        assert isinstance(ch1, SectionNode)
        assert ch1.heading.text == "Chapter 1"

        # Chapter 1 has: paragraph + Section 1.1
        paragraphs = [c for c in ch1.children if isinstance(c, ParagraphNode)]
        sections = [c for c in ch1.children if isinstance(c, SectionNode)]
        assert len(paragraphs) == 1
        assert len(sections) == 1

        s11 = sections[0]
        assert s11.heading.text == "Section 1.1"

        # Section 1.1 has: paragraph + Section 1.1.1
        sub_sections = [c for c in s11.children if isinstance(c, SectionNode)]
        assert len(sub_sections) == 1
        assert sub_sections[0].heading.text == "Section 1.1.1"

    def test_sibling_headings(self):
        """Two h2 sections under one h1."""
        pages = (
            _page(
                blocks=(
                    _block("Chapter", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="ch"),
                    _block("Part A", y0=100, block_type=BlockType.HEADING, heading_level=2, block_id="a"),
                    _block("A content.", y0=130, block_id="pa"),
                    _block("Part B", y0=200, block_type=BlockType.HEADING, heading_level=2, block_id="b"),
                    _block("B content.", y0=230, block_id="pb"),
                ),
            ),
        )
        ast = build(pages)
        ch = ast.root.children[0]
        assert isinstance(ch, SectionNode)
        sub_sections = [c for c in ch.children if isinstance(c, SectionNode)]
        assert len(sub_sections) == 2
        assert sub_sections[0].heading.text == "Part A"
        assert sub_sections[1].heading.text == "Part B"


class TestTablesInAST:
    """Tables are placed in the nearest section by y-coordinate."""

    def test_table_inserted_into_section(self):
        table = Table(
            cells=(
                TableCell(text="A", row=0, col=0),
                TableCell(text="B", row=0, col=1),
                TableCell(text="1", row=1, col=0),
                TableCell(text="2", row=1, col=1),
            ),
            rows=2,
            cols=2,
            bbox=BBox(x0=50, y0=160, x1=500, y1=250),
        )
        pages = (
            _page(
                blocks=(
                    _block("Results", y0=100, block_type=BlockType.HEADING, heading_level=1, block_id="r1"),
                    _block("See table below.", y0=140, block_id="p1"),
                ),
                tables=(table,),
            ),
        )
        ast = build(pages)
        section = ast.root.children[0]
        table_nodes = [c for c in section.children if isinstance(c, TableNode)]
        assert len(table_nodes) == 1
        assert table_nodes[0].table.rows == 2


class TestImagesInAST:
    """Images become FigureNodes."""

    def test_image_inserted_as_figure(self):
        image = ParsedImage(
            bbox=BBox(x0=100, y0=200, x1=400, y1=400),
            data=b"\x89PNG",
            format="png",
            caption="Figure 1",
            page_num=1,
            block_id="img001",
            alt_text=None,
        )
        pages = (
            _page(
                blocks=(
                    _block("Figures", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="f1"),
                ),
                images=(image,),
            ),
        )
        ast = build(pages)
        section = ast.root.children[0]
        figures = [c for c in section.children if isinstance(c, FigureNode)]
        assert len(figures) == 1
        assert figures[0].caption == "Figure 1"
        assert figures[0].page_num == 1


class TestHeadlessDocument:
    """Document with no headings -- all content under root."""

    def test_all_content_under_root(self):
        pages = (
            _page(
                blocks=(
                    _block("First line.", y0=50, block_id="l1"),
                    _block("Second line.", y0=80, block_id="l2"),
                    _block("Third line.", y0=110, block_id="l3"),
                ),
            ),
        )
        ast = build(pages)
        # All paragraphs should be direct children of root
        assert len(ast.root.children) == 3
        assert all(isinstance(c, ParagraphNode) for c in ast.root.children)


class TestCoverTocPages:
    """COVER and TOC pages get marker sections."""

    def test_cover_page_creates_marker_section(self):
        pages = (
            _page(
                page_num=1,
                page_type=PageType.COVER,
                blocks=(
                    _block("Company Name", y0=200, block_id="c1"),
                    _block("Annual Report 2025", y0=350, block_id="c2"),
                ),
            ),
        )
        ast = build(pages)
        assert len(ast.root.children) == 1
        section = ast.root.children[0]
        assert isinstance(section, SectionNode)
        assert section.heading.text == "[표지]"

    def test_toc_page_creates_marker_section(self):
        pages = (
            _page(
                page_num=2,
                page_type=PageType.TOC,
                blocks=(
                    _block("Table of Contents", y0=50, block_id="toc1"),
                    _block("Chapter 1 ......... 3", y0=80, block_id="toc2"),
                ),
            ),
        )
        ast = build(pages)
        assert len(ast.root.children) == 1
        section = ast.root.children[0]
        assert section.heading.text == "[목차]"


class TestClauseMapping:
    """Korean legal clause types map to section levels."""

    def test_clause_subclause_item(self):
        pages = (
            _page(
                blocks=(
                    _block(
                        "제1조 (목적)",
                        y0=50,
                        block_type=BlockType.CLAUSE,
                        heading_level=0,
                        block_id="cl1",
                    ),
                    _block("본 약관의 목적은...", y0=80, block_id="p1"),
                    _block(
                        "1. 세부사항",
                        y0=120,
                        block_type=BlockType.SUBCLAUSE,
                        heading_level=0,
                        block_id="sc1",
                    ),
                    _block("세부 내용입니다.", y0=150, block_id="p2"),
                    _block(
                        "가. 항목",
                        y0=190,
                        block_type=BlockType.ITEM,
                        heading_level=0,
                        block_id="it1",
                    ),
                    _block("항목 내용.", y0=220, block_id="p3"),
                ),
            ),
        )
        ast = build(pages)

        # Top-level: clause section
        assert len(ast.root.children) == 1
        clause = ast.root.children[0]
        assert isinstance(clause, SectionNode)
        assert clause.heading.text == "제1조 (목적)"
        assert clause.heading.level == 1


class TestEmptyPages:
    """Empty pages should not produce empty sections."""

    def test_empty_page_skipped(self):
        pages = (
            _page(page_num=1, blocks=()),
            _page(
                page_num=2,
                blocks=(
                    _block("Content", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="h1"),
                ),
            ),
        )
        ast = build(pages)
        assert ast.page_count == 2
        # Only the heading section from page 2
        assert len(ast.root.children) == 1


class TestNoiseBlocksSkipped:
    """PAGE_HEADER, PAGE_FOOTER, PAGE_NUMBER blocks are skipped."""

    def test_noise_blocks_filtered(self):
        pages = (
            _page(
                blocks=(
                    _block("Page Header", y0=10, block_type=BlockType.PAGE_HEADER, block_id="ph"),
                    _block("Title", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="h1"),
                    _block("Content.", y0=80, block_id="p1"),
                    _block("Page 1 of 10", y0=750, block_type=BlockType.PAGE_NUMBER, block_id="pn"),
                    _block("Footer text", y0=770, block_type=BlockType.PAGE_FOOTER, block_id="pf"),
                ),
            ),
        )
        ast = build(pages)
        section = ast.root.children[0]
        # Only "Content." paragraph, no noise blocks
        paragraphs = [c for c in section.children if isinstance(c, ParagraphNode)]
        assert len(paragraphs) == 1
        assert paragraphs[0].text == "Content."


class TestMultiPageContinuity:
    """Blocks from page 2 continue the section opened on page 1."""

    def test_cross_page_section(self):
        pages = (
            _page(
                page_num=1,
                blocks=(
                    _block("Chapter 1", y0=50, block_type=BlockType.HEADING, heading_level=1, block_id="ch1"),
                    _block("Page 1 content.", y0=80, block_id="p1"),
                ),
            ),
            _page(
                page_num=2,
                blocks=(
                    _block("Continued content.", y0=50, block_id="p2"),
                ),
            ),
        )
        ast = build(pages)
        # "Continued content." should be under Chapter 1, not root
        ch1 = ast.root.children[0]
        assert isinstance(ch1, SectionNode)
        paragraphs = [c for c in ch1.children if isinstance(c, ParagraphNode)]
        assert len(paragraphs) == 2
        assert paragraphs[1].text == "Continued content."
