"""Tests for usecases.chunking — by_page / by_title / semantic / fixed."""

from __future__ import annotations

import json

import pytest

from docforge.domain.enums import BlockType, DocumentComplexity, PageType
from docforge.domain.models import (
    Metadata,
    NoiseStats,
    PageContent,
    ParseResult,
    ParseStats,
    TextBlock,
)
from docforge.domain.ports import MorphemeToken
from docforge.domain.value_objects import BBox, DocumentProfile, FontInfo
from docforge.processing.heading_hierarchy import assign_hierarchy
from docforge.usecases.chunking import Chunk, chunk_document
from docforge.usecases.chunking.by_page_chunker import chunk_by_page
from docforge.usecases.chunking.by_title_chunker import chunk_by_title
from docforge.usecases.chunking.fixed_chunker import chunk_fixed
from docforge.usecases.chunking.semantic_chunker import chunk_semantic
from docforge.usecases.chunking.serializer import chunks_to_dicts, chunks_to_jsonl


def _block(
    text: str,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
    y0: float = 0.0,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(0, y0, 100, y0 + 12),
        font=FontInfo(name="A", size=10, is_bold=False),
        block_type=block_type,
        heading_level=heading_level,
    )


def _build_result(pages: list[list[TextBlock]]) -> ParseResult:
    page_contents = []
    for i, blocks in enumerate(pages, start=1):
        ranked = assign_hierarchy(blocks, page_num=i)
        page_contents.append(
            PageContent(
                page_num=i,
                page_type=PageType.DIGITAL,
                blocks=tuple(ranked),
                tables=(),
                raw_text="\n".join(b.text for b in ranked),
            )
        )
    metadata = Metadata(
        source="sample.pdf",
        source_type="pdf",
        pages=len(pages),
        parsed_at="2026-04-30T00:00:00Z",
        parser_version="test",
        ocr_used=False,
        tables_extracted=0,
        tables_need_review=0,
        noise_removed=NoiseStats(),
    )
    profile = DocumentProfile(
        total_pages=len(pages),
        text_pages=len(pages),
        image_only_pages=0,
        total_chars=0,
        has_tables=False,
        avg_chars_per_page=0.0,
        image_area_ratio=0.0,
        complexity=DocumentComplexity.TEXT_ONLY,
        recommended_parser="docforge",
    )
    return ParseResult(
        pages=tuple(page_contents),
        markdown="",
        metadata=metadata,
        stats=ParseStats(),
        profile=profile,
    )


# ----- by_page ------------------------------------------------------------


def test_by_page_one_chunk_per_nonempty_page():
    r = _build_result(
        [
            [_block("페이지 1 본문")],
            [_block("페이지 2 본문")],
        ]
    )
    chunks = chunk_by_page(r)
    assert len(chunks) == 2
    assert chunks[0].page_numbers == (1,)
    assert chunks[1].page_numbers == (2,)
    assert all(c.chunk_type == "by_page" for c in chunks)


def test_by_page_skips_empty_pages():
    r = _build_result([[_block("내용있음")], [_block("   ")]])
    chunks = chunk_by_page(r)
    assert len(chunks) == 1


def test_by_page_includes_block_ids():
    r = _build_result([[_block("a", y0=10), _block("b", y0=30)]])
    chunks = chunk_by_page(r)
    assert len(chunks[0].block_ids) == 2


def test_by_page_chunk_id_includes_source():
    r = _build_result([[_block("x")]])
    chunks = chunk_by_page(r)
    assert chunks[0].chunk_id.startswith("sample.pdf-")


def test_by_page_token_count_positive():
    r = _build_result([[_block("긴 본문 텍스트입니다 한국어")]])
    chunks = chunk_by_page(r)
    assert chunks[0].token_count >= 1


# ----- by_title -----------------------------------------------------------


