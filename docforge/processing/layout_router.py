"""Merge layout-detector regions with PyMuPDF text blocks (Phase B-1).

Given the text blocks PyMuPDF extracted (bbox + font + heuristic block
type) and the layout regions a detector (Surya) produced for the same
page, this module decides — per text block — whether the layout label
should override the heuristic ``BlockType``.

Design constraints:
  * ``TextBlock`` stays ``frozen`` with the same fields as Phase 3 — we
    rebuild a new instance instead of mutating, and we never add new
    fields. Layout label propagation outside ``BlockType`` is exposed via
    the helper :func:`build_layout_label_map` for callers that want a
    side-channel ``block_id -> layout_label`` mapping.
  * Graceful degradation: empty layout list => return inputs unchanged.

No imports from ``surya`` / ``surya_ocr`` here (Protocol injection only).
"""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox

DEFAULT_IOU_THRESHOLD = 0.3


def merge_layout_with_text(
    text_blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> list[TextBlock]:
    """Boost ``text_blocks`` using ``layout_blocks``.

    For each text block, find the best-overlapping layout region (IoU >=
    ``iou_threshold``) and apply label-specific overrides:

    * ``Title``  -> ``BlockType.HEADING`` (preserves existing
      ``heading_level`` if already set; defaults to 2 otherwise).
    * ``Caption`` -> ``BlockType.ITEM`` (caption rendering is handled
      downstream by the caption matcher).
    * Anything else -> leave the block unchanged.

    When ``layout_blocks`` is empty the input list is returned as-is,
    preserving full backward compatibility for the layout-disabled path.
    """
    if not layout_blocks:
        return text_blocks

    result: list[TextBlock] = []
    for tb in text_blocks:
        best_label = _best_label(tb.bbox, layout_blocks, iou_threshold)
        if best_label == "Title":
            result.append(
                _rebuild(
                    tb,
                    block_type=BlockType.HEADING,
                    heading_level=tb.heading_level if tb.heading_level > 0 else 2,
                )
            )
        elif best_label == "Caption":
            result.append(
                _rebuild(
                    tb,
                    block_type=BlockType.ITEM,
                    heading_level=0,
                )
            )
        else:
            result.append(tb)
    return result


def build_layout_label_map(
    text_blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> dict[str, str]:
    """Return ``{block_id: layout_label}`` for blocks that have a match.

    Blocks without a ``block_id`` or without a qualifying overlap are
    omitted. Used by callers (e.g. caption_matcher) that need access to
    the layout label without mutating ``TextBlock``.
    """
    if not layout_blocks:
        return {}
    out: dict[str, str] = {}
    for tb in text_blocks:
        if not tb.block_id:
            continue
        label = _best_label(tb.bbox, layout_blocks, iou_threshold)
        if label is not None:
            out[tb.block_id] = label
    return out


# -- internals ------------------------------------------------------------


def _best_label(
    bbox: BBox,
    layout_blocks: list[LayoutBlock],
    iou_threshold: float,
) -> str | None:
    best_label: str | None = None
    best_iou: float = 0.0
    for lb in layout_blocks:
        iou = bbox_iou(bbox, lb.bbox)
        if iou > best_iou and iou >= iou_threshold:
            best_iou = iou
            best_label = lb.label
    return best_label


def _rebuild(
    tb: TextBlock,
    *,
    block_type: BlockType,
    heading_level: int,
) -> TextBlock:
    """Return a new ``TextBlock`` with overridden type/level (frozen safe)."""
    return TextBlock(
        text=tb.text,
        bbox=tb.bbox,
        font=tb.font,
        block_type=block_type,
        heading_level=heading_level,
        confidence=tb.confidence,
        block_id=tb.block_id,
        parent_id=tb.parent_id,
    )


def bbox_iou(a: BBox, b: BBox) -> float:
    """Intersection-over-union for two bounding boxes."""
    inter_x0 = max(a.x0, b.x0)
    inter_y0 = max(a.y0, b.y0)
    inter_x1 = min(a.x1, b.x1)
    inter_y1 = min(a.y1, b.y1)

    inter_area = max(0.0, inter_x1 - inter_x0) * max(0.0, inter_y1 - inter_y0)
    if inter_area <= 0:
        return 0.0
    union = a.area + b.area - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


__all__ = [
    "DEFAULT_IOU_THRESHOLD",
    "bbox_iou",
    "build_layout_label_map",
    "merge_layout_with_text",
]
