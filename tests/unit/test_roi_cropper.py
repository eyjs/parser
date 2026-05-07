"""Tests for ``roi_cropper`` -- bbox region crop for VLM input."""

from __future__ import annotations

import io

from PIL import Image

from docforge.domain.models import NormalizedBlock
from docforge.domain.enums import BlockType
from docforge.domain.value_objects import BBox
from docforge.processing.roi_cropper import (
    crop_blocks_for_vlm,
    crop_region_from_image,
    encode_crop_for_vlm,
)


def _make_page_image(width: int = 200, height: int = 300) -> bytes:
    """Create a simple PNG image of given size."""
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _nb(
    block_id: str,
    x0: float, y0: float, x1: float, y1: float,
    block_type: BlockType = BlockType.TEXT,
) -> NormalizedBlock:
    return NormalizedBlock(
        block_id=block_id,
        bbox=BBox(x0, y0, x1, y1),
        block_type=block_type,
        confidence=0.9,
        source="test",
        page_num=1,
    )


class TestCropRegionFromImage:
    def test_basic_crop_produces_png(self) -> None:
        page = _make_page_image(200, 300)
        bbox = BBox(10, 10, 100, 100)
        result = crop_region_from_image(page, 200, 300, bbox)
        assert len(result) > 0
        # Verify it is valid PNG
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_crop_respects_bbox_proportions(self) -> None:
        """Crop output should be roughly proportional to bbox size."""
        page = _make_page_image(400, 600)
        bbox = BBox(0, 0, 200, 300)
        result = crop_region_from_image(page, 400, 600, bbox, padding=0)
        img = Image.open(io.BytesIO(result))
        # With 1:1 scale (image == page), crop should be ~200x300
        assert abs(img.width - 200) <= 2
        assert abs(img.height - 300) <= 2

    def test_bbox_clamps_to_image_bounds(self) -> None:
        """BBox extending beyond the image should be clamped."""
        page = _make_page_image(100, 100)
        bbox = BBox(-50, -50, 200, 200)  # extends beyond all edges
        result = crop_region_from_image(page, 100, 100, bbox, padding=0)
        assert len(result) > 0
        img = Image.open(io.BytesIO(result))
        assert img.width <= 100
        assert img.height <= 100

    def test_zero_area_bbox_returns_empty(self) -> None:
        """A bbox with zero area should return empty bytes."""
        page = _make_page_image(100, 100)
        bbox = BBox(50, 50, 50, 50)  # zero width and height
        result = crop_region_from_image(page, 100, 100, bbox, padding=0)
        assert result == b""

    def test_empty_image_returns_empty(self) -> None:
        result = crop_region_from_image(b"", 100, 100, BBox(0, 0, 50, 50))
        assert result == b""

    def test_padding_expands_crop(self) -> None:
        page = _make_page_image(200, 200)
        bbox = BBox(50, 50, 100, 100)
        no_pad = crop_region_from_image(page, 200, 200, bbox, padding=0)
        with_pad = crop_region_from_image(page, 200, 200, bbox, padding=20)
        img_no = Image.open(io.BytesIO(no_pad))
        img_yes = Image.open(io.BytesIO(with_pad))
        # Padded crop should be larger
        assert img_yes.width >= img_no.width
        assert img_yes.height >= img_no.height


class TestEncodeCropForVlm:
    def test_passthrough(self) -> None:
        data = b"some png data"
        assert encode_crop_for_vlm(data) == data

    def test_empty_input(self) -> None:
        assert encode_crop_for_vlm(b"") == b""


class TestCropBlocksForVlm:
    def test_batch_crop_multiple_blocks(self) -> None:
        page = _make_page_image(200, 200)
        blocks = [
            _nb("b1", 0, 0, 50, 50),
            _nb("b2", 100, 100, 150, 150),
        ]
        result = crop_blocks_for_vlm(page, 200, 200, blocks)
        assert "b1" in result
        assert "b2" in result
        assert len(result) == 2

    def test_zero_area_block_omitted(self) -> None:
        page = _make_page_image(100, 100)
        blocks = [
            _nb("good", 0, 0, 50, 50),
            _nb("bad", 80, 80, 80, 80),  # zero area
        ]
        result = crop_blocks_for_vlm(page, 100, 100, blocks, padding=0)
        assert "good" in result
        assert "bad" not in result

    def test_empty_blocks_list(self) -> None:
        page = _make_page_image(100, 100)
        assert crop_blocks_for_vlm(page, 100, 100, []) == {}
