"""Layout-detection adapters implementing :class:`LayoutDetector`.

Public exports:
    * :class:`DoclingLayoutDetector` — Docling/DocLayNet RT-DETR backend.
    * :class:`SuryaLayoutDetector` — Surya-OCR layout backend (lazy import).
    * :class:`NullLayoutDetector` — Always-empty fallback used when
      no layout backend is available.
"""

from docforge.adapters.layout.docling_adapter import DoclingLayoutDetector
from docforge.adapters.layout.surya_adapter import (
    NullLayoutDetector,
    SuryaLayoutDetector,
)

__all__ = ["DoclingLayoutDetector", "NullLayoutDetector", "SuryaLayoutDetector"]
