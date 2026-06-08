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
    _clean_cell_text,
    _deduplicate_tables,
    _is_form_like,
    _is_layout_table,
    _repair_text,
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


class TestCleanCellText:
    """Test CID stripping and noise removal from table cells."""

    def test_strip_cid(self) -> None:
        assert _clean_cell_text("Hello (cid:123) world") == "Hello world"

    def test_strip_multiple_cid(self) -> None:
        assert _clean_cell_text("(cid:1)(cid:2) text (cid:3)") == "text"

    def test_status_ok_noise(self) -> None:
        assert _clean_cell_text("상태 OK") == ""
        assert _clean_cell_text("  상태  OK  ") == ""

    def test_normal_text_unchanged(self) -> None:
        assert _clean_cell_text("보험계약자") == "보험계약자"

    def test_whitespace_normalization(self) -> None:
        assert _clean_cell_text("a  b   c") == "a b c"


class TestRepairText:
    """Test mojibake repair and CID cleanup for image alt_text."""

    def test_strip_cid(self) -> None:
        assert _repair_text("Hello (cid:144) world") == "Hello world"

    def test_status_ok_removed(self) -> None:
        assert _repair_text("상태 OK") == ""

    def test_normal_text_unchanged(self) -> None:
        assert _repair_text("전자항공권") == "전자항공권"

    def test_mojibake_repair(self) -> None:
        original = "전자항공권"
        garbled = original.encode("utf-8").decode("latin1")
        repaired = _repair_text(garbled)
        assert repaired == original


class TestFormLikeDetection:
    """Test form-like table detection."""

    def test_form_like_2col(self) -> None:
        cells = (
            TableCell(text="이름:", row=0, col=0),
            TableCell(text="홍길동", row=0, col=1),
            TableCell(text="생년월일:", row=1, col=0),
            TableCell(text="1990-01-01", row=1, col=1),
            TableCell(text="연락처:", row=2, col=0),
            TableCell(text="010-1234-5678", row=2, col=1),
        )
        table = Table(
            cells=cells, rows=3, cols=2,
            bbox=BBox(x0=0, y0=0, x1=400, y1=300),
        )
        assert _is_form_like(table)

    def test_data_table_not_form(self) -> None:
        cells = (
            TableCell(text="구분", row=0, col=0),
            TableCell(text="보험기간", row=0, col=1),
            TableCell(text="가입나이", row=0, col=2),
            TableCell(text="상해입원", row=1, col=0),
            TableCell(text="1년", row=1, col=1),
            TableCell(text="5~90세", row=1, col=2),
        )
        table = Table(
            cells=cells, rows=2, cols=3,
            bbox=BBox(x0=0, y0=0, x1=300, y1=200),
        )
        assert not _is_form_like(table)

    def test_form_renders_as_key_value(self) -> None:
        cells = (
            TableCell(text="승객명:", row=0, col=0),
            TableCell(text="김철수", row=0, col=1),
            TableCell(text="편명:", row=1, col=0),
            TableCell(text="KE123", row=1, col=1),
        )
        table = Table(
            cells=cells, rows=2, cols=2,
            bbox=BBox(x0=0, y0=0, x1=300, y1=200),
        )
        md = table_to_markdown(table)
        assert "**승객명**" in md
        assert "김철수" in md
        assert "**편명**" in md
        assert "KE123" in md
        assert "|" not in md


