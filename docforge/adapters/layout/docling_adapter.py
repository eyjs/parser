"""Docling (DocLayNet RT-DETR) layout-detection adapter.

Docling provides 11-class document layout analysis with reading-order
inference and table structure recovery. The adapter follows the same
lazy-load / graceful-fallback pattern as ``surya_adapter.py``.

When ``docling`` is not installed, ``is_available()`` returns False and
``detect()`` returns ``[]``. The import is lazy so importing this module
never triggers a heavy ML import.
"""

from __future__ import annotations

import logging
from typing import Any

from docforge.domain.models import LayoutBlock
from docforge.domain.value_objects import BBox, RawImage

logger = logging.getLogger(__name__)

# DocLayNet 11-class → canonical vocabulary mapping.
# The canonical set is {Text, Title, Table, Figure, Caption, Formula,
# List, Footer, Page-Header, Page-Number, Footnote}.
# Downstream consumers (block_normalizer, layout_router) handle these.
_DOCLING_LABEL_MAP: dict[str, str] = {
    "Text": "Text",
    "Title": "Title",
    "Section-header": "Title",
    "Section-Header": "Title",
    "SectionHeader": "Title",
    "Table": "Table",
    "Figure": "Figure",
    "Picture": "Figure",
    "Caption": "Caption",
    "Formula": "Formula",
    "Equation": "Formula",
    "List": "List",
    "List-item": "List",
    "Footer": "Footer",
    "Page-footer": "Footer",
    "Page-header": "Page-Header",
    "Header": "Page-Header",
    "Page-number": "Page-Number",
    "Footnote": "Footnote",
}


