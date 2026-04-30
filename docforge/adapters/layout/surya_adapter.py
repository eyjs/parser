"""Surya layout-detection adapter (Phase B-1).

Surya is an MIT-licensed multilingual document analysis toolkit. We only
use its layout sub-pipeline here; OCR is intentionally not invoked.

The adapter is graceful: if ``surya`` (or ``surya_ocr``) is not installed,
``is_available()`` returns False and ``detect()`` returns ``[]``. The
import is lazy so importing this module never triggers a heavy ML import.
"""

from __future__ import annotations

import logging
from typing import Any

from docforge.domain.models import LayoutBlock
from docforge.domain.value_objects import BBox, RawImage

logger = logging.getLogger(__name__)

# Map Surya's native labels onto the canonical {Text|Title|Table|Figure|
# Caption|Formula} vocabulary. Anything not in this map is normalized to
# "Text" so downstream consumers never have to special-case unknowns.
_SURYA_LABEL_MAP: dict[str, str] = {
    "Text": "Text",
    "Title": "Title",
    "Section-header": "Title",
    "SectionHeader": "Title",
    "Table": "Table",
    "Figure": "Figure",
    "Picture": "Figure",
    "Caption": "Caption",
    "Formula": "Formula",
    "Equation": "Formula",
}


class SuryaLayoutDetector:
    """Layout detector backed by the Surya layout model.

    The backend is lazy-loaded on first ``detect()`` call so users who
    do not enable layout detection never pay the import / model-load
    cost. Model weights (~1.5 GB) are downloaded by Surya itself on
    first use.
    """

    def __init__(self) -> None:
        self._loaded: bool = False
        self._predictor: Any | None = None
        self._import_error: Exception | None = None

    # -- availability -------------------------------------------------------

    def is_available(self) -> bool:
        """Probe whether ``surya`` is importable.

        Does NOT instantiate the model. Returns False on any ImportError.
        """
        if self._predictor is not None:
            return True
        if self._import_error is not None:
            return False
        try:
            self._import_predictor_class()
            return True
        except Exception as exc:  # pragma: no cover - import-time failures
            self._import_error = exc
            logger.info("Surya not available: %s", exc)
            return False

    # -- detection ---------------------------------------------------------

    def detect(self, image: RawImage | object, page_num: int) -> list[LayoutBlock]:
        """Run layout detection on a single page image.

        Returns an empty list (graceful degradation) when Surya is not
        installed or detection raises any exception. The caller should
        treat empty output as "no layout info" — never an error.
        """
        if not self._ensure_loaded():
            return []

        pil_image = self._coerce_to_pil(image)
        if pil_image is None:
            return []

        try:
            assert self._predictor is not None
            raw_results = self._predictor([pil_image])
        except Exception as exc:  # pragma: no cover - runtime backend errors
            logger.warning("Surya layout detection failed: %s", exc)
            return []

        return self._normalize_results(raw_results, page_num)

    # -- internals ---------------------------------------------------------

    def _import_predictor_class(self) -> Any:
        """Import the Surya layout predictor class. Lazy.

        Surya's public API moved across versions; we try the newer
        ``surya.layout.LayoutPredictor`` first, then fall back.
        """
        try:
            from surya.layout import LayoutPredictor  # type: ignore[import-not-found]

            return LayoutPredictor
        except Exception:
            from surya_ocr.layout import LayoutPredictor  # type: ignore[import-not-found]

            return LayoutPredictor

    def _ensure_loaded(self) -> bool:
        if self._predictor is not None:
            return True
        if self._import_error is not None:
            return False
        try:
            predictor_cls = self._import_predictor_class()
            self._predictor = predictor_cls()
            self._loaded = True
            return True
        except Exception as exc:
            self._import_error = exc
            logger.info("Surya backend unavailable: %s", exc)
            return False

    @staticmethod
    def _coerce_to_pil(image: RawImage | object) -> Any | None:
        """Best-effort convert RawImage / PIL.Image into the PIL form Surya wants."""
        try:
            from PIL import Image
        except Exception:  # pragma: no cover - Pillow is a hard dep
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

    @staticmethod
    def _normalize_results(raw: Any, page_num: int) -> list[LayoutBlock]:
        """Translate Surya's nested response into ``LayoutBlock`` instances."""
        if not raw:
            return []

        # Surya returns a list (one entry per input image) of objects
        # that expose ``bboxes`` — each with ``bbox``, ``label``,
        # ``confidence``. Be defensive: tolerate both attribute and dict.
        first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        boxes = getattr(first, "bboxes", None)
        if boxes is None and isinstance(first, dict):
            boxes = first.get("bboxes")
        if not boxes:
            return []

        out: list[LayoutBlock] = []
        for entry in boxes:
            bbox_seq = getattr(entry, "bbox", None)
            if bbox_seq is None and isinstance(entry, dict):
                bbox_seq = entry.get("bbox")
            label = getattr(entry, "label", None)
            if label is None and isinstance(entry, dict):
                label = entry.get("label")
            confidence = getattr(entry, "confidence", None)
            if confidence is None and isinstance(entry, dict):
                confidence = entry.get("confidence", 0.0)

            if bbox_seq is None or len(bbox_seq) != 4:
                continue

            normalized_label = _SURYA_LABEL_MAP.get(str(label), "Text")
            out.append(
                LayoutBlock(
                    bbox=BBox(
                        x0=float(bbox_seq[0]),
                        y0=float(bbox_seq[1]),
                        x1=float(bbox_seq[2]),
                        y1=float(bbox_seq[3]),
                    ),
                    label=normalized_label,
                    confidence=float(confidence or 0.0),
                    page_num=page_num,
                )
            )
        return out


class NullLayoutDetector:
    """Always-empty layout detector — used when Surya is unavailable.

    Lets the rest of the pipeline call ``detect()`` unconditionally:
    the empty list signals "no layout info" and existing heuristics keep
    running unchanged.
    """

    def detect(self, image: RawImage | object, page_num: int) -> list[LayoutBlock]:
        return []

    def is_available(self) -> bool:
        return False


__all__ = ["SuryaLayoutDetector", "NullLayoutDetector"]
