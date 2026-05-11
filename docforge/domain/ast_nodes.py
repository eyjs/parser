"""Document AST node hierarchy -- immutable tree representation.

Every node is a frozen dataclass. The tree is built by
:func:`docforge.processing.ast_builder.build` from flat
``PageContent`` sequences and consumed by renderers
(e.g. :mod:`docforge.processing.ast_markdown_renderer`).

Node IDs are deterministic 12-char MD5 hex strings derived from
the source ``block_id`` so re-parsing yields reproducible trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union

from docforge.domain.models import Table
from docforge.domain.value_objects import BBox


class ASTNodeType(str, Enum):
    """Semantic type tag carried by every AST node."""

    DOCUMENT = "document"
    SECTION = "section"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    LIST = "list"
    LIST_ITEM = "list_item"
    FORM = "form"
    KEY_VALUE = "key_value"


# ---------------------------------------------------------------------------
# Leaf nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadingNode:
    """A heading element with explicit depth level (1--6)."""

    node_id: str
    text: str
    level: int  # 1 -- 6
    node_type: ASTNodeType = field(default=ASTNodeType.HEADING, init=False)


@dataclass(frozen=True)
class ParagraphNode:
    """A plain-text paragraph."""

    node_id: str
    text: str
    node_type: ASTNodeType = field(default=ASTNodeType.PARAGRAPH, init=False)


@dataclass(frozen=True)
class TableNode:
    """A table extracted from the document.

    Wraps the existing :class:`~docforge.domain.models.Table` model so
    that table rendering logic can be shared with the legacy assembler.
    """

    node_id: str
    table: Table
    node_type: ASTNodeType = field(default=ASTNodeType.TABLE, init=False)


@dataclass(frozen=True)
class FigureNode:
    """An image / figure with optional caption and alt text."""

    node_id: str
    bbox: BBox
    data: bytes
    format: str  # "png" | "jpeg"
    caption: str | None
    alt_text: str | None
    page_num: int
    node_type: ASTNodeType = field(default=ASTNodeType.FIGURE, init=False)


@dataclass(frozen=True)
class KeyValueNode:
    """A single key-value pair extracted from a form or structured layout."""

    node_id: str
    key: str
    value: str
    node_type: ASTNodeType = field(default=ASTNodeType.KEY_VALUE, init=False)


@dataclass(frozen=True)
class FormNode:
    """A structured form region containing key-value pairs.

    Distinguishes structured key-value layouts (boarding passes, invoices,
    application forms) from regular data tables.
    """

    node_id: str
    fields: tuple[KeyValueNode, ...]
    node_type: ASTNodeType = field(default=ASTNodeType.FORM, init=False)


# ---------------------------------------------------------------------------
# Composite nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionNode:
    """A logical section that may contain a heading and nested children.

    ``heading`` is ``None`` for the implicit root section of a headingless
    document or for structural groupings that have no explicit title.

    ``children`` may contain any AST node type including nested
    :class:`SectionNode` instances.
    """

    node_id: str
    heading: HeadingNode | None = None
    children: tuple[Any, ...] = ()
    node_type: ASTNodeType = field(default=ASTNodeType.SECTION, init=False)


# ---------------------------------------------------------------------------
# Document root
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentAST:
    """Top-level container -- a single document parsed into an AST.

    ``root`` is always a :class:`SectionNode` whose ``heading`` is
    ``None`` (the implicit document-level wrapper).
    """

    root: SectionNode
    source_file: str = ""
    page_count: int = 0
    node_type: ASTNodeType = field(default=ASTNodeType.DOCUMENT, init=False)


# Runtime-safe type alias for type annotations in other modules.
# Use in TYPE_CHECKING blocks: ``ASTNode`` covers all concrete node types.
ASTNode = Union[
    HeadingNode, ParagraphNode, TableNode, FigureNode,
    SectionNode, FormNode, KeyValueNode,
]

__all__ = [
    "ASTNodeType",
    "HeadingNode",
    "ParagraphNode",
    "TableNode",
    "FigureNode",
    "KeyValueNode",
    "FormNode",
    "SectionNode",
    "DocumentAST",
    "ASTNode",
]
