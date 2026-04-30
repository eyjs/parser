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
        # Phase B-2 hardened: deterministic md5-truncated id (12 chars).
        assert len(img.block_id) == 12

    def test_block_id_is_deterministic(self) -> None:
        """Same xref+page+bbox => same block_id across runs."""
        page = _FakePage(
            images=[(101,)], bbox_map={101: _Rect(0, 0, 10, 10)},
        )
        doc1 = _FakeDoc(page, {101: {"image": b"\x89PNG", "ext": "png"}})
        doc2 = _FakeDoc(page, {101: {"image": b"\x89PNG", "ext": "png"}})
        out1 = extract_images(reader=None, doc=doc1, page_idx=0)
        out2 = extract_images(reader=None, doc=doc2, page_idx=0)
        assert out1[0].block_id == out2[0].block_id

    def test_placeholder_only_mode(self) -> None:
        """include_bytes=False keeps location + id but omits data."""
        page = _FakePage(images=[(1,)], bbox_map={1: _Rect(5, 5, 50, 50)})
        doc = _FakeDoc(page, {1: {"image": b"\x89PNG", "ext": "png"}})
        out = extract_images(reader=None, doc=doc, page_idx=0, include_bytes=False)
        assert len(out) == 1
        assert out[0].data == b""
        assert out[0].bbox.x0 == 5
        assert len(out[0].block_id) == 12

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

    def test_keeps_placeholder_when_bytes_missing(self) -> None:
        """Phase B-2: empty bytes are kept as placeholder (was: skipped).

        Future VLM injection needs the slot — losing the location entry
        means the slot can never be filled in later.
        """
        page = _FakePage(
            images=[(9,)],
            bbox_map={9: _Rect(0, 0, 10, 10)},
        )
        doc = _FakeDoc(page, {9: {"image": b"", "ext": "png"}})
        out = extract_images(reader=None, doc=doc, page_idx=0)
        assert len(out) == 1
        assert out[0].data == b""
        assert out[0].bbox.x0 == 0
