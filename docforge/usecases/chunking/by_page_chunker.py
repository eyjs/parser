"""Page-level chunker — one chunk per parsed page.

The simplest strategy. Useful as a baseline and for documents whose
semantic units already align to page boundaries (e.g. presentation decks,
single-topic memos).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docforge.usecases.chunking.models import Chunk

if TYPE_CHECKING:
    from docforge.domain.models import ParseResult


def _estimate_tokens(text: str) -> int:
    """Korean rough heuristic: ~2 chars/token."""
    return max(1, len(text) // 2)


def chunk_by_page(parse_result: "ParseResult", **_opts: object) -> list[Chunk]:
    """Return one ``Chunk`` per ``PageContent`` in the result."""
    source = parse_result.metadata.source
    chunks: list[Chunk] = []

    for idx, page in enumerate(parse_result.pages):
        block_texts = [b.text for b in page.blocks if b.text.strip()]
        text = "\n".join(block_texts)
        if not text.strip():
            continue
        block_ids = tuple(
            b.block_id for b in page.blocks if b.block_id is not None
        )
        chunks.append(
            Chunk(
                chunk_id=f"{source}-{idx:04d}",
                text=text,
                block_ids=block_ids,
                page_numbers=(page.page_num,),
                heading_path=(),
                chunk_type="by_page",
                token_count=_estimate_tokens(text),
            )
        )
    return chunks


__all__ = ["chunk_by_page"]
