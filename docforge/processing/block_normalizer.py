"""Block Normalization Layer -- unify Vision/Surya/heuristic outputs.

Converts ``LayoutBlock`` (from Surya) and ``TextBlock`` (from OCR /
PyMuPDF) into a common ``NormalizedBlock`` representation so the
routing engine can apply confidence thresholds consistently regardless
of the upstream source.

The merge step matches layout blocks to text blocks via IoU and picks
the higher-confidence classification.
"""

from __future__ import annotations

import hashlib
import logging

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, NormalizedBlock, TextBlock
from docforge.domain.value_objects import BBox

logger = logging.getLogger(__name__)


# ---- Surya label -> BlockType mapping ----

SURYA_LABEL_MAP: dict[str, BlockType] = {
    "Table": BlockType.TABLE,
    "Figure": BlockType.FIGURE,
    "Picture": BlockType.FIGURE,
    "Title": BlockType.HEADING,
    "Section-header": BlockType.HEADING,
    "Caption": BlockType.CAPTION,
    "Text": BlockType.TEXT,
    "Formula": BlockType.TEXT,
    "Equation": BlockType.TEXT,
}


def _make_block_id(bbox: BBox, page_num: int) -> str:
    """Deterministic 12-char block id from bbox + page number."""
    key = f"{page_num}|{bbox.x0:.1f}|{bbox.y0:.1f}|{bbox.x1:.1f}|{bbox.y1:.1f}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def normalize_layout_block(lb: LayoutBlock) -> NormalizedBlock:
    """Convert a single ``LayoutBlock`` into ``NormalizedBlock``."""
    block_type = SURYA_LABEL_MAP.get(lb.label, BlockType.UNKNOWN)
    return NormalizedBlock(
        block_id=_make_block_id(lb.bbox, lb.page_num),
        bbox=lb.bbox,
        block_type=block_type,
        confidence=lb.confidence,
        source="surya",
        original_label=lb.label,
        page_num=lb.page_num,
    )


def normalize_text_block(tb: TextBlock, page_num: int = 0) -> NormalizedBlock:
    """Convert a single ``TextBlock`` into ``NormalizedBlock``."""
    return NormalizedBlock(
        block_id=tb.block_id or _make_block_id(tb.bbox, page_num),
        bbox=tb.bbox,
        block_type=tb.block_type,
        confidence=tb.confidence,
        text=tb.text,
        source="vision",
        page_num=page_num,
    )


def merge_normalized(
    text_blocks: list[NormalizedBlock],
    layout_blocks: list[NormalizedBlock],
    iou_threshold: float = 0.3,
) -> list[NormalizedBlock]:
    """Merge text and layout normalized blocks via IoU matching.

    For each text block, find the best-matching layout block (by IoU).
    If a match is found (IoU >= ``iou_threshold``):
    - Adopt the **higher-confidence** block's ``block_type``
    - Keep the text from the text block
    - Set confidence to ``max(text.confidence, layout.confidence)``

    Unmatched layout blocks are included as-is (they may represent
    regions without OCR text, e.g. figures or charts).

    Returns a new list -- inputs are never mutated.
    """
    if not text_blocks and not layout_blocks:
        return []
    if not layout_blocks:
        return list(text_blocks)
    if not text_blocks:
        return list(layout_blocks)

    matched_layout_ids: set[str] = set()
    result: list[NormalizedBlock] = []

    for tb in text_blocks:
        best_lb: NormalizedBlock | None = None
        best_iou = 0.0
        for lb in layout_blocks:
            iou = tb.bbox.iou(lb.bbox)
            if iou > best_iou and iou >= iou_threshold:
                best_iou = iou
                best_lb = lb

        if best_lb is not None:
            matched_layout_ids.add(best_lb.block_id)
            # Pick higher-confidence block_type
            if best_lb.confidence > tb.confidence:
                merged_type = best_lb.block_type
                merged_label = best_lb.original_label
            else:
                merged_type = tb.block_type
                merged_label = best_lb.original_label

            merged = NormalizedBlock(
                block_id=tb.block_id,
                bbox=tb.bbox,
                block_type=merged_type,
                confidence=max(tb.confidence, best_lb.confidence),
                text=tb.text,
                source=f"{tb.source}+{best_lb.source}",
                original_label=merged_label,
                page_num=tb.page_num,
            )
            result.append(merged)
        else:
            result.append(tb)

    # Include unmatched layout blocks (figures, charts without OCR text)
    for lb in layout_blocks:
        if lb.block_id not in matched_layout_ids:
            result.append(lb)

    return result


__all__ = [
    "SURYA_LABEL_MAP",
    "normalize_layout_block",
    "normalize_text_block",
    "merge_normalized",
]
