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


def merge_and_label(
    text_blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> tuple[list[TextBlock], dict[str, str]]:
    """One-pass: rebuild text blocks AND emit ``block_id -> label`` map.

    Replaces the prior pattern of calling ``merge_layout_with_text`` and
    ``build_layout_label_map`` separately, which scanned the same N×M
    IoU grid twice. Returns the rebuilt list plus the label map for any
    block whose layout match qualifies.
    """
    if not layout_blocks:
        return text_blocks, {}

    rebuilt: list[TextBlock] = []
    label_map: dict[str, str] = {}
    for tb in text_blocks:
        label = _best_label(tb.bbox, layout_blocks, iou_threshold)
        if label is None:
            rebuilt.append(tb)
            continue
        if tb.block_id:
            label_map[tb.block_id] = label
        if label == "Title":
            rebuilt.append(_rebuild(
                tb,
                block_type=BlockType.HEADING,
                heading_level=tb.heading_level if tb.heading_level > 0 else 2,
            ))
        elif label == "Caption":
            rebuilt.append(_rebuild(tb, block_type=BlockType.ITEM, heading_level=0))
        else:
            rebuilt.append(tb)
    return rebuilt, label_map


def merge_layout_with_text(
    text_blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> list[TextBlock]:
    """Backward-compatible wrapper. Prefer :func:`merge_and_label`."""
    rebuilt, _ = merge_and_label(text_blocks, layout_blocks, iou_threshold)
    return rebuilt


def build_layout_label_map(
    text_blocks: list[TextBlock],
    layout_blocks: list[LayoutBlock],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> dict[str, str]:
    """Backward-compatible wrapper. Prefer :func:`merge_and_label`."""
    _, label_map = merge_and_label(text_blocks, layout_blocks, iou_threshold)
    return label_map


# -- internals ------------------------------------------------------------


def _best_label(
    bbox: BBox,
    layout_blocks: list[LayoutBlock],
    iou_threshold: float,
) -> str | None:
    best_label: str | None = None
    best_iou: float = 0.0
    for lb in layout_blocks:
        iou = bbox.iou(lb.bbox)
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
    """Backward-compatible alias for ``BBox.iou`` — prefer the method."""
    return a.iou(b)


__all__ = [
    "DEFAULT_IOU_THRESHOLD",
    "bbox_iou",
    "build_layout_label_map",
    "merge_and_label",
    "merge_layout_with_text",
]
