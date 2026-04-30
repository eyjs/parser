"""Heading hierarchy assignment via stack algorithm.

Computes ``block_id`` and ``parent_id`` for a sequence of classified text
blocks so downstream chunkers can navigate the document as a tree.

Headings push onto a stack of ``(level, block_id)`` pairs; same-or-shallower
levels pop the stack first (sibling/uncle handling). Non-heading blocks
attach to the closest heading on top of the stack as children.

The block IDs are deterministic — re-parsing the same PDF yields the same
IDs — by hashing ``(text, page_num, bbox.x0, bbox.y0)`` with MD5 and
truncating to 12 hex chars. This keeps re-runs reproducible without
external state.
"""

from __future__ import annotations

import hashlib

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock


def _make_block_id(block: TextBlock, page_num: int) -> str:
    """Deterministic 12-char block id from text + page + bbox top-left."""
    key = f"{block.text}|{page_num}|{block.bbox.x0:.1f}|{block.bbox.y0:.1f}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def assign_hierarchy(
    blocks: list[TextBlock],
    page_num: int = 0,
) -> list[TextBlock]:
    """Return new blocks with ``block_id`` and ``parent_id`` populated.

    Stack-based algorithm:
      * On a heading, pop while ``top.level >= new.level`` (siblings/uncles
        get popped). Parent is the new stack top after popping. Push self.
      * On non-heading blocks, parent is the current stack top (closest
        ancestor heading). The stack is not modified.

    The function is pure — input blocks are never mutated; new immutable
    ``TextBlock`` instances are returned.
    """
    result: list[TextBlock] = []
    stack: list[tuple[int, str]] = []  # (heading_level, block_id)

    for block in blocks:
        block_id = _make_block_id(block, page_num)

        if block.block_type == BlockType.HEADING:
            level = block.heading_level if block.heading_level > 0 else 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_id = stack[-1][1] if stack else None
            stack.append((level, block_id))
        else:
            parent_id = stack[-1][1] if stack else None

        result.append(
            TextBlock(
                text=block.text,
                bbox=block.bbox,
                font=block.font,
                block_type=block.block_type,
                heading_level=block.heading_level,
                confidence=block.confidence,
                block_id=block_id,
                parent_id=parent_id,
            )
        )
    return result


__all__ = ["assign_hierarchy"]
