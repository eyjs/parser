"""Layout-detection adapters implementing :class:`LayoutDetector`.

Public exports:
    * :class:`SuryaLayoutDetector` — Surya-OCR layout backend (lazy import).
    * :class:`NullLayoutDetector` — Always-empty fallback used when
      Surya (or any other backend) is unavailable.
"""

from docforge.adapters.layout.surya_adapter import (
    NullLayoutDetector,
    SuryaLayoutDetector,
)

__all__ = ["NullLayoutDetector", "SuryaLayoutDetector"]