class DoclingLayoutDetector:
    """Layout detector backed by the Docling RT-DETR model.

    The backend is lazy-loaded on first ``detect()`` call. Model weights
    are downloaded by Docling on first use.
    """

    def __init__(self) -> None:
        self._loaded: bool = False
        self._pipeline: Any | None = None
        self._import_error: Exception | None = None

    def is_available(self) -> bool:
        if self._pipeline is not None:
            return True
        if self._import_error is not None:
            return False
        try:
            self._import_docling()
            return True
        except Exception as exc:
            self._import_error = exc
            logger.info("Docling not available: %s", exc)
            return False

    def detect(self, image: RawImage | object, page_num: int) -> list[LayoutBlock]:
        """Run layout detection on a single page image.

        Returns layout blocks in Docling's reading-order sequence.
        Returns an empty list on any failure (graceful degradation).
        """
        if not self._ensure_loaded():
            return []

        pil_image = self._coerce_to_pil(image)
        if pil_image is None:
            return []

        try:
            return self._run_detection(pil_image, page_num)
        except Exception as exc:
            logger.warning("Docling layout detection failed: %s", exc)
            return []

    def _import_docling(self) -> Any:
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
        return DocumentConverter

    def _ensure_loaded(self) -> bool:
        if self._pipeline is not None:
            return True
        if self._import_error is not None:
            return False
        try:
            self._import_docling()
            self._pipeline = self._build_pipeline()
            self._loaded = True
            return True
        except Exception as exc:
            self._import_error = exc
            logger.info("Docling backend unavailable: %s", exc)
            return False

    def _build_pipeline(self) -> Any:
        """Build a Docling layout analysis pipeline for single-image inference."""
        try:
            from docling.datamodel.pipeline_options import (  # type: ignore[import-not-found]
                PipelineOptions,
            )
            from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]

            pipeline_options = PipelineOptions(
                do_ocr=False,
                do_table_structure=True,
            )
            return DocumentConverter(pipeline_options=pipeline_options)
        except Exception:
            from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
            return DocumentConverter()

    def _run_detection(self, pil_image: Any, page_num: int) -> list[LayoutBlock]:
        """Run detection via Docling's layout model on a PIL image."""
        try:
            from docling_core.types.doc import DocItemLabel  # type: ignore[import-not-found]
        except ImportError:
            DocItemLabel = None  # type: ignore[assignment,misc]

        try:
            from docling.datamodel.document import InputDocument  # type: ignore[import-not-found]
        except ImportError:
            pass

        # Docling's primary API expects file paths. For single-image inference,
        # we save to a temp file and convert.
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
            pil_image.save(tmp_path, format="PNG")

        try:
            assert self._pipeline is not None
            result = self._pipeline.convert(tmp_path)
            return self._extract_layout_blocks(result, page_num)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _extract_layout_blocks(self, result: Any, page_num: int) -> list[LayoutBlock]:
        """Extract LayoutBlock list from Docling conversion result.

        Docling returns blocks in reading order, which we preserve.
        """
        blocks: list[LayoutBlock] = []

        try:
            doc = result.document if hasattr(result, "document") else result
        except Exception:
            return blocks

        # Try Docling v2 API: iterate over document items
        items = None
        if hasattr(doc, "body"):
            items = doc.body
        elif hasattr(doc, "main_text"):
            items = doc.main_text

        if items is None:
            return self._extract_from_pages(result, page_num)

        for item in items:
            lb = self._item_to_layout_block(item, page_num)
            if lb is not None:
                blocks.append(lb)

        if not blocks:
            return self._extract_from_pages(result, page_num)

        return blocks

    def _extract_from_pages(self, result: Any, page_num: int) -> list[LayoutBlock]:
        """Fallback: extract layout from page-level predictions."""
        blocks: list[LayoutBlock] = []

        pages = None
        if hasattr(result, "pages"):
            pages = result.pages
        elif hasattr(result, "document") and hasattr(result.document, "pages"):
            pages = result.document.pages

        if not pages:
            return blocks

        for page in pages if isinstance(pages, (list, tuple)) else pages.values():
            predictions = None
            if hasattr(page, "predictions"):
                predictions = page.predictions
            elif hasattr(page, "cells"):
                predictions = page.cells

            if not predictions:
                continue

            pred_list = predictions if isinstance(predictions, (list, tuple)) else [predictions]
            for pred in pred_list:
                if isinstance(pred, (list, tuple)):
                    for cell in pred:
                        lb = self._cell_to_layout_block(cell, page_num)
                        if lb is not None:
                            blocks.append(lb)
                else:
                    lb = self._cell_to_layout_block(pred, page_num)
                    if lb is not None:
                        blocks.append(lb)

        return blocks

    def _item_to_layout_block(self, item: Any, page_num: int) -> LayoutBlock | None:
        """Convert a Docling document item to LayoutBlock."""
        label = None
        bbox_data = None
        confidence = 0.0

        # Extract label
        if hasattr(item, "label"):
            label = str(item.label)
            if hasattr(item.label, "value"):
                label = item.label.value
        elif hasattr(item, "type"):
            label = str(item.type)

        # Extract bbox from prov (provenance)
        if hasattr(item, "prov") and item.prov:
            prov = item.prov[0] if isinstance(item.prov, (list, tuple)) else item.prov
            if hasattr(prov, "bbox"):
                bbox_obj = prov.bbox
                bbox_data = self._parse_bbox(bbox_obj)
                if hasattr(prov, "confidence"):
                    confidence = float(prov.confidence or 0.0)

        if hasattr(item, "bbox") and bbox_data is None:
            bbox_data = self._parse_bbox(item.bbox)

        if label is None or bbox_data is None:
            return None

        normalized_label = _DOCLING_LABEL_MAP.get(label, "Text")
        return LayoutBlock(
            bbox=BBox(
                x0=bbox_data[0],
                y0=bbox_data[1],
                x1=bbox_data[2],
                y1=bbox_data[3],
            ),
            label=normalized_label,
            confidence=confidence,
            page_num=page_num,
        )

    def _cell_to_layout_block(self, cell: Any, page_num: int) -> LayoutBlock | None:
        """Convert a Docling cell/prediction to LayoutBlock."""
        label = None
        bbox_data = None
        confidence = 0.0

        if hasattr(cell, "label"):
            label = str(cell.label)
            if hasattr(cell.label, "value"):
                label = cell.label.value
        elif isinstance(cell, dict):
            label = cell.get("label", cell.get("type"))

        if hasattr(cell, "bbox"):
            bbox_data = self._parse_bbox(cell.bbox)
        elif isinstance(cell, dict) and "bbox" in cell:
            bbox_data = self._parse_bbox(cell["bbox"])

        if hasattr(cell, "confidence"):
            confidence = float(cell.confidence or 0.0)
        elif isinstance(cell, dict):
            confidence = float(cell.get("confidence", 0.0))

        if label is None or bbox_data is None:
            return None

        normalized_label = _DOCLING_LABEL_MAP.get(str(label), "Text")
        return LayoutBlock(
            bbox=BBox(
                x0=bbox_data[0],
                y0=bbox_data[1],
                x1=bbox_data[2],
                y1=bbox_data[3],
            ),
            label=normalized_label,
            confidence=confidence,
            page_num=page_num,
        )

    @staticmethod
    def _parse_bbox(bbox_obj: Any) -> tuple[float, float, float, float] | None:
        """Parse various bbox representations into (x0, y0, x1, y1)."""
        if bbox_obj is None:
            return None

        if isinstance(bbox_obj, (list, tuple)) and len(bbox_obj) >= 4:
            return (float(bbox_obj[0]), float(bbox_obj[1]),
                    float(bbox_obj[2]), float(bbox_obj[3]))

        # Docling BoundingBox object
        for attrs in [
            ("l", "t", "r", "b"),
            ("x0", "y0", "x1", "y1"),
            ("left", "top", "right", "bottom"),
        ]:
            if all(hasattr(bbox_obj, a) for a in attrs):
                vals = [getattr(bbox_obj, a) for a in attrs]
                return (float(vals[0]), float(vals[1]),
                        float(vals[2]), float(vals[3]))

        return None

    @staticmethod
    def _coerce_to_pil(image: RawImage | object) -> Any | None:
        try:
            from PIL import Image
        except Exception:
            return None

        if isinstance(image, Image.Image):
            return image
        if isinstance(image, RawImage):
            try:
                if image.channels == 1:
                    return Image.fromarray(image.data, mode="L").convert("RGB")
                return Image.fromarray(image.data)
            except Exception:
                return None
        return None


__all__ = ["DoclingLayoutDetector"]
