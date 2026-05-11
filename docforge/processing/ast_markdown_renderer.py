"""Render a :class:`DocumentAST` to Markdown text.

This renderer produces output compatible with the legacy
:func:`markdown_assembler.assemble_page` so that switching to the AST
path is transparent to downstream consumers.

Usage::

    from docforge.processing.ast_builder import build
    from docforge.processing.ast_markdown_renderer import render

    ast = build(pages, source_file="doc.pdf")
    markdown = render(ast)
"""

from __future__ import annotations

from docforge.domain.ast_nodes import (
    ASTNodeType,
    DocumentAST,
    FigureNode,
    HeadingNode,
    ParagraphNode,
    SectionNode,
    TableNode,
)
from docforge.processing.markdown_assembler import table_to_markdown


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(ast: DocumentAST) -> str:
    """Render the full document AST to a Markdown string.

    Parameters
    ----------
    ast:
        The document tree produced by :func:`ast_builder.build`.

    Returns
    -------
    str
        Markdown text with ``---`` page separators removed (the AST
        is a logical structure, not a physical-page structure).
    """
    parts = _render_section(ast.root, is_root=True)
    text = "\n".join(parts)
    # Collapse excessive blank lines
    while "\n\n\n\n" in text:
        text = text.replace("\n\n\n\n", "\n\n\n")
    return text.strip()


# ---------------------------------------------------------------------------
# Node dispatchers
# ---------------------------------------------------------------------------


def _render_node(node: object) -> list[str]:
    """Dispatch to the appropriate renderer based on node type."""
    if isinstance(node, SectionNode):
        return _render_section(node)
    if isinstance(node, HeadingNode):
        return _render_heading(node)
    if isinstance(node, ParagraphNode):
        return _render_paragraph(node)
    if isinstance(node, TableNode):
        return _render_table(node)
    if isinstance(node, FigureNode):
        return _render_figure(node)
    # Unknown node type -- skip silently
    return []


def _render_section(node: SectionNode, *, is_root: bool = False) -> list[str]:
    """Render a section: optional heading followed by children."""
    parts: list[str] = []

    if node.heading is not None and not is_root:
        parts.extend(_render_heading(node.heading))

    for child in node.children:
        child_parts = _render_node(child)
        if child_parts:
            parts.extend(child_parts)

    return parts


def _render_heading(node: HeadingNode) -> list[str]:
    """Render a heading as ``# Text`` with appropriate level."""
    level = min(max(node.level, 1), 6)
    prefix = "#" * level
    return ["", f"{prefix} {node.text}", ""]


def _render_paragraph(node: ParagraphNode) -> list[str]:
    """Render a paragraph as plain text."""
    text = node.text.strip()
    if not text:
        return []
    return [text]


def _render_table(node: TableNode) -> list[str]:
    """Render a table using the shared ``table_to_markdown`` formatter."""
    md = table_to_markdown(node.table)
    if not md:
        return []
    return ["", md, ""]


def _render_figure(node: FigureNode) -> list[str]:
    """Render a figure as a Markdown image reference.

    If ``alt_text`` is present the figure was already OCR'd / VLM'd and
    we emit the extracted text instead of an image tag (matching the
    legacy assembler behaviour).
    """
    extracted = (node.alt_text or "").strip()
    if extracted:
        # Mirror legacy: short text at top of page becomes heading-like,
        # but we keep it simple here -- just emit as text.
        return ["", extracted, ""]

    caption = (node.caption or "").strip()
    alt = caption or f"image-{node.page_num}-{node.node_id}"
    # Placeholder URI matching legacy assembler
    uri = f"placeholder://image/{node.node_id}?page={node.page_num}"
    return ["", f"![{alt}]({uri})", ""]


__all__ = ["render"]
