"""Build a :class:`DocumentAST` from flat ``PageContent`` sequences.

The builder consumes the ``block_id`` / ``parent_id`` annotations
produced by :func:`docforge.processing.heading_hierarchy.assign_hierarchy`
and assembles them into a proper tree structure.

Algorithm overview
------------------
1. Flatten all blocks across pages (page order preserved).
2. Walk blocks linearly with a *level stack*; each heading opens a new
   :class:`SectionNode` and non-heading blocks become leaf children of
   the current section.
3. Tables and images are inserted into the section whose heading is
   vertically closest (by ``bbox.y0``).
4. The resulting tree is wrapped in a root :class:`SectionNode` and
   returned as :class:`DocumentAST`.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docforge.domain.ast_nodes import (
    ASTNode,
    DocumentAST,
    FigureNode,
    FormNode,
    HeadingNode,
    KeyValueNode,
    ParagraphNode,
    SectionNode,
    TableNode,
)
from docforge.domain.enums import BlockType, PageType

if TYPE_CHECKING:
    from docforge.domain.models import PageContent, ParsedImage, Table, TextBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal mutable accumulator (frozen nodes are assembled at the end)
# ---------------------------------------------------------------------------


@dataclass
class _MutableSection:
    """Mutable scratch pad used during tree construction.

    Converted to a frozen :class:`SectionNode` by :meth:`freeze` once
    the tree is fully assembled.
    """

    node_id: str
    heading: HeadingNode | None
    level: int  # heading level (0 for root)
    children: list[ASTNode | "_MutableSection"]
    y0: float = 0.0  # vertical position for table/image placement

    def freeze(self) -> SectionNode:
        """Recursively convert to immutable :class:`SectionNode`."""
        frozen_children: list[ASTNode] = []
        for child in self.children:
            if isinstance(child, _MutableSection):
                frozen_children.append(child.freeze())
            else:
                frozen_children.append(child)
        return SectionNode(
            node_id=self.node_id,
            heading=self.heading,
            children=tuple(frozen_children),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build(
    pages: tuple[PageContent, ...],
    source_file: str = "",
) -> DocumentAST:
    """Build a :class:`DocumentAST` from parsed pages.

    Parameters
    ----------
    pages:
        Tuple of :class:`PageContent` as produced by the parsing pipeline.
    source_file:
        Optional source filename for metadata.

    Returns
    -------
    DocumentAST
        Immutable document tree.
    """
    root = _MutableSection(
        node_id=_make_node_id("root", 0),
        heading=None,
        level=0,
        children=[],
    )

    # Collect tables and images per page for later insertion
    page_tables: list[tuple[int, "Table"]] = []
    page_images: list[tuple[int, "ParsedImage"]] = []

    for page in pages:
        # Skip noise / cover / toc pages
        if page.page_type in (PageType.NOISE,):
            continue

        # COVER and TOC pages: emit as flat paragraphs under a marker section
        if page.page_type in (PageType.COVER, PageType.TOC):
            _add_marker_page(root, page)
            continue

        # Process text blocks
        _process_blocks(root, page)

        # Gather tables and images for deferred insertion
        for table in page.tables:
            page_tables.append((page.page_num, table))
        for image in page.images:
            page_images.append((page.page_num, image))

    # Insert tables into closest sections
    _insert_tables(root, page_tables)

    # Insert images into closest sections
    _insert_images(root, page_images)

    # Freeze the mutable tree
    frozen_root = root.freeze()

    return DocumentAST(
        root=frozen_root,
        source_file=source_file,
        page_count=len(pages),
    )


# ---------------------------------------------------------------------------
# Block processing
# ---------------------------------------------------------------------------

# Heading-like block types and their implicit heading levels
_CLAUSE_LEVEL_MAP: dict[BlockType, int] = {
    BlockType.CLAUSE: 1,
    BlockType.SUBCLAUSE: 2,
    BlockType.ITEM: 3,
}


def _process_blocks(root: _MutableSection, page: "PageContent") -> None:
    """Walk a page's blocks and populate the mutable section tree."""
    # Level stack tracks open sections: [(level, _MutableSection), ...]
    # Rebuild stack from root's existing children (for multi-page continuity)
    stack: list[tuple[int, _MutableSection]] = []
    _rebuild_stack(root, stack)

    for block in page.blocks:
        # Skip layout-noise block types
        if block.block_type in (
            BlockType.PAGE_FOOTER,
            BlockType.PAGE_NUMBER,
            BlockType.PAGE_HEADER,
            BlockType.FOOTNOTE,
        ):
            continue

        if block.block_type == BlockType.HEADING and block.heading_level > 0:
            _open_heading_section(root, stack, block, block.heading_level)
        elif block.block_type in _CLAUSE_LEVEL_MAP:
            level = _CLAUSE_LEVEL_MAP[block.block_type]
            _open_heading_section(root, stack, block, level)
        else:
            # Leaf paragraph
            node_id = block.block_id or _make_node_id(block.text, 0)
            paragraph = ParagraphNode(node_id=node_id, text=block.text.strip())
            if not paragraph.text:
                continue
            target = stack[-1][1] if stack else root
            target.children.append(paragraph)


