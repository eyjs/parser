"""Port interfaces (protocols) for dependency inversion.

Adapters implement these protocols so the domain never depends on external libraries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from docforge.domain.models import Table, TextBlock
from docforge.domain.value_objects import (
    ImageQualityReport,
    PreprocessingDecision,
    RawImage,
)


class PDFReader(Protocol):
    """Port for reading PDF documents."""

    def open(self, path: Path) -> Any:
        """Open a PDF file and return a document handle."""
        ...

    def get_page_count(self, doc: Any) -> int:
        """Return the total number of pages."""
        ...

    def extract_text_blocks(self, doc: Any, page_idx: int) -> list[TextBlock]:
        """Extract text blocks with font info and bounding boxes."""
        ...

    def extract_raw_text(self, doc: Any, page_idx: int) -> str:
        """Extract plain text from a page."""
        ...

    def render_page_image(self, doc: Any, page_idx: int, dpi: int) -> Any:
        """Render a page as a PIL Image."""
        ...

    def get_page_dimensions(self, doc: Any, page_idx: int) -> tuple[float, float]:
        """Return (width, height) of a page."""
        ...

    def get_page_images(self, doc: Any, page_idx: int) -> list[dict[str, Any]]:
        """Return list of image info dicts with bbox and area."""
        ...

    def close(self, doc: Any) -> None:
        """Close the document handle."""
        ...


class OCREngine(Protocol):
    """Port for OCR text recognition."""

    def recognize(self, image: Any) -> list[TextBlock]:
        """Run OCR on a PIL Image and return text blocks."""
        ...

    def is_available(self) -> bool:
        """Check if the OCR engine is installed and ready."""
        ...


class TableExtractor(Protocol):
    """Port for table extraction."""

    def extract_from_page(self, source: Any, page_idx: int) -> list[Table]:
        """Extract tables from a PDF page (digital PDF)."""
        ...

    def extract_from_image(self, image: Any) -> list[Table]:
        """Extract tables from a page image (scanned PDF)."""
        ...


class ImageDiagnostics(Protocol):
    """Port for image quality diagnosis. Measures only, no decisions."""

    def diagnose(self, image: RawImage) -> ImageQualityReport:
        ...


class ImagePreprocessor(Protocol):
    """Port for image preprocessing operations."""

    def preprocess(self, image: RawImage, decision: PreprocessingDecision) -> RawImage:
        """Apply selective preprocessing based on the decision. Returns a new image."""
        ...


class FormatParser(Protocol):
    """Port for document format parsers (PDF, HTML, DOCX, etc.)."""

    def can_parse(self, path: Path) -> bool:
        """Check if this parser can handle the given file."""
        ...

    def parse(self, path: Path) -> Any:
        """Parse the document and return a ParseResult."""
        ...

    def supported_extensions(self) -> tuple[str, ...]:
        """Return tuple of supported file extensions (e.g., '.pdf', '.html')."""
        ...
