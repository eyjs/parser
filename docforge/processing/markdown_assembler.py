"""Final markdown assembly and post-processing.

Combines text blocks and tables in reading order, applies structure markers,
and performs cleanup passes on the resulting markdown.
"""

from __future__ import annotations

import re

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import (
    Metadata,
    PageContent,
    ParsedImage,
    Table,
    TableCell,
    TextBlock,
)
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig
from docforge.infrastructure.metadata import generate_front_matter


_LEADER_DOTS_ONLY_RE = re.compile(r"^[\s·…]+$")
_UNICODE_BULLET_RE = re.compile(r"^[●•][​\s]*")
_UNICODE_SUB_BULLET_RE = re.compile(r"^[○◦][​\s]*")


def _convert_unicode_bullets(text: str) -> str:
    """Convert Unicode bullet characters to markdown list syntax."""
    text = _UNICODE_BULLET_RE.sub("- ", text)
    text = _UNICODE_SUB_BULLET_RE.sub("  - ", text)
    return text


def _is_junk_table(grid: list[list[str]]) -> bool:
    """Detect tables that are TOC leader-dot tables or mostly empty."""
    total_cells = 0
    empty_or_dots = 0
    for row in grid:
        for cell in row:
            total_cells += 1
            if not cell or _LEADER_DOTS_ONLY_RE.match(cell):
                empty_or_dots += 1
    if total_cells == 0:
        return True
    return (empty_or_dots / total_cells) > 0.5


def _table_to_text(grid: list[list[str]]) -> str:
    """Convert a junk table grid to plain text lines."""
    lines: list[str] = []
    for row in grid:
        parts = [c for c in row if c and not _LEADER_DOTS_ONLY_RE.match(c)]
        if parts:
            lines.append(" ".join(parts))
    return "\n".join(lines)


def table_to_markdown(table: Table) -> str:
    """Convert a Table to a markdown table string."""
    if not table.cells or table.rows == 0 or table.cols == 0:
        if table.needs_review:
            return "\n> **Table extraction failed - manual review required**\n"
        return ""

    # Build 2D grid
    grid: list[list[str]] = [["" for _ in range(table.cols)] for _ in range(table.rows)]
    for cell in table.cells:
        if 0 <= cell.row < table.rows and 0 <= cell.col < table.cols:
            cleaned = cell.text.strip()
            cleaned = re.sub(r"\s*\n\s*", " ", cleaned)
            cleaned = re.sub(r"\s{2,}", " ", cleaned)
            cleaned = re.sub(r"·{3,}", "", cleaned).strip()
            cleaned = cleaned.replace("|", "\\|")
            grid[cell.row][cell.col] = cleaned

    if _is_junk_table(grid):
        return _table_to_text(grid)

    lines: list[str] = []

    # Header row
    lines.append("| " + " | ".join(grid[0]) + " |")
    # Separator
    lines.append("| " + " | ".join(["---"] * table.cols) + " |")
    # Data rows
    for row_idx in range(1, table.rows):
        lines.append("| " + " | ".join(grid[row_idx]) + " |")

    review_note = ""
    if table.needs_review:
        review_note = "\n> **Table may have issues - manual review recommended**\n"

    return "\n".join(lines) + review_note


def _deduplicate_tables(tables: tuple[Table, ...]) -> list[Table]:
    """Remove duplicate tables with overlapping bounding boxes.

    When multiple extraction passes detect the same physical table, the
    resulting ``Table`` objects share near-identical bboxes.  We keep the
    first occurrence and drop later ones whose IoU exceeds 0.8.
    """
    if len(tables) <= 1:
        return list(tables)

    kept: list[Table] = []
    for table in tables:
        is_dup = False
        for existing in kept:
            if table.bbox.iou(existing.bbox) > 0.8:
                is_dup = True
                break
        if not is_dup:
            kept.append(table)
    return kept


_COVER_SECTION_HEADER = "## [표지]"
_TOC_SECTION_HEADER = "## [목차]"