def test_by_title_groups_under_heading():
    r = _build_result(
        [
            [
                _block("제1조", BlockType.HEADING, 1, y0=10),
                _block("본문 1", y0=30),
                _block("본문 2", y0=50),
            ]
        ]
    )
    chunks = chunk_by_title(r, max_tokens=500)
    # heading itself is one group; body shares heading_path
    body_chunks = [c for c in chunks if "본문" in c.text]
    assert body_chunks
    assert all("제1조" in c.heading_path[-1] for c in body_chunks)


def test_by_title_heading_path_is_root_to_leaf():
    r = _build_result(
        [
            [
                _block("제1편", BlockType.HEADING, 1, y0=10),
                _block("제1장", BlockType.HEADING, 2, y0=30),
                _block("제15조", BlockType.HEADING, 3, y0=50),
                _block("본문 내용", y0=70),
            ]
        ]
    )
    chunks = chunk_by_title(r, max_tokens=500)
    body = [c for c in chunks if "본문 내용" in c.text]
    assert body
    assert body[0].heading_path == ("제1편", "제1장", "제15조")


def test_by_title_oversized_group_splits_with_repeated_path():
    big_blocks = [_block("제1조", BlockType.HEADING, 1, y0=10)]
    for i in range(5):
        big_blocks.append(_block("가" * 200, y0=30 + i * 20))
    r = _build_result([big_blocks])
    chunks = chunk_by_title(r, max_tokens=50)
    body_chunks = [c for c in chunks if "가" in c.text]
    assert len(body_chunks) > 1
    for c in body_chunks:
        assert c.heading_path == ("제1조",)


def test_by_title_text_includes_heading_prefix():
    r = _build_result(
        [
            [
                _block("제1조", BlockType.HEADING, 1, y0=10),
                _block("본문", y0=30),
            ]
        ]
    )
    chunks = chunk_by_title(r, max_tokens=500)
    body = [c for c in chunks if "본문" in c.text]
    assert body
    assert "제1조" in body[0].text


def test_by_title_empty_document_returns_empty():
    r = _build_result([[]])
    chunks = chunk_by_title(r)
    assert chunks == []


def test_by_title_records_block_ids():
    r = _build_result(
        [
            [
                _block("제1조", BlockType.HEADING, 1, y0=10),
                _block("본문", y0=30),
            ]
        ]
    )
    chunks = chunk_by_title(r, max_tokens=500)
    body = [c for c in chunks if "본문" in c.text]
    assert all(len(c.block_ids) >= 1 for c in body)


# ----- fixed --------------------------------------------------------------


def test_fixed_respects_max_tokens_roughly():
    blocks = [_block("가" * 50, y0=i * 20) for i in range(10)]
    r = _build_result([blocks])
    chunks = chunk_fixed(r, max_tokens=30)
    assert len(chunks) > 1
    assert all(c.chunk_type == "fixed" for c in chunks)


def test_fixed_empty_document():
    r = _build_result([[]])
    assert chunk_fixed(r) == []


def test_fixed_concatenates_text_with_newlines():
    r = _build_result([[_block("a", y0=10), _block("b", y0=30)]])
    chunks = chunk_fixed(r, max_tokens=500)
    assert "a" in chunks[0].text and "b" in chunks[0].text


def test_fixed_records_pages_traversed():
    r = _build_result([[_block("a")], [_block("b")]])
    chunks = chunk_fixed(r, max_tokens=500)
    # All pages collapse into single chunk under high max_tokens
    assert chunks[0].page_numbers == (1, 2)


def test_fixed_chunk_id_sequential():
    blocks = [_block("가" * 50, y0=i * 20) for i in range(6)]
    r = _build_result([blocks])
    chunks = chunk_fixed(r, max_tokens=30)
    ids = [c.chunk_id for c in chunks]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


# ----- semantic -----------------------------------------------------------


