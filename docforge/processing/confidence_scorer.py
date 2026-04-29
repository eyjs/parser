"""Page-level confidence scoring system.

Calculates a composite confidence score per page based on:
- OCR confidence (average block confidence)
- Text density (chars per page area)
- Structure recognition ratio (structured blocks / total blocks)
- Preprocessing outcome (whether preprocessing was applied and improved results)
"""

from __future__ import annotations

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import PageConfidence, TextBlock
from docforge.domain.value_objects import QualityGateResult


def score_page(
    blocks: list[TextBlock] | tuple[TextBlock, ...],
    page_type: PageType,
    page_width: float,
    page_height: float,
    gate_result: QualityGateResult | None = None,
) -> PageConfidence:
    """Calculate confidence score for a parsed page.

    Args:
        blocks: Text blocks on the page.
        page_type: Page classification.
        page_width: Page width.
        page_height: Page height.
        gate_result: Quality gate result (if preprocessing was attempted).

    Returns:
        PageConfidence with overall score and breakdown.
    """
    ocr_conf = _calc_ocr_confidence(blocks, page_type)
    density = _calc_text_density(blocks, page_width, page_height)
    structure = _calc_structure_ratio(blocks)
    prep_applied = gate_result is not None and gate_result.use_preprocessed

    # Weighted average
    # Digital pages: structure and density matter more
    # Scanned pages: OCR confidence matters more
    if page_type == PageType.DIGITAL:
        overall = (
            ocr_conf * 0.2
            + density * 0.3
            + structure * 0.5
        )
    else:
        overall = (
            ocr_conf * 0.5
            + density * 0.2
            + structure * 0.3
        )

    # Clamp to [0, 1]
    overall = max(0.0, min(1.0, overall))

    return PageConfidence(
        overall=round(overall, 3),
        ocr_confidence=round(ocr_conf, 3),
        text_density=round(density, 3),
        structure_ratio=round(structure, 3),
        preprocessing_applied=prep_applied,
    )


def _calc_ocr_confidence(
    blocks: list[TextBlock] | tuple[TextBlock, ...],
    page_type: PageType,
) -> float:
    """Calculate OCR confidence score."""
    if not blocks:
        return 0.0

    if page_type == PageType.DIGITAL:
        # Digital pages have confidence=1.0 by default
        return 1.0

    # Average confidence across blocks
    total_conf = sum(b.confidence for b in blocks)
    return total_conf / len(blocks)


def _calc_text_density(
    blocks: list[TextBlock] | tuple[TextBlock, ...],
    page_width: float,
    page_height: float,
) -> float:
    """Calculate text density score.

    Higher density = more text extracted = more confident.
    """
    if not blocks or page_width <= 0 or page_height <= 0:
        return 0.0

    total_chars = sum(len(b.text) for b in blocks)
    page_area = page_width * page_height

    # Typical A4 page at 72dpi: ~595x842 = ~500,000 sq pts
    # Well-filled page might have ~3000-5000 chars
    # Normalize: 3000 chars / 500K area = 0.006 chars/sq pt
    chars_per_area = total_chars / page_area

    # Score: 0.006 chars/sq pt = 1.0
    score = min(1.0, chars_per_area / 0.006)
    return score


def _calc_structure_ratio(
    blocks: list[TextBlock] | tuple[TextBlock, ...],
) -> float:
    """Calculate structure recognition ratio.

    Higher ratio of structured blocks (headings, clauses) = better structure.
    """
    if not blocks:
        return 0.0

    structured_types = {
        BlockType.HEADING,
        BlockType.CLAUSE,
        BlockType.SUBCLAUSE,
        BlockType.ITEM,
    }

    structured_count = sum(1 for b in blocks if b.block_type in structured_types)
    total = len(blocks)

    if total == 0:
        return 0.0

    # If there are any structured blocks, it's a good sign
    # Typical legal doc: 20-40% structured
    ratio = structured_count / total

    # Score: 0.2 ratio = 1.0 (good structure recognition)
    return min(1.0, ratio / 0.2)