class TestLayoutTableDetection:
    """Test layout table detection."""

    def test_layout_table_with_long_cells(self) -> None:
        long_text = "이 보험계약은 보험계약자와 보험회사 사이에 체결된 계약으로서 보험계약자가 보험료를 납입하고 보험회사가 보험금을 지급합니다. 보험금 지급사유가 발생하였을 때에는 관련 서류를 갖추어 보험회사에 청구하여야 합니다."
        cells = (
            TableCell(text="보험계약 안내", row=0, col=0),
            TableCell(text=long_text, row=1, col=0),
            TableCell(text=long_text, row=2, col=0),
        )
        table = Table(
            cells=cells, rows=3, cols=1,
            bbox=BBox(x0=0, y0=0, x1=500, y1=700),
        )
        assert _is_layout_table(table)

    def test_layout_table_renders_as_text(self) -> None:
        long_text = "이 보험계약은 보험계약자와 보험회사 사이에 체결된 계약으로서 보험계약자가 보험료를 납입하고 보험회사가 보험금을 지급합니다. 보험금 지급사유가 발생하였을 때에는 관련 서류를 갖추어 보험회사에 청구하여야 합니다."
        cells = (
            TableCell(text="안내", row=0, col=0),
            TableCell(text=long_text, row=0, col=1),
            TableCell(text="내용", row=1, col=0),
            TableCell(text=long_text, row=1, col=1),
        )
        table = Table(
            cells=cells, rows=2, cols=2,
            bbox=BBox(x0=0, y0=0, x1=500, y1=500),
        )
        md = table_to_markdown(table)
        assert "|" not in md
        assert long_text in md

    def test_normal_data_table_not_layout(self) -> None:
        cells = (
            TableCell(text="구분", row=0, col=0),
            TableCell(text="금액", row=0, col=1),
            TableCell(text="A", row=1, col=0),
            TableCell(text="1000", row=1, col=1),
        )
        table = Table(
            cells=cells, rows=2, cols=2,
            bbox=BBox(x0=0, y0=0, x1=300, y1=200),
        )
        assert not _is_layout_table(table)


class TestTableCidCleanup:
    """Test that CID references are cleaned from table cells during rendering."""

    def test_cid_in_table_cells_stripped(self) -> None:
        cells = (
            TableCell(text="Header", row=0, col=0),
            TableCell(text="Value", row=0, col=1),
            TableCell(text="(cid:144) text", row=1, col=0),
            TableCell(text="normal", row=1, col=1),
        )
        table = Table(
            cells=cells, rows=2, cols=2,
            bbox=BBox(x0=0, y0=0, x1=300, y1=200),
        )
        md = table_to_markdown(table)
        assert "(cid:" not in md
        assert "text" in md


class TestImageAltTextRepair:
    """Test that mojibake in image alt_text is repaired during assembly."""

    def test_mojibake_alt_text_repaired(self) -> None:
        original_text = "전자항공권"
        garbled = original_text.encode("utf-8").decode("latin1")
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text=garbled, y0=300.0),),
        )
        md = assemble_page(page, 10.0, config)
        assert original_text in md
        assert garbled not in md

    def test_cid_in_alt_text_stripped(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(_make_image(alt_text="(cid:123) 텍스트 (cid:456)", y0=300.0),),
        )
        md = assemble_page(page, 10.0, config)
        assert "(cid:" not in md
        assert "텍스트" in md


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


class TestWideLayoutTableDetection:
    """Test that wide tables (e.g. 8-col e-ticket) are detected as layout."""

    def test_8col_sparse_table_is_layout(self) -> None:
        cells = tuple(
            TableCell(text=t, row=0, col=c)
            for c, t in enumerate(["KE", "ICN", "NRT", "", "", "", "", ""])
        ) + tuple(
            TableCell(text=t, row=1, col=c)
            for c, t in enumerate(["", "14:00", "17:30", "", "", "", "", ""])
        )
        table = Table(
            cells=cells, rows=2, cols=8,
            bbox=BBox(x0=0, y0=0, x1=500, y1=100),
        )
        assert _is_layout_table(table)

    def test_7col_table_with_long_cells_is_layout(self) -> None:
        cells = tuple(
            TableCell(
                text="This is a rather long paragraph cell text that exceeds the eighty character threshold for layout detection purposes here.",
                row=0, col=c,
            )
            for c in range(7)
        )
        table = Table(
            cells=cells, rows=1, cols=7,
            bbox=BBox(x0=0, y0=0, x1=500, y1=50),
        )
        assert _is_layout_table(table)

    def test_4col_data_table_not_layout(self) -> None:
        cells = tuple(
            TableCell(text="항목", row=0, col=c) for c in range(4)
        ) + tuple(
            TableCell(text="값", row=r, col=c)
            for r in range(1, 6) for c in range(4)
        )
        table = Table(
            cells=cells, rows=6, cols=4,
            bbox=BBox(x0=0, y0=0, x1=400, y1=300),
        )
        assert not _is_layout_table(table)


