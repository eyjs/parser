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
_CID_PATTERN = re.compile(r"\(cid:\s*\d+\s*\)")
_STATUS_NOISE_RE = re.compile(r"^\s*상태\s*OK\s*$", re.IGNORECASE)
_MOJIBAKE_HINT = re.compile(r"[\xc0-\xff]")
_KOREAN_RE = re.compile(r"[가-힣]")
_ENCODING_PAIRS: list[tuple[str, str]] = [
    ("latin1", "utf-8"),
    ("cp1252", "utf-8"),
]


def _repair_text(text: str) -> str:
    """Strip CID references, fix mojibake, and remove noise from text."""
    text = _CID_PATTERN.sub("", text)
    text = re.sub(r"  +", " ", text).strip()
    if _STATUS_NOISE_RE.match(text):
        return ""
    if _MOJIBAKE_HINT.search(text):
        for src, tgt in _ENCODING_PAIRS:
            try:
                raw = text.encode(src, errors="ignore")
                candidate = raw.decode(tgt, errors="strict")
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
            if (
                len(candidate) >= len(text) * 0.2
                and _KOREAN_RE.search(candidate)
                and not _MOJIBAKE_HINT.search(candidate)
            ):
                return candidate.strip()
    return text


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


def _clean_cell_text(text: str) -> str:
    """Clean a single table cell: strip CID, noise, normalize whitespace."""
    cleaned = _CID_PATTERN.sub("", text).strip()
    if _STATUS_NOISE_RE.match(cleaned):
        return ""
    cleaned = re.sub(r"\s*\n\s*", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"·{3,}", "", cleaned).strip()
    return cleaned


def _is_form_like(table: Table) -> bool:
    """Detect tables that are actually key-value forms.

    Matches 2-column tables where >= 60% of rows have a short label
    in the left column and a value in the right column.
    """
    if table.cols != 2 or table.rows < 2:
        return False

    label_value_count = 0
    for row_idx in range(table.rows):
        row_cells = [c for c in table.cells if c.row == row_idx]
        if len(row_cells) != 2:
            continue
        left, right = sorted(row_cells, key=lambda c: c.col)
        left_text = left.text.strip()
        right_text = right.text.strip()
        if len(left_text) < 2 or len(right_text) == 0:
            continue
        if len(left_text) > 30:
            continue
        if left_text.endswith(":") or left_text.endswith("："):
            label_value_count += 1
        elif len(left_text) <= 15 and len(right_text) > len(left_text):
            label_value_count += 1

    return label_value_count >= table.rows * 0.6


def _form_to_text(table: Table) -> str:
    """Render a form-like table as key: value lines."""
    lines: list[str] = []
    for row_idx in range(table.rows):
        row_cells = [c for c in table.cells if c.row == row_idx]
        if len(row_cells) != 2:
            texts = [_clean_cell_text(c.text) for c in sorted(row_cells, key=lambda c: c.col)]
            line = " ".join(t for t in texts if t)
            if line:
                lines.append(line)
            continue
        left, right = sorted(row_cells, key=lambda c: c.col)
        key = _clean_cell_text(left.text).rstrip(":").rstrip("：").strip()
        value = _clean_cell_text(right.text)
        if key and value:
            lines.append(f"**{key}**: {value}")
        elif key:
            lines.append(f"**{key}**")
        elif value:
            lines.append(value)
    return "\n\n".join(lines)


def _is_layout_table(table: Table) -> bool:
    """Detect tables that are really document layout containers.

    A layout table wraps an entire page section rather than presenting
    tabular data. Signals:
    - Wide tables (5+ cols) with very few rows — pdfplumber layout artifact
    - Few columns (1-3) with highly variable cell text lengths
    - Some cells contain long paragraph-like text (> 80 chars)
    """
    cell_lengths = [len(c.text.strip()) for c in table.cells if c.text.strip()]
    if not cell_lengths:
        return False

    long_cells = sum(1 for length in cell_lengths if length > 80)

    if table.cols >= 5 and table.rows <= 3:
        if table.cols >= 7:
            return True
        non_empty = len(cell_lengths)
        total = table.rows * table.cols
        if non_empty < total * 0.5 or long_cells >= 1:
            return True

    if table.cols > 4:
        return False

    if long_cells == 0:
        return False

    if table.cols <= 2 and long_cells >= len(cell_lengths) * 0.3:
        return True

    if table.cols <= 4 and table.rows >= 5:
        mean_len = sum(cell_lengths) / len(cell_lengths)
        if mean_len == 0:
            return False
        variance = sum((x - mean_len) ** 2 for x in cell_lengths) / len(cell_lengths)
        cv = (variance ** 0.5) / mean_len
        if cv > 1.0 and long_cells >= 2:
            return True

    return False


