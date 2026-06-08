"""Immutable value objects for the domain layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docforge.domain.enums import DocumentComplexity, SelectionReason

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray
    from docforge.domain.models import TextBlock


@dataclass(frozen=True)
class BBox:
    """Bounding box in PDF coordinate space (origin at top-left)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def overlaps_y(self, other: BBox, tolerance: float = 2.0) -> bool:
        """Check if two boxes overlap vertically within tolerance."""
        return abs(self.center_y - other.center_y) < tolerance

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def iou(self, other: BBox) -> float:
        """Intersection-over-union with another bbox. Returns 0.0 on no overlap."""
        inter_x0 = max(self.x0, other.x0)
        inter_y0 = max(self.y0, other.y0)
        inter_x1 = min(self.x1, other.x1)
        inter_y1 = min(self.y1, other.y1)
        inter_area = max(0.0, inter_x1 - inter_x0) * max(0.0, inter_y1 - inter_y0)
        if inter_area <= 0:
            return 0.0
        union = self.area + other.area - inter_area
        if union <= 0:
            return 0.0
        return inter_area / union


@dataclass(frozen=True)
class FontInfo:
    """Font metadata for a text span."""

    name: str
    size: float
    is_bold: bool


@dataclass(frozen=True)
class RawImage:
    """Domain wrapper for image data. Prevents external library types from leaking."""

    data: NDArray[np.uint8]
    width: int
    height: int
    channels: int  # 1=grayscale, 3=RGB


@dataclass(frozen=True)
class ImageQualityPolicy:
    """Thresholds for image quality diagnosis. Injected from ParserConfig."""

    min_dpi: float = 200.0
    max_skew_degrees: float = 0.5
    min_contrast_ratio: float = 0.3
    max_noise_score: float = 0.4
    max_bg_nonuniformity: float = 0.5
    confidence_margin: float = 0.02
    char_loss_threshold: float = 0.8
    char_gain_threshold: float = 1.2


@dataclass(frozen=True)
class ImageQualityReport:
    """Image quality diagnosis result. Each metric is measured independently."""

    dpi_estimated: float
    skew_angle: float
    contrast_ratio: float
    noise_score: float
    background_uniformity: float

    def needs_upscale(self, policy: ImageQualityPolicy) -> bool:
        return self.dpi_estimated != -1.0 and self.dpi_estimated < policy.min_dpi

    def needs_deskew(self, policy: ImageQualityPolicy) -> bool:
        return abs(self.skew_angle) > policy.max_skew_degrees

    def needs_contrast(self, policy: ImageQualityPolicy) -> bool:
        return self.contrast_ratio < policy.min_contrast_ratio

    def needs_denoise(self, policy: ImageQualityPolicy) -> bool:
        return self.noise_score > policy.max_noise_score

    def needs_binarize(self, policy: ImageQualityPolicy) -> bool:
        return self.background_uniformity > policy.max_bg_nonuniformity

    def is_clean(self, policy: ImageQualityPolicy) -> bool:
        """All metrics are within acceptable range."""
        return not any([
            self.needs_upscale(policy),
            self.needs_deskew(policy),
            self.needs_contrast(policy),
            self.needs_denoise(policy),
            self.needs_binarize(policy),
        ])


@dataclass(frozen=True)
class PreprocessingDecision:
    """Preprocessing decision based on quality diagnosis."""

    quality_report: ImageQualityReport
    apply_upscale: bool = False
    apply_deskew: bool = False
    apply_contrast: bool = False
    apply_denoise: bool = False
    apply_binarize: bool = False
    skew_angle: float = 0.0

    @property
    def skip_all(self) -> bool:
        return not any([
            self.apply_upscale,
            self.apply_deskew,
            self.apply_contrast,
            self.apply_denoise,
            self.apply_binarize,
        ])


@dataclass(frozen=True)
class QualityGateResult:
    """Quality gate A/B comparison result. Contains winning_blocks to avoid re-OCR."""

    use_preprocessed: bool
    original_confidence: float
    preprocessed_confidence: float
    original_char_count: int
    preprocessed_char_count: int
    reason: SelectionReason
    reason_detail: str
    winning_blocks: tuple[TextBlock, ...] = ()


@dataclass(frozen=True)
class DocumentProfile:
    """Document profiling result used for parser routing decision."""

    total_pages: int
    text_pages: int
    image_only_pages: int
    total_chars: int
    has_tables: bool
    avg_chars_per_page: float
    image_area_ratio: float
    complexity: DocumentComplexity
    recommended_parser: str


# ---------------------------------------------------------------------------
# Phase 3: Block-level Adaptive Processing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageStrategy:
    """Per-page processing strategy -- generated by DocumentIntelligence before parsing."""

    page_index: int
    primary_method: str  # "pymupdf_text" | "apple_vision_ocr" | "vlm_full" | "skip"
    fallback_chain: tuple[str, ...]  # ("apple_vision_ocr", "vlm_full") order matters
    block_quality_threshold: float  # blocks scoring below this are retry candidates
    surya_needed: bool
    estimated_complexity: str  # "simple" | "table_heavy" | "image_heavy" | "mixed"


@dataclass(frozen=True)
class DocumentStrategyReport:
    """Pre-parse strategy report -- frozen, JSON-serialisable via to_dict()."""

    pages: tuple[PageStrategy, ...]
    total_pages: int
    strategy_counts: dict[str, int]  # {"pymupdf_text": 277, ...}
    surya_page_count: int
    generated_at: str  # ISO 8601
    detected_language: str = "ko"  # ISO 639-1: "ko", "en", "zh", etc.

    def to_dict(self) -> dict:
        """Serialise for SSE / console reporting."""
        return {
            "total_pages": self.total_pages,
            "strategy_counts": dict(self.strategy_counts),
            "surya_page_count": self.surya_page_count,
            "generated_at": self.generated_at,
            "detected_language": self.detected_language,
            "pages": [
                {
                    "page_index": p.page_index,
                    "primary_method": p.primary_method,
                    "fallback_chain": list(p.fallback_chain),
                    "block_quality_threshold": p.block_quality_threshold,
                    "surya_needed": p.surya_needed,
                    "estimated_complexity": p.estimated_complexity,
                }
                for p in self.pages
            ],
        }


@dataclass(frozen=True)
class BlockQualityResult:
    """Block-level quality verification result."""

    score: float  # 0.0 -- 1.0
    is_garbled: bool
    garbled_ratio: float  # ratio of garbled characters
    needs_retry: bool  # score < threshold
    recommended_fallback: str  # "ocr" | "vlm" | "skip" | "none"
