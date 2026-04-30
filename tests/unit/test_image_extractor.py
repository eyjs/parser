"""Tests for ``image_extractor`` using mock PyMuPDF objects."""

from __future__ import annotations

from dataclasses import dataclass

from docforge.processing.image_extractor import extract_images


@dataclass
class _Rect:
    x0: float
    y0: float
    x1: float
    y1: float


class _FakePage:
    def __init__(self, images: list[tuple[int, ...]], bbox_map: dict[int, _Rect]) -> None:
        self._images = images
        self._bbox_map = bbox_map

    def get_images(self, full: bool = False) -> list[tuple[int, ...]]:
        return self._images

    def get_image_bbox(self, xref: int) -> _Rect:
        return self._bbox_map[xref]


class _FakeDoc:
    def __init__(self, page: _FakePage, image_data: dict[int, dict[str, object]]) -> None:
        self._page = page
        self._image_data = image_data

    def __getitem__(self, idx: int) -> _FakePage:
        return self._page

    def extract_image(self, xref: int) -> dict[str, object]:
        return self._image_data[xref]


class TestExtractImages:
    def test_returns_empty_when_no_images(self) -> None:
        page = _FakePage(images=[], bbox_map={})
        doc = _FakeDoc(page, {})
        assert extract_images(reader=None, doc=doc, page_idx=0) == []

    def test_extracts_single_image_with_bbox_and_data(self) -> None:
        page = _FakePage(
            images=[(42,)],
            bbox_map={42: _Rect(10, 20, 110, 220)},
        )
        doc = _FakeDoc(
            page,
            {42: {"image": b"\x89PNG\r\n\x1a\n", "ext": "png"}},
        )
        out = extract_images(reader=None, doc=doc, page_idx=0)
        assert len(out) == 1
        img = out[0]
        assert img.format == "png"
        assert img.data.startswith(b"\x89PNG")
        assert img.bbox.x0 == 10 and img.bbox.y1 == 220
        assert img.page_num == 1
        assert img.caption is None
        assert len(img.block_id) == 8

    def test_jpeg_extension_normalized(self) -> None:
        page = _FakePage(
            images=[(7,)],
            bbox_map={7: _Rect(0, 0, 50, 50)},
        )
        doc = _FakeDoc(
            page,
            {7: {"image": b"\xff\xd8\xff", "ext": "jpg"}},
        )
        out = extract_images(reader=None, doc=doc, page_idx=0)
        assert out[0].format == "jpeg"

    def test_skips_image_when_bytes_missing(self) -> None:
        page = _FakePage(
            images=[(9,)],
            bbox_map={9: _Rect(0, 0, 10, 10)},
        )
        doc = _FakeDoc(page, {9: {"image": b"", "ext": "png"}})
        assert extract_images(reader=None, doc=doc, page_idx=0) == []
