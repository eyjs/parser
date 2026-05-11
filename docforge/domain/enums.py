"""Domain enumerations for document classification and block typing."""

from enum import Enum, auto


class DocumentComplexity(str, Enum):
    """Document-level complexity classification for parser routing."""

    TEXT_ONLY = "text_only"
    TEXT_WITH_TABLES = "text_tables"
    MIXED = "mixed"
    IMAGE_HEAVY = "image_heavy"


class PageType(str, Enum):
    """Per-page type classification.

    ``COVER`` and ``TOC`` were split out from the legacy ``NOISE`` bucket
    in Phase B-3 so cover/table-of-contents pages can be preserved with
    dedicated section markers in the markdown output instead of being
    silently dropped. Backward compatibility: pages that previously fell
    into ``NOISE`` still classify as ``NOISE`` unless they match the
    cover/TOC heuristics.
    """

    DIGITAL = "digital"
    SCANNED = "scanned"
    MIXED = "mixed"
    NOISE = "noise"
    COVER = "cover"
    TOC = "toc"


class BlockType(str, Enum):
    """Text block semantic type."""

    HEADING = "heading"
    CLAUSE = "clause"
    SUBCLAUSE = "subclause"
    ITEM = "item"
    TEXT = "text"
    FOOTNOTE = "footnote"
    LIST = "list"
    # --- Phase 2: confidence-based routing ---
    TABLE = "table"
    FIGURE = "figure"
    CHART = "chart"
    CAPTION = "caption"
    # --- Docling/DocLayNet noise labels ---
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    UNKNOWN = "unknown"


class SelectionReason(Enum):
    """Quality gate selection reason for preprocessing A/B comparison."""

    ORIGINAL_DEFAULT = auto()
    PREP_CHAR_LOSS = auto()
    PREP_CONFIDENCE_UP = auto()
    PREP_CHAR_GAIN = auto()
    PREP_RESCUED_EMPTY = auto()
    PREPROCESSING_FAILED = auto()
