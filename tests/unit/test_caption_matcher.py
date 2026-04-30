"""Tests for caption matching scoring."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import ParsedImage, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.caption_matcher import CAPTION_PATTERN, match_captions


def _img(y0: float, y1: float, block_id: str = "img1") -> ParsedImage:
    return ParsedImage(
        bbox=BBox(50, y0, 350, y1),
        data=b"\x89PNG",
        format="png",
        caption=None,
        page_num=1,
        block_id=block_id,
    )


def _tb(text: str, y0: float, y1: float, block_id: str | None = None) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(50, y0, 350, y1),
        font=FontInfo(name="N", size=10, is_bold=False),
        block_type=BlockType.TEXT,
        block_id=block_id,
    )


class TestCaptionPattern:
    def test_korean_figure(self) -> None:
        assert CAPTION_PATTERN.search("그림 1: 흐름도")

    def test_korean_table(self) -> None:
        assert CAPTION_PATTERN.search("표 3 — 가입자 분포")

    def test_english_figure(self) -> None:
        assert CAPTION_PATTERN.search("Figure 12 shows ...")

    def test_no_match(self) -> None:
        assert CAPTION_PATTERN.search("일반 문장입니다") is None


class TestMatchCaptions:
    def test_no_text_blocks_returns_image_unchanged(self) -> None:
        img = _img(100, 200)
        out = match_captions([img], [])
        assert out[0].caption is None

    def test_pattern_block_below_image_wins(self) -> None:
        img = _img(100, 200)
        below = _tb("그림 1: 흐름도", 210, 225, block_id="cap1")
        far = _tb("일반 본문 텍스트", 600, 615, block_id="x")
        out = match_captions([img], [below, far])
        assert out[0].caption == "그림 1: 흐름도"

    def test_layout_label_caption_boost(self) -> None:
        img = _img(100, 200)
        # Block without pattern but with Caption layout label should still win
        layout_block = _tb("도표 설명", 210, 225, block_id="cap1")
        plain = _tb("그림 1", 800, 815, block_id="far")  # too far away (>100pt)
        out = match_captions(
            [img], [layout_block, plain], layout_label_map={"cap1": "Caption"},
        )
        assert out[0].caption == "도표 설명"

    def test_far_block_ignored(self) -> None:
        img = _img(100, 200)
        far = _tb("그림 1: 멀리 있음", 500, 515)
        out = match_captions([img], [far])
        assert out[0].caption is None

    def test_block_above_image_also_considered(self) -> None:
        img = _img(300, 400)
        above = _tb("그림 1: 위에 있음", 270, 290)
        out = match_captions([img], [above])
        assert out[0].caption == "그림 1: 위에 있음"

    def test_multiple_images_independent(self) -> None:
        img1 = _img(100, 200, block_id="a")
        img2 = _img(500, 600, block_id="b")
        cap1 = _tb("그림 1", 210, 225)
        cap2 = _tb("그림 2", 610, 625)
        out = match_captions([img1, img2], [cap1, cap2])
        assert out[0].caption == "그림 1"
        assert out[1].caption == "그림 2"
