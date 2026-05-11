"""Tests for markdown assembly and post-processing."""

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import (
    Metadata,
    NoiseStats,
    PageContent,
    Table,
    TableCell,
    TextBlock,
)
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.domain.models import ParsedImage
from docforge.processing.markdown_assembler import (
    _classify_image_text,
    _deduplicate_tables,
    assemble_page,
    finalize_markdown,
    table_to_markdown,
)


def _make_block(
    text: str,
    y0: float,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=50.0, y0=y0, x1=500.0, y1=y0 + 12.0),
        font=FontInfo(name="Arial", size=10.0, is_bold=False),
        block_type=block_type,
        heading_level=heading_level,
    )


class TestTableToMarkdown:
    """Test table-to-markdown conversion."""

    def test_simple_table(self) -> None:
        cells = (
            TableCell(text="Name", row=0, col=0),
            TableCell(text="Value", row=0, col=1),
            TableCell(text="A", row=1, col=0),
            TableCell(text="1", row=1, col=1),
        )
        table = Table(
            cells=cells, rows=2, cols=2,
            bbox=BBox(x0=0, y0=0, x1=100, y1=100),
        )
        md = table_to_markdown(table)
        assert "| Name | Value |" in md
        assert "| --- | --- |" in md
        assert "| A | 1 |" in md

    def test_empty_table(self) -> None:
        table = Table(
            cells=(), rows=0, cols=0,
            bbox=BBox(x0=0, y0=0, x1=100, y1=100),
        )
        md = table_to_markdown(table)
        assert md == ""

    def test_pipe_escape(self) -> None:
        cells = (
            TableCell(text="A|B", row=0, col=0),
            TableCell(text="C", row=0, col=1),
            TableCell(text="D", row=1, col=0),
            TableCell(text="E", row=1, col=1),
        )
        table = Table(
            cells=cells, rows=2, cols=2,
            bbox=BBox(x0=0, y0=0, x1=100, y1=100),
        )
        md = table_to_markdown(table)
        assert "A\\|B" in md

    def test_rowspan_propagation(self) -> None:
        """Merged cell values should propagate to all spanned rows."""
        cells = (
            TableCell(text="구분", row=0, col=0),
            TableCell(text="보험기간", row=0, col=1),
            TableCell(text="가입나이", row=0, col=2),
            TableCell(text="상해입원의료비", row=1, col=0, rowspan=2),
            TableCell(text="1년(최초계약)", row=1, col=1),
            TableCell(text="5~90세", row=1, col=2),
            TableCell(text="", row=2, col=0),
            TableCell(text="1년(갱신계약)", row=2, col=1),
            TableCell(text="6~92세", row=2, col=2),
        )
        table = Table(
            cells=cells, rows=3, cols=3,
            bbox=BBox(x0=0, y0=0, x1=300, y1=300),
        )
        md = table_to_markdown(table)
        lines = [l for l in md.split("\n") if l.startswith("|")]
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "상해입원의료비" in lines[2]
        assert "1년(최초계약)" in lines[2]
        assert "상해입원의료비" in lines[3]
        assert "1년(갱신계약)" in lines[3]

    def test_colspan_propagation(self) -> None:
        """Merged cell values should propagate across columns."""
        cells = (
            TableCell(text="전체 제목", row=0, col=0, colspan=3),
            TableCell(text="A", row=1, col=0),
            TableCell(text="B", row=1, col=1),
            TableCell(text="C", row=1, col=2),
        )
        table = Table(
            cells=cells, rows=2, cols=3,
            bbox=BBox(x0=0, y0=0, x1=300, y1=200),
        )
        md = table_to_markdown(table)
        header_line = [l for l in md.split("\n") if l.startswith("|")][0]
        assert header_line.count("전체 제목") == 3

    def test_rowspan_colspan_combined(self) -> None:
        """Combined rowspan+colspan cell fills the full rectangular region."""
        cells = (
            TableCell(text="H1", row=0, col=0),
            TableCell(text="H2", row=0, col=1),
            TableCell(text="H3", row=0, col=2),
            TableCell(text="병합", row=1, col=0, rowspan=2, colspan=2),
            TableCell(text="X", row=1, col=2),
            TableCell(text="", row=2, col=0),
            TableCell(text="", row=2, col=1),
            TableCell(text="Y", row=2, col=2),
        )
        table = Table(
            cells=cells, rows=3, cols=3,
            bbox=BBox(x0=0, y0=0, x1=300, y1=300),
        )
        md = table_to_markdown(table)
        data_lines = [l for l in md.split("\n") if l.startswith("|")][2:]
        assert data_lines[0].count("병합") == 2
        assert data_lines[1].count("병합") == 2

    def test_needs_review_note(self) -> None:
        cells = (
            TableCell(text="H", row=0, col=0),
            TableCell(text="", row=1, col=0),
        )
        table = Table(
            cells=cells, rows=2, cols=1,
            bbox=BBox(x0=0, y0=0, x1=100, y1=100),
            needs_review=True,
        )
        md = table_to_markdown(table)
        assert "manual review" in md.lower()


