"""ROI Crop Pipeline -- NormalizedBlock bbox region crop for VLM input.

Generalises the table-only crop in ``docforge/adapters/region_crop.py``
to support arbitrary bbox regions (tables, charts, figures).  Works on
already-rendered page images (PNG bytes) rather than raw PyMuPDF page
objects so it stays adapter-agnostic.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from docforge.domain.value_objects import BBox

if TYPE_CHECKING:
    from docforge.domain.models import NormalizedBlock

logger = logging.getLogger(__name__)


def crop_region_from_image(
    page_image_bytes: bytes,
    page_width: int,
    page_height: int,
    bbox: BBox,
    padding: int = 5,
) -> bytes:
    """Crop *bbox* from a full-page PNG image and return PNG bytes.

    Parameters
    ----------
    page_image_bytes:
        Full-page image encoded as PNG.
    page_width, page_height:
        Logical page dimensions (PDF points).  Used to map ``bbox``
        (PDF coordinate space) to pixel coordinates in the image.
    bbox:
        Target region in PDF coordinate space.
    padding:
        Extra pixel margin around the crop.

    Returns
    -------
    bytes
        Cropped region as PNG bytes.  Empty ``bytes()`` when the
        computed region has zero area.
    """
    from PIL import Image

    if not page_image_bytes:
        return b""

    img = Image.open(io.BytesIO(page_image_bytes))
    img_w, img_h = img.size

    # Scale factors: PDF points -> pixels
    sx = img_w / page_width if page_width > 0 else 1.0
    sy = img_h / page_height if page_height > 0 else 1.0

    # Convert bbox (PDF coords) to pixel coords and clamp
    px0 = max(0, int(bbox.x0 * sx) - padding)
    py0 = max(0, int(bbox.y0 * sy) - padding)
    px1 = min(img_w, int(bbox.x1 * sx) + padding)
    py1 = min(img_h, int(bbox.y1 * sy) + padding)

    if px1 <= px0 or py1 <= py0:
        logger.warning("ROI crop produced zero-area region: bbox=%s", bbox)
        return b""

    cropped = img.crop((px0, py0, px1, py1))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def encode_crop_for_vlm(
    cropped_bytes: bytes,
    format: str = "png",  # noqa: A002  -- shadows built-in intentionally
) -> bytes:
    """Encode a cropped image for VLM consumption.

    Currently a pass-through since ``crop_region_from_image`` already
    emits PNG.  Kept as a seam for future re-encoding (e.g. JPEG for
    bandwidth savings).
    """
    if not cropped_bytes:
        return b""
    return cropped_bytes


def crop_blocks_for_vlm(
    page_image_bytes: bytes,
    page_width: int,
    page_height: int,
    blocks: list[NormalizedBlock],
    padding: int = 5,
) -> dict[str, bytes]:
    """Batch-crop multiple blocks and return ``{block_id: png_bytes}``.

    Blocks whose crop produces zero-area regions are silently omitted
    from the result dict.
    """
    result: dict[str, bytes] = {}
    for block in blocks:
        cropped = crop_region_from_image(
            page_image_bytes,
            page_width,
            page_height,
            block.bbox,
            padding=padding,
        )
        if cropped:
            result[block.block_id] = cropped
    return result


__all__ = [
    "crop_region_from_image",
    "encode_crop_for_vlm",
    "crop_blocks_for_vlm",
]
