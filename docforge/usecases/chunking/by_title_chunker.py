"""Title-aware chunker — groups blocks by heading subtree.

Walks all pages in document order, tracks the current heading path via
the ``parent_id`` chain set by ``processing.heading_hierarchy``, and emits
one chunk per leaf heading group. Oversized groups are split into
``max_tokens``-sized sub-chunks with the heading path repeated on each.
Tiny groups can be combined with adjacent siblings via ``combine_under_n``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docforge.domain.enums import BlockType
from docforge.usecases.chunking.models import Chunk

if TYPE_CHECKING:
    from docforge.domain.models import ParseResult, TextBlock


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _build_heading_path(
    block: "TextBlock",
    headings_by_id: dict[str, "TextBlock"],
) -> tuple[str, ...]:
    """Walk parent_id chain through heading blocks to build root→leaf path."""
    path: list[str] = []
    current_id = block.block_id if block.block_type == BlockType.HEADING else block.parent_id
    visited: set[str] = set()
    while current_id and current_id in headings_by_id and current_id not in visited:
        visited.add(current_id)
        h = headings_by_id[current_id]
        path.append(h.text.strip())
        current_id = h.parent_id
    return tuple(reversed(path))


def chunk_by_title(
    parse_result: "ParseResult",
    *,
    max_tokens: int = 500,
    combine_under_n: int = 0,
    **_opts: object,
) -> list[Chunk]:
    """Group blocks under the same heading path into chunks."""
    source = parse_result.metadata.source

    # Flatten + index headings so we can resolve heading_path quickly.
    flat: list[tuple["TextBlock", int]] = []
    headings_by_id: dict[str, TextBlock] = {}
    for page in parse_result.pages:
        for block in page.blocks:
            if not block.text.strip():
                continue
            flat.append((block, page.page_num))
            if block.block_type == BlockType.HEADING and block.block_id:
                headings_by_id[block.block_id] = block

    if not flat:
        return []

    # Group consecutive blocks sharing the same heading path.
    groups: list[tuple[tuple[str, ...], list[tuple[TextBlock, int]]]] = []
    for block, page_num in flat:
        path = _build_heading_path(block, headings_by_id)
        if groups and groups[-1][0] == path:
            groups[-1][1].append((block, page_num))
        else:
            groups.append((path, [(block, page_num)]))

    # Optional: merge tiny groups into the previous one — only when they
    # share a heading subtree (same path, or current is a descendant of prev).
    # This prevents mixing semantically unrelated sections together.
    if combine_under_n > 0:
        merged: list[tuple[tuple[str, ...], list[tuple[TextBlock, int]]]] = []
        for path, items in groups:
            tokens = _estimate_tokens(" ".join(b.text for b, _ in items)) if items else 0
            if merged and tokens < combine_under_n:
                prev_path, prev_items = merged[-1]
                same_subtree = (
                    path == prev_path
                    or (len(path) > len(prev_path) and path[: len(prev_path)] == prev_path)
                    or (len(prev_path) > len(path) and prev_path[: len(path)] == path)
                )
                if same_subtree:
                    use_path = prev_path if len(prev_path) <= len(path) else path
                    merged[-1] = (use_path, prev_items + items)
                    continue
            merged.append((path, items))
        groups = merged

    chunks: list[Chunk] = []
    chunk_idx = 0

    for path, items in groups:
        path_prefix = " > ".join(path)

        # Split oversize group into sub-chunks while preserving heading_path.
        buf_text: list[str] = []
        buf_block_ids: list[str] = []
        buf_pages: set[int] = set()
        buf_tokens = 0

        def flush() -> None:
            nonlocal chunk_idx, buf_tokens
            if not buf_text:
                return
            body = "\n".join(buf_text)
            text = f"{path_prefix}\n\n{body}" if path_prefix else body
            chunks.append(
                Chunk(
                    chunk_id=f"{source}-{chunk_idx:04d}",
                    text=text,
                    block_ids=tuple(buf_block_ids),
                    page_numbers=tuple(sorted(buf_pages)),
                    heading_path=path,
                    chunk_type="by_title",
                    token_count=_estimate_tokens(text),
                )
            )
            chunk_idx += 1
            buf_text.clear()
            buf_block_ids.clear()
            buf_pages.clear()
            buf_tokens = 0

        for block, page_num in items:
            tokens = _estimate_tokens(block.text)
            if buf_tokens + tokens > max_tokens and buf_text:
                flush()
            buf_text.append(block.text)
            if block.block_id is not None:
                buf_block_ids.append(block.block_id)
            buf_pages.add(page_num)
            buf_tokens += tokens

        flush()

    return chunks


__all__ = ["chunk_by_title"]
