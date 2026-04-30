"""RAG-friendly chunking module.

Provides four chunking strategies optimized for Korean documents:

* ``by_page``    — one chunk per source page (simplest)
* ``by_title``   — group blocks under same heading subtree
* ``semantic``   — Korean sentence-boundary chunking via Kiwi EF/SF tags;
                   falls back to ``fixed`` when no analyzer is available
* ``fixed``      — fixed-size token buckets (deterministic fallback)

The chunkers depend only on ``domain`` models — clean architecture is
preserved (no infrastructure leaks). External libraries (e.g. Kiwi) are
injected via the ``MorphemeAnalyzer`` Protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from docforge.usecases.chunking.models import Chunk

if TYPE_CHECKING:
    from docforge.domain.models import ParseResult
    from docforge.domain.ports import MorphemeAnalyzer


class Chunker(Protocol):
    """Strategy interface implemented by every chunker module."""

    def chunk(self, parse_result: "ParseResult", **opts: Any) -> list[Chunk]:
        ...


def chunk_document(
    parse_result: "ParseResult",
    strategy: str = "by_title",
    *,
    morpheme_analyzer: "MorphemeAnalyzer | None" = None,
    **opts: Any,
) -> list[Chunk]:
    """Entry point — dispatch to the requested chunker.

    ``opts`` is forwarded to the chunker (e.g. ``max_tokens``,
    ``combine_under_n``). Unknown strategies raise ``ValueError``.
    """
    # Local imports keep optional deps out of import time.
    from docforge.usecases.chunking.by_page_chunker import chunk_by_page
    from docforge.usecases.chunking.by_title_chunker import chunk_by_title
    from docforge.usecases.chunking.fixed_chunker import chunk_fixed
    from docforge.usecases.chunking.semantic_chunker import chunk_semantic

    if strategy == "by_page":
        return chunk_by_page(parse_result, **opts)
    if strategy == "by_title":
        return chunk_by_title(parse_result, **opts)
    if strategy == "semantic":
        return chunk_semantic(parse_result, analyzer=morpheme_analyzer, **opts)
    if strategy == "fixed":
        return chunk_fixed(parse_result, **opts)
    raise ValueError(
        f"Unknown chunking strategy: {strategy!r}. "
        "Expected one of: by_page, by_title, semantic, fixed."
    )


__all__ = ["Chunk", "Chunker", "chunk_document"]