def _open_heading_section(
    root: _MutableSection,
    stack: list[tuple[int, _MutableSection]],
    block: "TextBlock",
    level: int,
) -> None:
    """Create a new section for a heading block and update the stack."""
    node_id = block.block_id or _make_node_id(block.text, 0)
    heading_node = HeadingNode(
        node_id=node_id + "_h",
        text=block.text.strip(),
        level=min(level, 6),
    )
    section = _MutableSection(
        node_id=node_id,
        heading=heading_node,
        level=level,
        children=[],
        y0=block.bbox.y0,
    )

    # Pop stack until we find a parent with strictly smaller level
    while stack and stack[-1][0] >= level:
        stack.pop()

    parent = stack[-1][1] if stack else root
    parent.children.append(section)
    stack.append((level, section))


def _rebuild_stack(
    root: _MutableSection,
    stack: list[tuple[int, _MutableSection]],
) -> None:
    """Rebuild the level stack from existing children (rightmost path).

    This allows multi-page continuity: blocks on page N+1 attach to
    the last open section from page N.
    """
    current = root
    while current.children:
        last = current.children[-1]
        if isinstance(last, _MutableSection) and last.heading is not None:
            stack.append((last.level, last))
            current = last
        else:
            break


# ---------------------------------------------------------------------------
# Table / image insertion
# ---------------------------------------------------------------------------


def _insert_tables(
    root: _MutableSection,
    page_tables: list[tuple[int, "Table"]],
) -> None:
    """Insert TableNodes (or FormNodes for form-like tables) into the
    section closest by vertical position."""
    if not page_tables:
        return

    sections = _collect_sections(root)
    for page_num, table in page_tables:
        if _is_form_like(table):
            form_node = _table_to_form_node(table, page_num)
            target = _find_closest_section(sections, table.bbox.y0, root)
            target.children.append(form_node)
        else:
            node_id = _make_node_id(f"table_{page_num}_{table.bbox.y0}", page_num)
            table_node = TableNode(node_id=node_id, table=table)
            target = _find_closest_section(sections, table.bbox.y0, root)
            target.children.append(table_node)


def _insert_images(
    root: _MutableSection,
    page_images: list[tuple[int, "ParsedImage"]],
) -> None:
    """Insert FigureNodes into the section closest by vertical position."""
    if not page_images:
        return

    sections = _collect_sections(root)
    for page_num, image in page_images:
        node_id = image.block_id or _make_node_id(
            f"fig_{page_num}_{image.bbox.y0}", page_num,
        )
        figure_node = FigureNode(
            node_id=node_id,
            bbox=image.bbox,
            data=image.data,
            format=image.format,
            caption=image.caption,
            alt_text=image.alt_text,
            page_num=page_num,
        )
        target = _find_closest_section(sections, image.bbox.y0, root)
        target.children.append(figure_node)


