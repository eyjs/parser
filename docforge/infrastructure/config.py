"""Parser configuration with all thresholds and default values."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScorerWeights:
    """Immutable weights for table quality scoring components.

    The three weights must sum to 1.0 (within float tolerance).
    """

    empty_ratio: float = 0.40
    consistency: float = 0.35
    text_length: float = 0.25

    def __post_init__(self) -> None:
        total = self.empty_ratio + self.consistency + self.text_length
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ScorerWeights must sum to 1.0, got {total}"
            )
        if min(self.empty_ratio, self.consistency, self.text_length) < 0.0:
            raise ValueError("ScorerWeights components must be non-negative")


# Domain-specific scoring presets. LEGAL preserves the historical 0.4/0.35/0.25
# weights for backward compatibility with the Korean legal pipeline.
LEGAL_PRESET = ScorerWeights(0.40, 0.35, 0.25)
FINANCIAL_PRESET = ScorerWeights(0.50, 0.40, 0.10)
ACADEMIC_PRESET = ScorerWeights(0.30, 0.30, 0.40)


@dataclass(frozen=True)
class TableConfig:
    """Immutable configuration bundle for table extraction quality control.

    NOTE: ``quality_threshold`` lives on ``ParserConfig.table_quality_threshold``
    (flat field) to keep a single source of truth — ``page_processor`` reads
    the flat field. ``TableConfig`` owns settings without flat counterparts.
    """

    scorer_weights: ScorerWeights = field(default_factory=lambda: LEGAL_PRESET)
    vlm_router_enabled: bool = True


@dataclass(frozen=True)
class ParserConfig:
    """Immutable configuration for the parsing pipeline."""

    # Noise detection
    header_ratio: float = 0.08
    footer_ratio: float = 0.08
    min_noise_repeat: int = 3

    # Page classification thresholds
    min_chars_per_page: int = 50
    image_heavy_ratio: float = 0.5
    image_area_table_hint: float = 0.15
    toc_threshold: float = 0.4

    # Table extraction
    min_table_rows: int = 2
    min_table_cols: int = 2
    snap_tolerance: int = 5
    join_tolerance: int = 5
    edge_min_length: int = 10
    empty_cell_threshold: float = 0.9

    # Cross-page table merging
    table_bottom_ratio: float = 0.20
    table_top_ratio: float = 0.20

    # Line merger
    line_gap_multiplier: float = 1.5
    indent_tolerance: float = 5.0

    # Font-based heading detection
    heading_bold_ratio: float = 1.3
    heading_size_ratio: float = 1.2

    # OCR
    ocr_backend: str = "auto"  # "auto", "easyocr", "apple_vision", "paddleocr"
    ocr_confidence_low: float = 0.8
    ocr_confidence_fail: float = 0.5
    dpi: int = 300

    # Rendering
    verify_dpi: int = 150

    # Table hint keywords
    table_line_keywords: frozenset[str] = field(
        default_factory=lambda: frozenset({
            "─", "━", "│", "┃", "|", "+", "-+-",
        })
    )

    # Korean postpositions for line merger
    korean_postpositions: tuple[str, ...] = (
        "은", "는", "이", "가", "을", "를",
        "에", "의", "로", "와", "과", "도",
        "만", "까지", "부터", "에서",
    )

    # Korean conjunctions for line merger
    korean_conjunctions: tuple[str, ...] = (
        "그러나", "다만", "또한", "및",
        "또는", "그리고", "따라서",
        "아울러",
    )

    # Korean line-ending suffixes suggesting continuation
    korean_continuation_suffixes: tuple[str, ...] = (
        "의", "에", "를", "을", "한", "된",
        "는", "로",
    )

    # OCR correction patterns
    ocr_correction_map: dict[str, str] = field(
        default_factory=lambda: {
            "웰": "월",
            "임": "입",
        }
    )

    # Insurance-specific terms for OCR correction
    insurance_terms: tuple[str, ...] = (
        "피보험자", "보험수익자",
        "약관", "보험금", "보험료",
        "해지환급금", "만기", "갱신",
    )

    # Parallel processing
    max_workers: int = 1
    max_ocr_workers: int = 1

    # LLM Fallback
    llm_fallback_enabled: bool = False
    llm_confidence_threshold: float = 0.7
    llm_confidence_margin: float = 0.05
    llm_char_loss_threshold: float = 0.8
    llm_domain_hint: str = "보험약관"

    # Region-level VLM table routing
    table_quality_threshold: float = 0.6
    region_vlm_enabled: bool = True
    region_vlm_paddle_fallback: bool = True

    # Bundled table configuration (preferred over flat fields above for new code).
    # The flat fields are kept for backward compatibility.
    table_config: TableConfig = field(default_factory=TableConfig)

    # Domain profile selector for text structure recognition.
    # "korean_legal" (default) | "english_academic"
    domain_profile: str = "korean_legal"

    # Phase B-1: Layout detection (Surya). Off by default — opt-in.
    layout_detection_enabled: bool = False
    layout_iou_threshold: float = 0.3

    # Phase B-2: Image extraction + caption matching. Off by default.
    image_extraction_enabled: bool = False
    image_output_dir: str | None = None
