"""Tests for confidence scoring system."""

from __future__ import annotations

from docforge.domain.enums import BlockType, PageType, SelectionReason
from docforge.domain.models import PageConfidence, TextBlock
from docforge.domain.value_objects import BBox, FontInfo, QualityGateResult
from docforge.processing.confidence_scorer import score_page


def _block(
    text: str = "test text",
    block_type: BlockType = BlockType.TEXT,
    confidence: float = 0.9,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=500, y1=20),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=block_type,
        confidence=confidence,
    )


class TestScorePage:
    def test_digital_page_high_confidence(self) -> None:
        blocks = [
            _block("This is a heading", BlockType.HEADING),
            _block("Paragraph text here"),
            _block("More content text"),
        ]
        result = score_page(blocks, PageType.DIGITAL, 595.0, 842.0)
        assert isinstance(result, PageConfidence)
        assert result.ocr_confidence == 1.0  # Digital always 1.0
        assert 0.0 <= result.overall <= 1.0

    def test_scanned_page_low_ocr(self) -> None:
        blocks = [
            _block("blurry text", confidence=0.4),
            _block("more blurry", confidence=0.3),
        ]
        result = score_page(blocks, PageType.SCANNED, 595.0, 842.0)
        assert result.ocr_confidence < 0.5
        assert result.overall < 0.5

    def test_empty_blocks_zero_confidence(self) -> None:
        result = score_page([], PageType.DIGITAL, 595.0, 842.0)
        assert result.overall == 0.0

    def test_preprocessing_applied_flag(self) -> None:
        blocks = [_block()]
        gate = QualityGateResult(
            use_preprocessed=True,
            original_confidence=0.7,
            preprocessed_confidence=0.9,
            original_char_count=100,
            preprocessed_char_count=120,
            reason=SelectionReason.PREP_CONFIDENCE_UP,
            reason_detail="test",
        )
        result = score_page(blocks, PageType.SCANNED, 595.0, 842.0, gate)
        assert result.preprocessing_applied is True

    def test_structure_ratio_boosts_score(self) -> None:
        # Page with many structured blocks
        structured_blocks = [
            _block("heading", BlockType.HEADING),
            _block("clause", BlockType.CLAUSE),
            _block("subclause", BlockType.SUBCLAUSE),
            _block("text"),
            _block("text2"),
        ]
        # Page with no structure
        plain_blocks = [
            _block("text1"),
            _block("text2"),
            _block("text3"),
            _block("text4"),
            _block("text5"),
        ]
        structured_score = score_page(structured_blocks, PageType.DIGITAL, 595.0, 842.0)
        plain_score = score_page(plain_blocks, PageType.DIGITAL, 595.0, 842.0)
        assert structured_score.structure_ratio > plain_score.structure_ratio

    def test_frozen(self) -> None:
        result = score_page([_block()], PageType.DIGITAL, 595.0, 842.0)
        import pytest
        with pytest.raises(AttributeError):
            result.overall = 0.5  # type: ignore[misc]
