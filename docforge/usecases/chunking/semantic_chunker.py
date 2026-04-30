"""Korean semantic chunker — Kiwi EF/SF tag based sentence splitting.

The text from all body blocks is concatenated, then segmented into
sentences at every ``EF`` (어말 종결어미) or ``SF`` (마침표/물음표/느낌표)
token boundary returned by the injected ``MorphemeAnalyzer``. Sentences
are accumulated greedily up to ``max_tokens``; the buffer flushes
**before** an oversized sentence is added (max_tokens is a soft cap, not
a hard cut).

When no analyzer is available — or the analyzer reports unavailable —
the function falls back to ``fixed_chunker`` so callers always get a
valid chunk list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docforge.usecases.chunking.fixed_chunker import chunk_fixed
from docforge.usecases.chunking.models import Chunk

if TYPE_CHECKING:
    from docforge.domain.models import ParseResult, TextBlock
    from docforge.domain.ports import MorphemeAnalyzer


_SENTENCE_BOUNDARY_TAGS = {"EF", "SF"}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _split_sentences(
    text: str,
    analyzer: "MorphemeAnalyzer",
) -> list[tuple[str, int, int]]:
    """Return ``[(sentence, start, end)]`` using EF/SF morpheme boundaries.

    Positions are absolute offsets into ``text`` so callers don't need to
    re-find sentences (which would be O(n*m) and fragile for repeated text).
    """
    tokens = analyzer.tokenize(text)
    sentences: list[tuple[str, int, int]] = []
    cursor = 0
    for tok in tokens:
        if tok.tag in _SENTENCE_BOUNDARY_TAGS:
            end = tok.start + tok.length
            raw = text[cursor:end]
            sent = raw.strip()
            if sent:
                # Compensate for stripped leading whitespace.
                lead = len(raw) - len(raw.lstrip())
                start = cursor + lead
                sentences.append((sent, start, start + len(sent)))
            cursor = end
    if cursor < len(text):
        raw = text[cursor:]
        tail = raw.strip()
        if tail:
            lead = len(raw) - len(raw.lstrip())
            start = cursor + lead
            sentences.append((tail, start, start + len(tail)))
    return sentences


def _flatten_text_with_blocks(
    parse_result: "ParseResult",
) -> tuple[str, list[tuple[int, int, "TextBlock", int]]]:
    """Concatenate all block text; return joined text + (start, end, block, page)."""
    parts: list[str] = []
    spans: list[tuple[int, int, "TextBlock", int]] = []
    cursor = 0
    for page in parse_result.pages:
        for block in page.blocks:
            if not block.text.strip():
                continue
            text = block.text
            spans.append((cursor, cursor + len(text), block, page.page_num))
            parts.append(text)
            cursor += len(text) + 1  # +1 for the joining "\n"
    joined = "\n".join(parts)
    return joined, spans


def _spans_in_range(
    spans: list[tuple[int, int, "TextBlock", int]],
    start: int,
    end: int,
) -> tuple[list[str], list[int]]:
    block_ids: list[str] = []
    pages: list[int] = []
    for s, e, block, page_num in spans:
        if e <= start or s >= end:
            continue
        if block.block_id is not None and block.block_id not in block_ids:
            block_ids.append(block.block_id)
        if page_num not in pages:
            pages.append(page_num)
    return block_ids, sorted(pages)


def chunk_semantic(
    parse_result: "ParseResult",
    *,
    analyzer: "MorphemeAnalyzer | None" = None,
    max_tokens: int = 500,
    **opts: object,
) -> list[Chunk]:
    """Split into chunks at Korean sentence boundaries (EF/SF)."""
    if analyzer is None or not analyzer.is_available():
        return chunk_fixed(parse_result, max_tokens=max_tokens, **opts)

    source = parse_result.metadata.source
    joined, spans = _flatten_text_with_blocks(parse_result)
    if not joined.strip():
        return []

    sentences = _split_sentences(joined, analyzer)
    if not sentences:
        return chunk_fixed(parse_result, max_tokens=max_tokens, **opts)

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    buf_start = sentences[0][1]
    chunk_idx = 0
    last_end = buf_start

    def flush(end_pos: int) -> None:
        nonlocal chunk_idx, buf_tokens, buf_start
        if not buf:
            return
        text = " ".join(buf)
        block_ids, pages = _spans_in_range(spans, buf_start, end_pos)
        chunks.append(
            Chunk(
                chunk_id=f"{source}-{chunk_idx:04d}",
                text=text,
                block_ids=tuple(block_ids),
                page_numbers=tuple(pages),
                heading_path=(),
                chunk_type="semantic",
                token_count=_estimate_tokens(text),
            )
        )
        chunk_idx += 1
        buf.clear()
        buf_tokens = 0
        buf_start = end_pos

    for sent, start, end in sentences:
        sent_tokens = _estimate_tokens(sent)
        # Cap respects "직전에서 끊는다" — flush before pushing the new one.
        if buf_tokens + sent_tokens > max_tokens and buf:
            flush(start)
            buf_start = start
        buf.append(sent)
        buf_tokens += sent_tokens
        last_end = end

    flush(last_end)
    return chunks


__all__ = ["chunk_semantic"]