def _collect_sections(node: _MutableSection) -> list[_MutableSection]:
    """Flatten all mutable sections in DFS order."""
    result: list[_MutableSection] = [node]
    for child in node.children:
        if isinstance(child, _MutableSection):
            result.extend(_collect_sections(child))
    return result


def _find_closest_section(
    sections: list[_MutableSection],
    y0: float,
    fallback: _MutableSection,
) -> _MutableSection:
    """Return the section whose y0 is closest to (but not exceeding) *y0*.

    Falls back to the last section if all sections are above the target.
    """
    best: _MutableSection = fallback
    best_y: float = -1.0
    for sec in sections:
        if sec.y0 <= y0 and sec.y0 > best_y:
            best = sec
            best_y = sec.y0
    # If no section is above/at y0, use the last section
    if best is fallback and sections:
        best = sections[-1]
    return best


# ---------------------------------------------------------------------------
# COVER / TOC helper
# ---------------------------------------------------------------------------


def _add_marker_page(root: _MutableSection, page: "PageContent") -> None:
    """Add a COVER or TOC page as a flat section with a marker heading."""
    label = "[표지]" if page.page_type == PageType.COVER else "[목차]"
    node_id = _make_node_id(f"{label}_{page.page_num}", page.page_num)
    heading = HeadingNode(node_id=node_id + "_h", text=label, level=2)
    section = _MutableSection(
        node_id=node_id,
        heading=heading,
        level=2,
        children=[],
        y0=0.0,
    )
    sorted_blocks = sorted(page.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
    for block in sorted_blocks:
        text = block.text.strip()
        if text:
            bid = block.block_id or _make_node_id(text, page.page_num)
            section.children.append(ParagraphNode(node_id=bid, text=text))
    # Fall back to raw_text
    if not section.children and page.raw_text.strip():
        fid = _make_node_id(page.raw_text[:50], page.page_num)
        section.children.append(ParagraphNode(node_id=fid, text=page.raw_text.strip()))
    root.children.append(section)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _make_node_id(content: str, page_num: int) -> str:
    """Deterministic 12-char node ID from content + page number."""
    key = f"{content}|{page_num}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Table vs Form heuristic
# ---------------------------------------------------------------------------


def _is_form_like(table: "Table") -> bool:
    """Heuristic: is this table actually a form (key-value layout)?

    A table is form-like when:
    1. It has exactly 2 columns AND most rows have a short label + value, OR
    2. Cell sizes are highly non-uniform (label cells much shorter than value cells)

    This is a basic heuristic -- future iterations can add layout-signal
    scoring.
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
        # Label must be at least 2 chars (single-char cells are data, not labels)
        if len(left_text) < 2 or len(right_text) == 0:
            continue
        if len(left_text) > 30:
            continue
        # Label ends with colon or full-width colon -> strong form signal
        if left_text.endswith(":") or left_text.endswith("："):
            label_value_count += 1
        # Short label (2-15 chars) that is shorter than value -> form-like
        elif len(left_text) <= 15 and len(right_text) > len(left_text):
            label_value_count += 1

    return label_value_count >= table.rows * 0.6


def _table_to_form_node(table: "Table", page_num: int) -> FormNode:
    """Convert a form-like table into a FormNode."""
    fields: list[KeyValueNode] = []
    for row_idx in range(table.rows):
        row_cells = [c for c in table.cells if c.row == row_idx]
        if len(row_cells) != 2:
            continue
        left, right = sorted(row_cells, key=lambda c: c.col)
        key = left.text.strip().rstrip(":").rstrip("：").strip()
        value = right.text.strip()
        if key or value:
            node_id = _make_node_id(f"kv_{page_num}_{row_idx}_{key}", page_num)
            fields.append(KeyValueNode(node_id=node_id, key=key, value=value))

    form_id = _make_node_id(f"form_{page_num}_{table.bbox.y0}", page_num)
    return FormNode(node_id=form_id, fields=tuple(fields))


__all__ = ["build"]