class TestRepairTextFunction:
    """Test _repair_text standalone behavior."""

    def test_cp1252_mojibake_repaired(self) -> None:
        original = "한글"
        garbled = original.encode("utf-8").decode("cp1252")
        result = _repair_text(garbled)
        assert "한글" in result

    def test_latin1_mojibake_repaired(self) -> None:
        original = "보험계약"
        garbled = original.encode("utf-8").decode("latin1")
        result = _repair_text(garbled)
        assert "보험계약" in result

    def test_mixed_cp1252_latin1_byte_repair(self) -> None:
        """Test byte-level repair for text with cp1252-specific AND latin1 chars.

        전자항공권 contains byte 0x90 which is undefined in cp1252.
        A lenient decoder maps it to U+0090 (latin1) while surrounding
        bytes use cp1252 chars (like „ U+201E for 0x84).
        """
        raw_bytes = "전자항공권".encode("utf-8")
        garbled_chars = []
        for b in raw_bytes:
            try:
                garbled_chars.append(bytes([b]).decode("cp1252"))
            except UnicodeDecodeError:
                garbled_chars.append(bytes([b]).decode("latin1"))
        garbled = "".join(garbled_chars)
        result = _repair_text(garbled)
        assert result == "전자항공권"

    def test_garbled_with_space_replacing_nbsp(self) -> None:
        """NBSP in garbled text often gets normalized to space."""
        raw_bytes = "전자".encode("utf-8")  # \xEC\xA0\x84\xEC\x9E\x90
        garbled = raw_bytes.decode("latin1")
        garbled = garbled.replace("\xa0", " ")
        result = _repair_text(garbled)
        assert result == "전자"

    def test_unrepairable_garbled_stripped(self) -> None:
        """Garbled text with no Korean and mojibake hints should be stripped."""
        garbled = "Ã«Â³Â¸Ã­Â"
        result = _repair_text(garbled)
        assert result == ""

    def test_legit_non_korean_accents_preserved(self) -> None:
        """다국어: 정상 악센트/CJK 비한국어 텍스트는 모지바케로 오인해 버리지 않는다.

        과거엔 한국어가 없으면 통째로 버렸으나, 이제는 Â/Ã UTF-8 잔재(진짜
        모지바케)가 있을 때만 버린다. café·Müller·中文 등은 보존돼야 한다.
        """
        for text in ("café résumé Müller", "naïve coördinate", "正常な中文", "plain english"):
            assert _repair_text(text) == text, f"정상 비한국어가 버려짐: {text!r}"

    def test_true_mojibake_stripped_regardless_of_language(self) -> None:
        """Â/Ã 잔재가 있는 복구 불가 모지바케는 언어 무관하게 버린다."""
        for garbled in ("Ã«Â³Â¸Ã­Â", "Ã©tÃ© Ã  la"):
            assert _repair_text(garbled) == "", f"모지바케가 새어듦: {garbled!r}"

    def test_status_ok_noise_removed(self) -> None:
        assert _repair_text("상태 OK") == ""
        assert _repair_text("  상태  OK  ") == ""

    def test_cid_stripped(self) -> None:
        result = _repair_text("Hello (cid:123) world (cid:456)")
        assert "(cid:" not in result
        assert "Hello" in result
        assert "world" in result

    def test_clean_text_unchanged(self) -> None:
        assert _repair_text("정상적인 텍스트") == "정상적인 텍스트"


