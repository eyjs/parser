"""JSON / JSON Lines serialization for ``Chunk`` objects."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docforge.usecases.chunking.models import Chunk


def chunk_to_dict(chunk: "Chunk") -> dict[str, object]:
    """Convert a single chunk to a serializable dict."""
    return {
        "chunk_id": chunk.chunk_id,
        "text": chunk.text,
        "block_ids": list(chunk.block_ids),
        "page_numbers": list(chunk.page_numbers),
        "heading_path": list(chunk.heading_path),
        "chunk_type": chunk.chunk_type,
        "token_count": chunk.token_count,
        "metadata": dict(chunk.metadata),
    }


def chunks_to_dicts(chunks: list["Chunk"]) -> list[dict[str, object]]:
    """Convert a chunk list to a list of dicts."""
    return [chunk_to_dict(c) for c in chunks]


def chunks_to_jsonl(chunks: list["Chunk"]) -> str:
    """Serialize chunks to JSON Lines (one chunk dict per line).

    Includes a trailing newline so downstream readers (pandas, jq, line-based
    streaming parsers) consume the last record cleanly.
    """
    if not chunks:
        return ""
    return "\n".join(
        json.dumps(chunk_to_dict(c), ensure_ascii=False) for c in chunks
    ) + "\n"


__all__ = ["chunk_to_dict", "chunks_to_dicts", "chunks_to_jsonl"]