class _StubAnalyzer:
    """Hand-rolled analyzer that emits SF/EF tokens at every '.'."""

    def __init__(self, available: bool = True) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def tokenize(self, text: str) -> list[MorphemeToken]:
        out: list[MorphemeToken] = []
        for i, ch in enumerate(text):
            if ch == ".":
                out.append(MorphemeToken(form=".", tag="SF", start=i, length=1))
            elif ch == "다" and i + 1 < len(text) and text[i + 1] == ".":
                out.append(MorphemeToken(form="다", tag="EF", start=i, length=1))
        return out


def test_semantic_falls_back_to_fixed_when_no_analyzer():
    r = _build_result([[_block("아무 텍스트")]])
    chunks = chunk_semantic(r, analyzer=None, max_tokens=500)
    assert chunks
    assert all(c.chunk_type == "fixed" for c in chunks)


def test_semantic_falls_back_when_analyzer_unavailable():
    r = _build_result([[_block("아무 텍스트")]])
    chunks = chunk_semantic(r, analyzer=_StubAnalyzer(available=False))
    assert all(c.chunk_type == "fixed" for c in chunks)


def test_semantic_splits_at_sentence_boundaries():
    r = _build_result(
        [[_block("첫 문장이다. 두 번째 문장이다. 세 번째 문장이다.")]]
    )
    chunks = chunk_semantic(r, analyzer=_StubAnalyzer(), max_tokens=10000)
    assert chunks
    assert chunks[0].chunk_type == "semantic"
    # All sentences should fit one chunk under huge max_tokens
    assert "첫 문장" in chunks[0].text
    assert "세 번째" in chunks[0].text


def test_semantic_flushes_before_exceeding_max_tokens():
    long_sent = "가" * 60 + "다."
    text = (long_sent + " ") * 4
    r = _build_result([[_block(text)]])
    chunks = chunk_semantic(r, analyzer=_StubAnalyzer(), max_tokens=40)
    assert len(chunks) > 1


def test_semantic_empty_returns_empty():
    r = _build_result([[]])
    assert chunk_semantic(r, analyzer=_StubAnalyzer()) == []


# ----- entry point + dispatch --------------------------------------------


def test_chunk_document_dispatch_by_title():
    r = _build_result([[_block("h", BlockType.HEADING, 1), _block("body", y0=30)]])
    chunks = chunk_document(r, strategy="by_title")
    assert all(c.chunk_type == "by_title" for c in chunks)


def test_chunk_document_dispatch_by_page():
    r = _build_result([[_block("body")]])
    chunks = chunk_document(r, strategy="by_page")
    assert chunks and chunks[0].chunk_type == "by_page"


def test_chunk_document_unknown_strategy_raises():
    r = _build_result([[_block("body")]])
    with pytest.raises(ValueError):
        chunk_document(r, strategy="nonexistent")


# ----- serializer ---------------------------------------------------------


def test_serializer_jsonl_one_chunk_per_line():
    r = _build_result([[_block("a")], [_block("b")]])
    chunks = chunk_document(r, strategy="by_page")
    out = chunks_to_jsonl(chunks)
    assert out.endswith("\n")
    lines = out.rstrip("\n").split("\n")
    assert len(lines) == len(chunks)
    for line in lines:
        obj = json.loads(line)
        assert "chunk_id" in obj
        assert "text" in obj
        assert "block_ids" in obj


def test_serializer_dicts_round_trip_keys():
    r = _build_result([[_block("a")]])
    chunks = chunk_document(r, strategy="by_page")
    dicts = chunks_to_dicts(chunks)
    assert dicts[0].keys() >= {
        "chunk_id", "text", "block_ids", "page_numbers",
        "heading_path", "chunk_type", "token_count", "metadata",
    }


def test_chunk_is_frozen():
    c = Chunk(
        chunk_id="x",
        text="t",
        block_ids=(),
        page_numbers=(),
        heading_path=(),
        chunk_type="by_page",
        token_count=1,
    )
    with pytest.raises(Exception):
        c.text = "y"  # type: ignore[misc]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
