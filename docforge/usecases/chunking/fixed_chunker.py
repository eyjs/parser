"""Fixed-size chunker — deterministic fallback.

Concatenates all block text in document order then slices into roughly
equal token windows. Block-id and page-number metadata are best-effort:
each output chunk records every block whose text spans the slice range.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docforge.usecases.chunking.models import Chunk

if TYPE_CHECKING:
    from docforge.domain.models import ParseResult, TextBlock


def _flatten(parse_result: "ParseResult") -> list[tuple["TextBlock", int]]:
    out: list[tuple[TextBlock, int]] = []
    for page in parse_result.pages:
        for block in page.blocks:
            if block.text.strip():
                out.append((block, page.page_num))
    return out


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def chunk_fixed(
    parse_result: "ParseResult",
    *,
    max_tokens: int = 500,
    **_opts: object,
) -> list[Chunk]:
    """Slice document into fixed ``max_tokens``-sized chunks (rough)."""
    source = parse_result.metadata.source
    flat = _flatten(parse_result)
    if not flat:
        return []

    chunks: list[Chunk] = []
    buf_text: list[str] = []
    buf_block_ids: list[str] = []
    buf_pages: set[int] = set()
    buf_tokens = 0
    chunk_idx = 0

    def flush() -> None:
        nonlocal chunk_idx, buf_tokens
        if not buf_text:
            return
        text = "\n".join(buf_text)
        chunks.append(
            Chunk(
                chunk_id=f"{source}-{chunk_idx:04d}",
                text=text,
                block_ids=tuple(buf_block_ids),
                page_numbers=tuple(sorted(buf_pages)),
                heading_path=(),
                chunk_type="fixed",
                token_count=_estimate_tokens(text),
            )
        )
        chunk_idx += 1
        buf_text.clear()
        buf_block_ids.clear()
        buf_pages.clear()
        buf_tokens = 0

    for block, page_num in flat:
        block_tokens = _estimate_tokens(block.text)
        if buf_tokens + block_tokens > max_tokens and buf_text:
            flush()
        buf_text.append(block.text)
        if block.block_id is not None:
            buf_block_ids.append(block.block_id)
        buf_pages.add(page_num)
        buf_tokens += block_tokens

    flush()
    return chunks


__all__ = ["chunk_fixed"]