def assemble_page(
    page: PageContent,
    avg_font_size: float,
    config: ParserConfig,
) -> str:
    """Assemble a single page into markdown.

    Interleaves text blocks and tables in y-coordinate order (reading order).
    Filters out text blocks that fall within table bounding boxes.

    COVER/TOC pages get a dedicated section header and emit raw block
    text without heading-hierarchy processing.
    """
    if page.page_type == PageType.COVER:
        return _assemble_marker_page(page, _COVER_SECTION_HEADER)
    if page.page_type == PageType.TOC:
        return _assemble_marker_page(page, _TOC_SECTION_HEADER)

    # Build sortable elements: (y_position, type, element)
    elements: list[tuple[float, str, TextBlock | Table | ParsedImage]] = []

    for block in page.blocks:
        elements.append((block.bbox.y0, "text", block))

    deduped_tables = _deduplicate_tables(page.tables)
    for table in deduped_tables:
        elements.append((table.bbox.y0, "table", table))

    for image in page.images:
        elements.append((image.bbox.y0, "image", image))

    elements.sort(key=lambda x: x[0])

    # Collect table regions for overlap filtering
    table_regions = [t.bbox for t in deduped_tables]

    parts: list[str] = []

    image_dir = config.image_output_dir

    for _, elem_type, elem in elements:
        if elem_type == "image":
            from docforge.domain.models import ParsedImage as _PI

            assert isinstance(elem, _PI)
            md_img = _image_to_markdown(elem, image_dir)
            if md_img:
                parts.append("")
                parts.append(md_img)
                parts.append("")
            continue
        if elem_type == "table":
            assert isinstance(elem, Table)
            md_table = table_to_markdown(elem)
            if md_table:
                parts.append("")
                parts.append(md_table)
                parts.append("")

        elif elem_type == "text":
            assert isinstance(elem, TextBlock)

            # Skip text inside table regions
            if _is_inside_table(elem.bbox, table_regions):
                continue

            text = elem.text.strip()
            if not text:
                continue

            if elem.block_type == BlockType.HEADING and elem.heading_level > 0:
                md_level = min(elem.heading_level, 6)
                parts.append("")
                parts.append(f"{'#' * md_level} {text}")
                parts.append("")
            elif elem.block_type == BlockType.CLAUSE:
                parts.append(f"\n{text}")
            elif elem.block_type in (BlockType.SUBCLAUSE, BlockType.ITEM):
                parts.append(f"  {text}")
            else:
                text = _convert_unicode_bullets(text)
                parts.append(text)

    return "\n".join(parts)


def _image_to_markdown(image, image_dir: str | None) -> str:
    """Render a ``ParsedImage`` as visible image link + machine-readable
    placeholder so VLM-generated alt-text can be back-mapped later.

    The HTML comment carries ``id``, ``page``, and bbox — invisible in
    rendered markdown but trivial to grep/replace once a VLM pipeline
    fills in alt-text. Visible part keeps standard markdown image syntax
    so downstream tooling (LangChain/LlamaIndex loaders) recognizes it.
    """
    caption = (image.caption or image.alt_text or "").strip()
    alt_text = caption or f"image-{image.page_num}-{image.block_id}"

    bbox = image.bbox
    comment = (
        f"<!-- IMAGE id={image.block_id} page={image.page_num} "
        f"bbox=[{bbox.x0:.1f},{bbox.y0:.1f},{bbox.x1:.1f},{bbox.y1:.1f}] -->"
    )

    if image_dir and image.data:
        ext = "jpg" if image.format == "jpeg" else image.format
        path = f"{image_dir.rstrip('/')}/page-{image.page_num}-img-{image.block_id}.{ext}"
        visible = f"![{alt_text}]({path})"
    else:
        # No bytes on disk yet — emit a placeholder URI so the markdown
        # round-trip stays valid and post-processors can spot the slot.
        placeholder_uri = (
            f"placeholder://image/{image.block_id}?page={image.page_num}"
        )
        visible = f"![{alt_text}]({placeholder_uri})"

    return f"{comment}\n{visible}"


