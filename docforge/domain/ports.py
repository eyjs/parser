"""Port interfaces (protocols) for dependency inversion.

Adapters implement these protocols so the domain never depends on external libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, Table, TextBlock
from docforge.domain.value_objects import (
    ImageQualityReport,
    PreprocessingDecision,
    RawImage,
)


class DomainProfile(Protocol):
    """Domain-specific text structure profile.

    Encapsulates regex patterns and classification rules for a particular
    document domain (Korean legal, English academic, etc).

    Implementations live in ``docforge.processing.domain_profiles`` and
    are injected into ``text_structurer.classify_block`` so the structurer
    itself stays domain-agnostic.
    """

    def classify(
        self,
        text: str,
        font_size: float,
        is_bold: bool,
        avg_font_size: float,
        heading_bold_ratio: float,
        heading_size_ratio: float,
    ) -> tuple[BlockType, int]:
        """Classify text and return ``(block_type, heading_level)``.

        ``heading_level`` is 0 for non-heading blocks.
        """
        ...

    def name(self) -> str:
        """Return profile identifier for logging/debugging."""
        ...


@dataclass(frozen=True)
class MorphemeToken:
    """Immutable value object representing a single morpheme from analysis."""

    form: str      # 형태소 원형 ("보험", "계약자")
    tag: str       # 품사 태그 ("NNG", "NNP", "JKG", "EF" 등)
    start: int     # 원문에서의 시작 위치 (문자 인덱스)
    length: int    # 형태소 길이


class MorphemeAnalyzer(Protocol):
    """Port for morpheme analysis — tokenize Korean text into morphemes."""

    def tokenize(self, text: str) -> list[MorphemeToken]:
        """Tokenize text into morpheme tokens."""
        ...

    def is_available(self) -> bool:
        """Check if the analyzer backend is installed and ready."""
        ...

# Opaque type aliases for adapter-specific handles.
# Concrete types live in adapters; the domain only sees `object`.
PDFDoc = object
"""Opaque PDF document handle returned by PDFReader.open()."""


class PDFReader(Protocol):
    """Port for reading PDF documents."""

    def open(self, path: Path) -> object:
        """Open a PDF file and return a document handle."""
        ...

    def get_page_count(self, doc: object) -> int:
        """Return the total number of pages."""
        ...

    def extract_text_blocks(self, doc: object, page_idx: int) -> list[TextBlock]:
        """Extract text blocks with font info and bounding boxes."""
        ...

    def extract_raw_text(self, doc: object, page_idx: int) -> str:
        """Extract plain text from a page."""
        ...

    def render_page_image(self, doc: object, page_idx: int, dpi: int) -> object:
        """Render a page as an image. Concrete type is adapter-specific."""
        ...

    def get_page_dimensions(self, doc: object, page_idx: int) -> tuple[float, float]:
        """Return (width, height) of a page."""
        ...

    def get_page_images(self, doc: object, page_idx: int) -> list[dict[str, object]]:
        """Return list of image info dicts with bbox and area."""
        ...

    def close(self, doc: object) -> None:
        """Close the document handle."""
        ...


class OCREngine(Protocol):
    """Port for OCR text recognition."""

    def recognize(self, image: RawImage | object) -> list[TextBlock]:
        """Run OCR on an image and return text blocks."""
        ...

    def is_available(self) -> bool:
        """Check if the OCR engine is installed and ready."""
        ...


class TableExtractor(Protocol):
    """Port for table extraction."""

    def extract_from_page(self, source: object, page_idx: int) -> list[Table]:
        """Extract tables from a PDF page (digital PDF)."""
        ...

    def extract_from_image(self, image: RawImage | object) -> list[Table]:
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

    def parse(self, path: Path) -> object:
        """Parse the document and return a ParseResult."""
        ...

    def supported_extensions(self) -> tuple[str, ...]:
        """Return tuple of supported file extensions (e.g., '.pdf', '.html')."""
        ...


class LayoutDetector(Protocol):
    """Port for page layout detection (Surya, LayoutLMv3, etc.).

    Adapters live in ``docforge.adapters.layout``. The domain depends only
    on this Protocol — no adapter library may be imported from
    ``domain/`` or ``processing/``.
    """

    def detect(self, image: RawImage | object, page_num: int) -> list[LayoutBlock]:
        """Detect layout regions on a rendered page image.

        Returns an empty list when the backend is unavailable or fails.
        """
        ...

    def is_available(self) -> bool:
        """Return True if the backend is installed and ready to run."""
        ...


class VisionLLMEngine(Protocol):
    """Port for Vision LLM — page-level text correction and image captioning."""

    def correct_page(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str = "",
    ) -> list[TextBlock]:
        """Correct OCR output using vision LLM on the page image."""
        ...

    def describe_image(
        self,
        image_data: bytes,
        format: str = "png",
        prompt_hint: str = "",
        block_type: str = "",
        context_text: str = "",
        bbox_info: str = "",
    ) -> str:
        """Generate a concise alt-text description for an image.

        Args:
            image_data: Raw image bytes (PNG or JPEG).
            format: Image format -- ``"png"`` or ``"jpeg"``.
            prompt_hint: Optional domain hint for more relevant captions.
            block_type: Semantic type hint (``"chart"``, ``"figure"``,
                ``"table"``).  Adapters may use this to select a
                specialised prompt.
            context_text: OCR text from the same region, for enriched
                VLM input (especially charts).
            bbox_info: Bounding-box coordinate string for spatial context.

        Returns:
            A short description string, or ``""`` on failure.
        """
        ...

    def is_available(self) -> bool:
        ...
