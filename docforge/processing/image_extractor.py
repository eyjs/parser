"""PDF image extraction (Phase B-2).

Walks ``page.get_images(full=True)`` to enumerate embedded images, looks
each one up via ``page.get_image_bbox`` for spatial context, and (when
``include_bytes=True``) decodes the raw bytes via ``doc.extract_image``.

The default mode (``include_bytes=False``) records only **location +
deterministic id** — this preserves the spatial information VLM-based
captioning will need later, without paying the bytes-decoding cost or
shipping the image data through the pipeline.

``block_id`` is derived deterministically from ``(xref, page_num,
bbox.x0, bbox.y0)`` so the same PDF re-parsed yields the same id, which
is essential for cross-run correlation (chunk references, VLM caption
joins, etc.).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from docforge.domain.models import ParsedImage
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)


def _make_image_id(xref: int, page_num: int, bbox: BBox) -> str:
    """Deterministic 12-char id keyed on xref + page + bbox top-left."""
    key = f"img|{xref}|{page_num}|{bbox.x0:.1f}|{bbox.y0:.1f}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def extract_images(
    reader: Any,
    doc: Any,
    page_idx: int,
    *,
    include_bytes: bool = True,
) -> list[ParsedImage]:
    """Extract image regions on ``page_idx``.

    Args:
        reader: ``PyMuPDFReader`` (kept for symmetry; unused here so mocks
            without get_images still work).
        doc: PyMuPDF doc handle.
        page_idx: 0-based page index.
        include_bytes: When True, decode raw image bytes. When False
            (placeholder-only mode), bytes are an empty ``b""`` —
            location and id are still populated so VLM captioning can map
            results back to the correct spot later.

    Returns:
        List of :class:`ParsedImage` (with ``caption=None``). Empty when
        the page has no images or bbox lookup fails.
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
    page_num = page_idx + 1
    for img_info in raw_images:
        xref = img_info[0] if isinstance(img_info, (list, tuple)) else img_info.get("xref")
        if xref is None:
            continue
        bbox = _resolve_bbox(page, xref, img_info)
        if bbox is None:
            continue
        block_id = _make_image_id(int(xref), page_num, bbox)
        if include_bytes:
            data, fmt = _extract_bytes(doc, xref)
            if not data:
                # bytes failed but we still want the placeholder
                data, fmt = b"", "png"
        else:
            data, fmt = b"", "png"
        out.append(
            ParsedImage(
                bbox=bbox,
                data=data,
                format=fmt,
                caption=None,
                page_num=page_num,
                block_id=block_id,
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
    """Look up image bbox via ``page.get_image_bbox``; fall back to page rect.

    Scanned PDFs commonly carry a single full-page image that isn't
    registered as a page resource, so ``get_image_bbox`` raises "bad
    image name". For that case we fall back to ``page.rect`` — the
    image effectively IS the whole page, which is the correct bbox.
    """
    if isinstance(img_info, dict) and "bbox" in img_info:
        bb = img_info["bbox"]
        if len(bb) == 4:
            return BBox(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))

    getter = getattr(page, "get_image_bbox", None)
    if getter is not None:
        try:
            rect = getter(xref)
            if rect is not None:
                try:
                    return BBox(
                        float(rect.x0), float(rect.y0),
                        float(rect.x1), float(rect.y1),
                    )
                except Exception:
                    try:
                        return BBox(
                            float(rect[0]), float(rect[1]),
                            float(rect[2]), float(rect[3]),
                        )
                    except Exception:
                        pass
        except Exception:
            pass  # bad image name / unregistered xref — try page.rect fallback

    page_rect = getattr(page, "rect", None)
    if page_rect is None:
        return None
    try:
        return BBox(
            float(page_rect.x0), float(page_rect.y0),
            float(page_rect.x1), float(page_rect.y1),
        )
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