class TestPageAssembly:
    """Test single-page markdown assembly."""

    def test_heading_formatting(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(
                _make_block("제1조 (목적)", 100.0, BlockType.HEADING, 4),
                _make_block("이 약관은 보험에 대해 규정합니다.", 120.0),
            ),
            tables=(),
            raw_text="",
        )
        md = assemble_page(page, 10.0, config)
        assert "#### 제1조 (목적)" in md
        assert "이 약관은" in md

    def test_clause_formatting(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(
                _make_block("① 보험계약자는", 100.0, BlockType.CLAUSE),
            ),
            tables=(),
            raw_text="",
        )
        md = assemble_page(page, 10.0, config)
        assert "① 보험계약자는" in md


def _make_image(
    alt_text: str | None = None,
    page_num: int = 1,
    block_id: str = "abc123",
    y0: float = 50.0,
    data: bytes = b"\x89PNG",
    fmt: str = "png",
    caption: str | None = None,
) -> ParsedImage:
    return ParsedImage(
        bbox=BBox(x0=50.0, y0=y0, x1=200.0, y1=y0 + 100.0),
        data=data,
        format=fmt,
        caption=caption,
        page_num=page_num,
        block_id=block_id,
        alt_text=alt_text,
    )


class TestImageBlockTextPriority:
    """Test that images with extracted text output text, not image references."""

    def test_alt_text_image_outputs_text_only(self) -> None:
        """Image with alt_text should output text, not ![alt](path)."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text="추출된 텍스트 내용", y0=300.0),),
        )
        md = assemble_page(page, 10.0, config)
        assert "추출된 텍스트 내용" in md
        assert "![" not in md
        assert "]()" not in md

    def test_no_alt_text_image_keeps_reference(self) -> None:
        """Image without alt_text should keep the image markdown reference."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text=None, y0=300.0),),
        )
        md = assemble_page(page, 10.0, config)
        assert "![" in md

    def test_alt_text_with_data_prioritizes_text(self) -> None:
        """Image with both alt_text and data should output text, not image."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(
                alt_text="이미지에서 추출한 텍스트",
                data=b"\x89PNG\r\n\x1a\n",
                y0=300.0,
            ),),
        )
        md = assemble_page(page, 10.0, config)
        assert "이미지에서 추출한 텍스트" in md
        assert "![" not in md


class TestImageTextClassification:
    """Test figure vs heading classification for extracted image text."""

    def test_short_text_at_top_is_heading(self) -> None:
        """Short text without punctuation at page top -> heading."""
        img = _make_image(alt_text="문서 제목", y0=50.0)
        result = _classify_image_text("문서 제목", img, page_height=800.0)
        assert result == "heading"

    def test_long_text_is_body(self) -> None:
        """Text longer than 60 chars -> body regardless of position."""
        long_text = "이것은 매우 긴 텍스트입니다 " * 5  # well over 60 chars
        img = _make_image(alt_text=long_text, y0=50.0)
        result = _classify_image_text(long_text.strip(), img, page_height=800.0)
        assert result == "body"

    def test_text_with_period_is_body(self) -> None:
        """Text with period -> body regardless of length or position."""
        img = _make_image(alt_text="설명 문장.", y0=50.0)
        result = _classify_image_text("설명 문장.", img, page_height=800.0)
        assert result == "body"

    def test_heading_renders_as_h1(self) -> None:
        """In assembled page, heading-classified text renders as # heading."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text="문서 제목", y0=50.0),),
        )
        md = assemble_page(page, 10.0, config)
        assert "# 문서 제목" in md
        assert "![" not in md

    def test_body_renders_as_plain_text(self) -> None:
        """In assembled page, body-classified text renders as plain text."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text="그림 1. 시스템 구조도", y0=400.0),),
        )
        md = assemble_page(page, 10.0, config)
        assert "그림 1. 시스템 구조도" in md
        assert "# " not in md
        assert "![" not in md

    def test_no_link_format_generated(self) -> None:
        """Image with alt_text must never produce [text](path) link format."""
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text="제목 텍스트", y0=50.0),),
        )
        md = assemble_page(page, 10.0, config)
        # Must not contain markdown link format [text](url)
        import re as _re
        assert not _re.search(r"\[.*?\]\(.*?\)", md)


class TestTableDeduplication:
    """Test table deduplication by IoU."""

    def _make_table(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        text: str = "A",
    ) -> Table:
        cells = (
            TableCell(text="H", row=0, col=0),
            TableCell(text=text, row=1, col=0),
        )
        return Table(
            cells=cells,
            rows=2,
            cols=1,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        )

    def test_empty_tables(self) -> None:
        assert _deduplicate_tables(()) == []

    def test_single_table(self) -> None:
        t = self._make_table(0, 0, 100, 100)
        result = _deduplicate_tables((t,))
        assert len(result) == 1
        assert result[0] is t

    def test_duplicate_tables_same_bbox(self) -> None:
        t1 = self._make_table(0, 0, 100, 100, text="A")
        t2 = self._make_table(0, 0, 100, 100, text="B")
        result = _deduplicate_tables((t1, t2))
        assert len(result) == 1
        assert result[0] is t1

    def test_non_overlapping_tables_kept(self) -> None:
        t1 = self._make_table(0, 0, 100, 100, text="Alpha")
        t2 = self._make_table(200, 200, 300, 300, text="Beta")
        result = _deduplicate_tables((t1, t2))
        assert len(result) == 2

    def test_high_iou_deduplicates(self) -> None:
        """Two tables with IoU > 0.8 should collapse to one."""
        t1 = self._make_table(0, 0, 100, 100)
        # Slightly shifted -- still very high IoU
        t2 = self._make_table(2, 2, 102, 102)
        result = _deduplicate_tables((t1, t2))
        assert len(result) == 1

    def test_low_iou_keeps_both(self) -> None:
        """Two tables with IoU < 0.8 should both be kept."""
        t1 = self._make_table(0, 0, 100, 100, text="Gamma")
        # Overlap is about 50x50 = 2500, union = 10000+10000-2500=17500, IoU~0.14
        t2 = self._make_table(50, 50, 150, 150, text="Delta")
        result = _deduplicate_tables((t1, t2))
        assert len(result) == 2

    def test_content_hash_dedup_different_bbox(self) -> None:
        """Phase 2: tables with different bboxes but identical cell content
        should be deduplicated via content-hash."""
        t1 = self._make_table(0, 0, 100, 100, text="SameValue")
        # Completely different bbox, but same cell text
        t2 = self._make_table(500, 500, 600, 600, text="SameValue")
        result = _deduplicate_tables((t1, t2))
        assert len(result) == 1
        assert result[0] is t1

    def test_content_hash_keeps_different_content(self) -> None:
        """Tables with different bboxes AND different content should both be kept."""
        t1 = self._make_table(0, 0, 100, 100, text="Alpha")
        t2 = self._make_table(500, 500, 600, 600, text="Beta")
        result = _deduplicate_tables((t1, t2))
        assert len(result) == 2

    def test_assemble_page_deduplicates(self) -> None:
        """End-to-end: duplicate tables appear only once in assembled markdown."""
        config = ParserConfig()
        t1 = self._make_table(0, 200, 400, 300, text="ValueA")
        t2 = self._make_table(0, 200, 400, 300, text="ValueB")
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(t1, t2),
            raw_text="",
        )
        md = assemble_page(page, 10.0, config)
        # Only the first table should appear
        assert "ValueA" in md
        assert "ValueB" not in md


class TestFinalMarkdown:
    """Test final markdown assembly with front matter."""

    def test_includes_front_matter(self) -> None:
        metadata = Metadata(
            source="test.pdf",
            source_type="digital_pdf",
            pages=10,
            parsed_at="2026-04-28T14:30:00+09:00",
            parser_version="1.0.0",
            ocr_used=False,
            tables_extracted=5,
            tables_need_review=1,
            noise_removed=NoiseStats(headers=9, footers=9, page_numbers=10),
        )
        result = finalize_markdown(["## Heading\n\nContent"], metadata)
        assert result.startswith("---")
        assert 'source: "test.pdf"' in result
        assert "## Heading" in result

    def test_page_separators(self) -> None:
        metadata = Metadata(
            source="test.pdf", source_type="digital_pdf", pages=2,
            parsed_at="", parser_version="1.0.0", ocr_used=False,
            tables_extracted=0, tables_need_review=0, noise_removed=NoiseStats(),
        )
        result = finalize_markdown(["Page 1 content", "Page 2 content"], metadata)
        assert "---" in result
        assert "Page 1 content" in result
        assert "Page 2 content" in result

    def test_post_processing_cleans_whitespace(self) -> None:
        metadata = Metadata(
            source="test.pdf", source_type="digital_pdf", pages=1,
            parsed_at="", parser_version="1.0.0", ocr_used=False,
            tables_extracted=0, tables_need_review=0, noise_removed=NoiseStats(),
        )
        result = finalize_markdown(["Content\n\n\n\n\nExtra lines"], metadata)
        # Should not have 4+ consecutive newlines
        assert "\n\n\n\n" not in result

    def test_nbsp_normalization(self) -> None:
        """Non-breaking spaces should be normalized to regular spaces."""
        metadata = Metadata(
            source="test.pdf", source_type="digital_pdf", pages=1,
            parsed_at="", parser_version="1.0.0", ocr_used=False,
            tables_extracted=0, tables_need_review=0, noise_removed=NoiseStats(),
        )
        # U+00A0 (NBSP) in text
        result = finalize_markdown(["이 보험계약"], metadata)
        assert " " not in result
        assert "이 보험계약" in result

    def test_en_space_normalization(self) -> None:
        """EN SPACE (U+2002) should be normalized."""
        metadata = Metadata(
            source="test.pdf", source_type="digital_pdf", pages=1,
            parsed_at="", parser_version="1.0.0", ocr_used=False,
            tables_extracted=0, tables_need_review=0, noise_removed=NoiseStats(),
        )
        result = finalize_markdown(["word another"], metadata)
        assert " " not in result
        assert "word another" in result

    def test_ideographic_space_normalization(self) -> None:
        """Ideographic space (U+3000) should be normalized."""
        metadata = Metadata(
            source="test.pdf", source_type="digital_pdf", pages=1,
            parsed_at="", parser_version="1.0.0", ocr_used=False,
            tables_extracted=0, tables_need_review=0, noise_removed=NoiseStats(),
        )
        result = finalize_markdown(["word　another"], metadata)
        assert "　" not in result
        assert "word another" in result

    def test_multiple_spaces_collapsed(self) -> None:
        """Multiple consecutive spaces should be collapsed to one."""
        metadata = Metadata(
            source="test.pdf", source_type="digital_pdf", pages=1,
            parsed_at="", parser_version="1.0.0", ocr_used=False,
            tables_extracted=0, tables_need_review=0, noise_removed=NoiseStats(),
        )
        result = finalize_markdown(["word   many    spaces"], metadata)
        assert "word many spaces" in result