class TestTextBlockRepairInAssembly:
    """Test that _repair_text is applied to text blocks during assemble_page."""

    def test_text_block_cid_stripped(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(_make_block("(cid:1) 중요한 내용 (cid:2)", y0=100.0),),
            tables=(),
            raw_text="",
            height=800.0,
            images=(),
        )
        md = assemble_page(page, 10.0, config)
        assert "(cid:" not in md
        assert "중요한 내용" in md

    def test_text_block_status_ok_filtered(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(
                _make_block("본문 내용", y0=100.0),
                _make_block("상태 OK", y0=200.0),
                _make_block("추가 내용", y0=300.0),
            ),
            tables=(),
            raw_text="",
            height=800.0,
            images=(),
        )
        md = assemble_page(page, 10.0, config)
        assert "상태 OK" not in md
        assert "본문 내용" in md
        assert "추가 내용" in md

    def test_text_block_mojibake_repaired(self) -> None:
        original = "보험계약"
        garbled = original.encode("utf-8").decode("latin1")
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(_make_block(garbled, y0=100.0),),
            tables=(),
            raw_text="",
            height=800.0,
            images=(),
        )
        md = assemble_page(page, 10.0, config)
        assert "보험계약" in md


class TestUnicodeBulletConversion:
    """Test that unicode bullets are converted in multi-line blocks."""

    def test_multiline_bullets_all_converted(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(
                _make_block("●첫 번째 항목\n●두 번째 항목\n●세 번째 항목", y0=100.0),
            ),
            tables=(),
            raw_text="",
            height=800.0,
            images=(),
        )
        md = assemble_page(page, 10.0, config)
        assert md.count("- ") >= 3
        assert "●" not in md

    def test_sub_bullets_multiline_all_converted(self) -> None:
        config = ParserConfig()
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(
                _make_block("○하위 항목 1\n○하위 항목 2", y0=100.0),
            ),
            tables=(),
            raw_text="",
            height=800.0,
            images=(),
        )
        md = assemble_page(page, 10.0, config)
        assert md.count("  - ") >= 2
        assert "○" not in md


class TestImageCaptionMojibakeRepair:
    """Test that image captions with mojibake are repaired."""

    def test_garbled_caption_repaired(self) -> None:
        original = "전자항공권"
        garbled = original.encode("utf-8").decode("latin1")
        config = ParserConfig(image_output_dir="/uploads/test")
        img = _make_image(caption=garbled)
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(img,),
        )
        md = assemble_page(page, 10.0, config)
        assert "ì" not in md
        assert "전자항공권" in md or "image-1-" in md

    def test_unrepairable_caption_uses_generic_alt(self) -> None:
        config = ParserConfig(image_output_dir="/uploads/test")
        img = _make_image(caption="Ã«Â³Â¸Ã­Â")
        page = PageContent(
            page_num=1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text="",
            height=800.0,
            images=(img,),
        )
        md = assemble_page(page, 10.0, config)
        assert "image-1-abc123" in md


class TestDenseFlightTableNotLayout:
    """Test that dense wide tables (e.g. flight itinerary) are NOT layout."""

    def test_dense_7col_flight_table_not_layout(self) -> None:
        row0 = [
            ("OZ 545", 0), ("ASIANA", 1), ("ICN", 2),
            ("14JUN 10:45", 3), ("T2", 4), ("ECONOMY/K", 5), ("13:00", 6),
        ]
        row1 = [
            ("OZ 546", 0), ("ASIANA", 1), ("PRG", 2),
            ("18JUN 18:50", 3), ("T1", 4), ("ECONOMY/K", 5), ("11:20", 6),
        ]
        cells = tuple(
            TableCell(text=t, row=0, col=c) for t, c in row0
        ) + tuple(
            TableCell(text=t, row=1, col=c) for t, c in row1
        )
        table = Table(
            cells=cells, rows=2, cols=7,
            bbox=BBox(x0=30, y0=200, x1=560, y1=320),
        )
        assert not _is_layout_table(table)
