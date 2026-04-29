"""Core domain models - all immutable (frozen dataclasses)."""

from __future__ import annotations

from dataclasses import dataclass, field

from docforge.domain.enums import BlockType, PageType
from docforge.domain.value_objects import BBox, DocumentProfile, FontInfo


@dataclass(frozen=True)
class TextBlock:
    """A single text block extracted from a PDF page."""

    text: str
    bbox: BBox
    font: FontInfo
    block_type: BlockType = BlockType.TEXT
    heading_level: int = 0
    confidence: float = 1.0


@dataclass(frozen=True)
class TableCell:
    """A single cell in a table."""

    text: str
    row: int
    col: int
    colspan: int = 1
    rowspan: int = 1


@dataclass(frozen=True)
class Table:
    """An extracted table with cells and metadata."""

    cells: tuple[TableCell, ...]
    rows: int
    cols: int
    bbox: BBox
    confidence: float = 1.0
    needs_review: bool = False


@dataclass(frozen=True)
class PageConfidence:
    """Per-page confidence score with breakdown."""

    overall: float  # 0.0 ~ 1.0
    ocr_confidence: float = 1.0
    text_density: float = 1.0
    structure_ratio: float = 1.0
    preprocessing_applied: bool = False


@dataclass(frozen=True)
class PageContent:
    """Parsed content of a single PDF page."""

    page_num: int
    page_type: PageType
    blocks: tuple[TextBlock, ...]
    tables: tuple[Table, ...]
    raw_text: str
    width: float = 0.0
    height: float = 0.0
    confidence: PageConfidence | None = None


@dataclass(frozen=True)
class NoiseStats:
    """Statistics about removed noise elements."""

    headers: int = 0
    footers: int = 0
    page_numbers: int = 0
    toc_pages: int = 0
    toc_entries: int = 0
    watermarks: int = 0


@dataclass(frozen=True)
class ParseStats:
    """Parsing quality metrics."""

    total_pages: int = 0
    parsed_pages: int = 0
    tables_found: int = 0
    tables_need_review: int = 0
    text_blocks: int = 0
    heading_count: int = 0
    empty_line_ratio: float = 0.0
    avg_line_length: float = 0.0
    noise_removed: NoiseStats = field(default_factory=NoiseStats)
    parse_time_ms: float = 0.0


@dataclass(frozen=True)
class Metadata:
    """Document metadata for YAML front matter."""

    source: str
    source_type: str
    pages: int
    parsed_at: str
    parser_version: str
    ocr_used: bool
    tables_extracted: int
    tables_need_review: int
    noise_removed: NoiseStats


@dataclass(frozen=True)
class ParseResult:
    """Complete parsing result."""

    pages: tuple[PageContent, ...]
    markdown: str
    metadata: Metadata
    stats: ParseStats
    profile: DocumentProfile