def _assemble_marker_page(page: PageContent, header: str) -> str:
    """Render a COVER/TOC page as ``header`` followed by its raw lines."""
    sorted_blocks = sorted(page.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
    lines: list[str] = [header, ""]
    for block in sorted_blocks:
        text = block.text.strip()
        if text:
            lines.append(text)
    if len(lines) == 2:
        # No usable blocks — fall back to raw_text if available
        raw = page.raw_text.strip()
        if raw:
            lines.append(raw)
    return "\n".join(lines)


def finalize_markdown(
    page_markdowns: list[str],
    metadata: Metadata,
) -> str:
    """Combine page markdowns with separators, add front matter, and post-process."""
    front_matter = generate_front_matter(metadata)
    body = "\n\n---\n\n".join(page_markdowns)
    body = _post_process(body)
    return front_matter + "\n\n" + body


_LEADER_DOT_RE = re.compile(r"\s*·{3,}\s*")

_STRUCTURE_START_RE = re.compile(
    r"^("
    r"#{1,6}\s"
    r"|제\s*\d+\s*[편장절관조]"
    r"|[①-⑩]\s*"
    r"|\d+\.\d+(?:\.\d+)?\s+"
    r"|\d+\.\s+"
    r"|[가-하]\.\s+"
    r"|-\s+"
    r"|[●○•◦]\s*"
    r"|\|"
    r"|>"
    r"|---"
    r"|$"
    r")"
)

_SENTENCE_END_CHARS = frozenset(".。?!」)）】")


def _post_process(text: str) -> str:
    """Apply cleanup rules to the final markdown."""
    normalized_lines: list[str] = []
    for line in text.split("\n"):
        line = line.replace(" ", " ")
        line = line.replace(" ", " ")
        line = line.replace(" ", " ")
        line = line.replace("　", " ")
        line = _LEADER_DOT_RE.sub(" ", line)
        stripped = line.lstrip()
        leading = line[: len(line) - len(stripped)]
        stripped = re.sub(r" {2,}", " ", stripped)
        normalized_lines.append(leading + stripped)
    text = "\n".join(normalized_lines)

    text = _merge_broken_lines(text)

    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"\n*\n---\n\n*", "\n\n---\n\n", text)
    text = re.sub(r"(\n---\n\s*){2,}", "\n\n---\n\n", text)

    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)

    return text.strip()


def _merge_broken_lines(text: str) -> str:
    """Merge physical line breaks that split a logical sentence."""
    lines = text.split("\n")
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or i + 1 >= len(lines):
            merged.append(line)
            i += 1
            continue

        next_stripped = lines[i + 1].strip()

        if (
            stripped
            and not _STRUCTURE_START_RE.match(stripped)
            and stripped[-1] not in _SENTENCE_END_CHARS
            and next_stripped
            and not _STRUCTURE_START_RE.match(next_stripped)
        ):
            leading = line[: len(line) - len(stripped)]
            prev_char = stripped[-1]
            next_char = next_stripped[0]
            is_hangul_prev = "가" <= prev_char <= "힣"
            is_hangul_next = "가" <= next_char <= "힣"
            sep = "" if is_hangul_prev and is_hangul_next else " "
            merged.append(leading + stripped + sep + next_stripped)
            i += 2
        else:
            merged.append(line)
            i += 1

    return "\n".join(merged)


def _is_inside_table(bbox: BBox, table_regions: list[BBox]) -> bool:
    """Check if a text block overlaps significantly with any table region."""
    bbox_area = (bbox.x1 - bbox.x0) * (bbox.y1 - bbox.y0)
    if bbox_area <= 0:
        return False
    for tr in table_regions:
        ox0 = max(bbox.x0, tr.x0)
        oy0 = max(bbox.y0, tr.y0)
        ox1 = min(bbox.x1, tr.x1)
        oy1 = min(bbox.y1, tr.y1)
        if ox0 < ox1 and oy0 < oy1:
            if (ox1 - ox0) * (oy1 - oy0) / bbox_area > 0.3:
                return True
    return False
