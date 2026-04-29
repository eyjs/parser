"""Domain enumerations for document classification and block typing."""

from enum import Enum, auto


class DocumentComplexity(str, Enum):
    """Document-level complexity classification for parser routing."""

    TEXT_ONLY = "text_only"
    TEXT_WITH_TABLES = "text_tables"
    MIXED = "mixed"
    IMAGE_HEAVY = "image_heavy"


class PageType(str, Enum):
    """Per-page type classification."""

    DIGITAL = "digital"
    SCANNED = "scanned"
    MIXED = "mixed"
    NOISE = "noise"


class BlockType(str, Enum):
    """Text block semantic type."""

    HEADING = "heading"
    CLAUSE = "clause"
    SUBCLAUSE = "subclause"
    ITEM = "item"
    TEXT = "text"
    FOOTNOTE = "footnote"


class SelectionReason(Enum):
    """Quality gate selection reason for preprocessing A/B comparison."""

    ORIGINAL_DEFAULT = auto()
    PREP_CHAR_LOSS = auto()
    PREP_CONFIDENCE_UP = auto()
    PREP_CHAR_GAIN = auto()
    PREP_RESCUED_EMPTY = auto()
    PREPROCESSING_FAILED = auto()
