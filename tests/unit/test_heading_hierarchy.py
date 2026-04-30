"""Tests for processing.heading_hierarchy.assign_hierarchy."""

from __future__ import annotations

import pytest

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.heading_hierarchy import _make_block_id, assign_hierarchy


def _block(
    text: str,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
    x0: float = 0.0,
    y0: float = 0.0,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x0 + 100, y1=y0 + 12),
        font=FontInfo(name="Arial", size=10, is_bold=False),
        block_type=block_type,
        heading_level=heading_level,
    )


def test_empty_input_returns_empty_list():
    assert assign_hierarchy([]) == []


def test_single_text_block_has_no_parent():
    out = assign_hierarchy([_block("hello")])
    assert len(out) == 1
    assert out[0].block_id is not None
    assert len(out[0].block_id) == 12
    assert out[0].parent_id is None


def test_text_under_single_heading_attaches_to_it():
    h = _block("제1조", BlockType.HEADING, 1, y0=10)
    body = _block("본문 내용", y0=30)
    out = assign_hierarchy([h, body])
    assert out[0].parent_id is None
    assert out[1].parent_id == out[0].block_id


def test_three_level_nesting_pyeon_jang_jo():
    h1 = _block("제1편 보험계약", BlockType.HEADING, 1, y0=10)
    h2 = _block("제1장 통칙", BlockType.HEADING, 2, y0=30)
    h3 = _block("제15조 지급사유", BlockType.HEADING, 3, y0=50)
    body = _block("보험금을 지급한다.", y0=70)
    out = assign_hierarchy([h1, h2, h3, body])

    assert out[0].parent_id is None
    assert out[1].parent_id == out[0].block_id
    assert out[2].parent_id == out[1].block_id
    assert out[3].parent_id == out[2].block_id


def test_sibling_headings_share_parent():
    h1 = _block("제1편", BlockType.HEADING, 1, y0=10)
    h2a = _block("제1장", BlockType.HEADING, 2, y0=30)
    h2b = _block("제2장", BlockType.HEADING, 2, y0=50)
    out = assign_hierarchy([h1, h2a, h2b])
    assert out[1].parent_id == out[0].block_id
    assert out[2].parent_id == out[0].block_id


def test_heading_pop_to_uncle_level():
    h1 = _block("제1편", BlockType.HEADING, 1, y0=10)
    h2 = _block("제1장", BlockType.HEADING, 2, y0=30)
    h3 = _block("제15조", BlockType.HEADING, 3, y0=50)
    h2_next = _block("제2장", BlockType.HEADING, 2, y0=70)
    out = assign_hierarchy([h1, h2, h3, h2_next])
    # 제2장 must pop 제15조 and 제1장, attach to 제1편
    assert out[3].parent_id == out[0].block_id


def test_text_attaches_to_deepest_heading():
    h1 = _block("제1편", BlockType.HEADING, 1, y0=10)
    h2 = _block("제1장", BlockType.HEADING, 2, y0=30)
    body = _block("본문", y0=50)
    out = assign_hierarchy([h1, h2, body])
    assert out[2].parent_id == out[1].block_id


def test_clause_and_item_blocks_treated_as_non_heading():
    h = _block("제15조", BlockType.HEADING, 1, y0=10)
    clause = _block("①항", BlockType.CLAUSE, y0=30)
    item = _block("1.", BlockType.ITEM, y0=50)
    out = assign_hierarchy([h, clause, item])
    assert out[1].parent_id == out[0].block_id
    assert out[2].parent_id == out[0].block_id


def test_block_id_is_deterministic():
    blocks = [_block("제1조", BlockType.HEADING, 1, y0=10), _block("본문", y0=30)]
    out_a = assign_hierarchy(blocks, page_num=3)
    out_b = assign_hierarchy(blocks, page_num=3)
    assert [b.block_id for b in out_a] == [b.block_id for b in out_b]


def test_block_id_differs_with_page_num():
    blocks = [_block("동일 텍스트", y0=10)]
    a = assign_hierarchy(blocks, page_num=1)
    b = assign_hierarchy(blocks, page_num=2)
    assert a[0].block_id != b[0].block_id


def test_input_blocks_not_mutated():
    h = _block("제1조", BlockType.HEADING, 1, y0=10)
    assign_hierarchy([h])
    assert h.block_id is None
    assert h.parent_id is None


def test_make_block_id_format():
    bid = _make_block_id(_block("x"), page_num=0)
    assert len(bid) == 12
    int(bid, 16)  # must parse as hex


def test_text_before_any_heading_has_no_parent():
    body = _block("머리말", y0=10)
    h = _block("제1조", BlockType.HEADING, 1, y0=30)
    out = assign_hierarchy([body, h])
    assert out[0].parent_id is None
    assert out[1].parent_id is None


def test_heading_with_zero_level_treated_as_level_one():
    h0 = _block("제목", BlockType.HEADING, 0, y0=10)
    body = _block("본문", y0=30)
    h_next = _block("다음", BlockType.HEADING, 1, y0=50)
    out = assign_hierarchy([h0, body, h_next])
    # body attaches to h0; h_next pops h0
    assert out[1].parent_id == out[0].block_id
    assert out[2].parent_id is None


def test_all_returned_blocks_have_block_id():
    blocks = [
        _block("h1", BlockType.HEADING, 1, y0=10),
        _block("t1", y0=30),
        _block("h2", BlockType.HEADING, 2, y0=50),
        _block("t2", y0=70),
    ]
    out = assign_hierarchy(blocks)
    assert all(b.block_id is not None for b in out)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
