"""Immutable chunk model for RAG-friendly document segmentation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chunk:
    """A single retrieval-ready segment of a parsed document.

    Frozen for safety (immutable text/ids/heading_path). ``metadata`` is
    a mutable dict to allow extension fields without redefining the class —
    note this means ``Chunk`` is NOT hashable; use it as a transport object
    (serialize / iterate), not as a set/dict key. Treat metadata as
    append-only — never mutate after construction in chunker code paths.
    """

    chunk_id: str
    text: str
    block_ids: tuple[str, ...]
    page_numbers: tuple[int, ...]
    heading_path: tuple[str, ...]
    chunk_type: str  # "by_title" | "by_page" | "semantic" | "fixed"
    token_count: int
    metadata: dict[str, str] = field(default_factory=dict)


__all__ = ["Chunk"]
