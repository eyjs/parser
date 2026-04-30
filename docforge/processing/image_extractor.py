"""PDF image extraction (Phase B-2).

Walks ``page.get_images(full=True)`` to enumerate embedded images, looks
each one up via ``page.get_image_bbox`` for spatial context, and decodes
the raw image bytes via ``doc.extract_image(xref)``. The result is a
list of :class:`ParsedImage` instances **without captions** — the caller
should hand them to :func:`docforge.processing.caption_matcher.match_captions`.

We intentionally accept the ``PyMuPDFReader``+``doc`` pair instead of
hard-importing ``fitz`` so this module stays usable from environments
that mock the reader. ``fitz`` is imported only inside the function on
the happy path, gated on ``hasattr`` checks.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from docforge.domain.models import ParsedImage
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)


def extract_images(
    reader: Any,
    doc: Any,
    page_idx: int,
) -> list[ParsedImage]:
    """Extract all embedded images on ``page_idx``.

    Returns:
        List of :class:`ParsedImage` (with ``caption=None``). Empty when
        the page has no images or extraction fails.
    """
    try:
        page = doc[page_idx]
    except Exception:  # pragma: no cover - defensive
        logger.warning("Cannot index page %d", page_idx)
        return []

    raw_images = _get_image_xrefs(page)
    if not raw_images:
        return []

    out: list[ParsedImage] = []
    for img_info in raw_images:
        xref = img_info[0] if isinstance(img_info, (list, tuple)) else img_info.get("xref")
        if xref is None:
            continue
        bbox = _resolve_bbox(page, xref, img_info)
        if bbox is None:
            continue
        data, fmt = _extract_bytes(doc, xref)
        if not data:
            continue
        out.append(
            ParsedImage(
                bbox=bbox,
                data=data,
                format=fmt,
                caption=None,
                page_num=page_idx + 1,
                block_id=uuid.uuid4().hex[:8],
            )
        )
    return out


# -- internals ------------------------------------------------------------


def _get_image_xrefs(page: Any) -> list[Any]:
    """Return raw image entries from PyMuPDF; tolerant of mock objects."""
    getter = getattr(page, "get_images", None)
    if getter is None:
        return []
    try:
        return list(getter(full=True))
    except TypeError:
        try:
            return list(getter())
        except Exception:
            return []
    except Exception:
        return []


def _resolve_bbox(page: Any, xref: int, img_info: Any) -> BBox | None:
    """Look up image bbox via ``page.get_image_bbox`` or fall back to dict."""
    if isinstance(img_info, dict) and "bbox" in img_info:
        bb = img_info["bbox"]
        if len(bb) == 4:
            return BBox(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))

    getter = getattr(page, "get_image_bbox", None)
    if getter is None:
        return None
    try:
        rect = getter(xref)
    except Exception:
        return None
    if rect is None:
        return None
    try:
        return BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
    except Exception:
        try:
            return BBox(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
        except Exception:
            return None


def _extract_bytes(doc: Any, xref: int) -> tuple[bytes, str]:
    """Return ``(image_bytes, format)`` for an xref. ``("", "png")`` on failure."""
    extractor = getattr(doc, "extract_image", None)
    if extractor is None:
        return b"", "png"
    try:
        info = extractor(xref)
    except Exception:
        return b"", "png"
    data = info.get("image", b"") if isinstance(info, dict) else b""
    ext = info.get("ext", "png") if isinstance(info, dict) else "png"
    fmt = "jpeg" if ext.lower() in ("jpg", "jpeg") else "png"
    return data, fmt


__all__ = ["extract_images"]