def _layout_table_to_text(table: Table) -> str:
    """Render a layout table as structured text preserving row order."""
    lines: list[str] = []
    for row_idx in range(table.rows):
        row_cells = sorted(
            [c for c in table.cells if c.row == row_idx],
            key=lambda c: c.col,
        )
        for cell in row_cells:
            text = _clean_cell_text(cell.text)
            if text:
                lines.append(text)
    return "\n\n".join(lines)


def table_to_markdown(table: Table) -> str:
    """Convert a Table to a markdown table string."""
    if not table.cells or table.rows == 0 or table.cols == 0:
        if table.needs_review:
            return "\n> **Table extraction failed - manual review required**\n"
        return ""

    if _is_form_like(table):
        return _form_to_text(table)

    if _is_layout_table(table):
        return _layout_table_to_text(table)

    # Build 2D grid — propagate merged cell values across their span
    grid: list[list[str]] = [["" for _ in range(table.cols)] for _ in range(table.rows)]
    for cell in table.cells:
        if 0 <= cell.row < table.rows and 0 <= cell.col < table.cols:
            cleaned = _clean_cell_text(cell.text)
            cleaned = cleaned.replace("|", "\\|")
            if not cleaned:
                continue
            row_end = min(cell.row + cell.rowspan, table.rows)
            col_end = min(cell.col + cell.colspan, table.cols)
            for r in range(cell.row, row_end):
                for c in range(cell.col, col_end):
                    grid[r][c] = cleaned

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


def _table_content_hash(table: Table) -> str:
    """Content-based hash for a table -- catches duplicates with different bboxes."""
    import hashlib

    cell_texts = sorted(c.text.strip() for c in table.cells if c.text.strip())
    return hashlib.md5("|".join(cell_texts).encode()).hexdigest()


def _deduplicate_tables(tables: tuple[Table, ...]) -> list[Table]:
    """Remove duplicate tables with overlapping bboxes OR identical content.

    Two dedup strategies are applied:
    1. **IoU-based** (existing): IoU > 0.8 between bboxes.
    2. **Content-hash** (Phase 2): identical cell-text sets even when
       bboxes differ (e.g. cross-page merge artefacts).

    The first occurrence is always kept.
    """
    if len(tables) <= 1:
        return list(tables)

    kept: list[Table] = []
    seen_hashes: set[str] = set()

    for table in tables:
        # Check IoU overlap with already-kept tables
        is_dup = False
        for existing in kept:
            if table.bbox.iou(existing.bbox) > 0.8:
                is_dup = True
                break
        if is_dup:
            continue

        # Check content hash
        content_hash = _table_content_hash(table)
        if content_hash in seen_hashes:
            continue

        kept.append(table)
        seen_hashes.add(content_hash)

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

    page_height = getattr(page, "height", 0.0) or 0.0

    for _, elem_type, elem in elements:
        if elem_type == "image":
            from docforge.domain.models import ParsedImage as _PI

            assert isinstance(elem, _PI)

            # If alt_text is present, output text instead of image reference
            extracted = (elem.alt_text or "").strip()
            if extracted:
                extracted = _repair_text(extracted)
                if not extracted:
                    continue
                classification = _classify_image_text(
                    extracted, elem, page_height,
                )
                parts.append("")
                if classification == "heading":
                    parts.append(f"# {extracted}")
                else:
                    parts.append(extracted)
                parts.append("")
            else:
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

            text = _repair_text(text)
            if not text:
                continue

            if elem.block_type in (
                BlockType.PAGE_FOOTER,
                BlockType.PAGE_NUMBER,
                BlockType.PAGE_HEADER,
            ):
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
            elif elem.block_type == BlockType.LIST:
                parts.append(f"- {text}")
            else:
                text = _convert_unicode_bullets(text)
                parts.append(text)

    return "\n".join(parts)


def _classify_image_text(
    text: str,
    image: ParsedImage,
    page_height: float,
) -> str:
    """Classify extracted image text as heading or body.

    Returns ``"heading"`` when ALL of these conditions hold:
      - text length <= 60 characters
      - no period (``.``) or comma (``,``)
      - image is in the top 20 % of the page (``bbox.y0 / page_height <= 0.2``)

    Otherwise returns ``"body"``.
    """
    if len(text) > 60:
        return "body"
    if "." in text or "," in text:
        return "body"
    if page_height > 0 and image.bbox.y0 / page_height > 0.2:
        return "body"
    return "heading"


def _image_to_markdown(image, image_dir: str | None) -> str:
    """Render a ``ParsedImage`` as standard markdown image syntax."""
    caption = (image.caption or image.alt_text or "").strip()
    alt_text = caption or f"image-{image.page_num}-{image.block_id}"

    if image_dir and image.data:
        ext = "jpg" if image.format == "jpeg" else image.format
        path = f"{image_dir.rstrip('/')}/page-{image.page_num}-img-{image.block_id}.{ext}"
        return f"![{alt_text}]({path})"

    placeholder_uri = (
        f"placeholder://image/{image.block_id}?page={image.page_num}"
    )
    return f"![{alt_text}]({placeholder_uri})"


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
