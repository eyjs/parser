"""Tests for OCR result correction."""

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.ocr_corrector import correct_blocks


def _make_ocr_block(text: str, confidence: float = 0.95) -> TextBlock:
    """Helper to create an OCR TextBlock."""
    return TextBlock(
        text=text,
        bbox=BBox(x0=50.0, y0=100.0, x1=500.0, y1=120.0),
        font=FontInfo(name="ocr", size=0.0, is_bold=False),
        block_type=BlockType.TEXT,
        confidence=confidence,
    )


class TestOCRCorrection:
    """Test OCR text correction rules."""

    def test_correction_map_applied(self) -> None:
        config = ParserConfig(ocr_correction_map={"웰": "월"})
        blocks = [_make_ocr_block("보험료 납입 웹")]
        # "웰" -> "월" but "웹" should not match
        result = correct_blocks(blocks, config)
        assert result[0].text == "보험료 납입 웹"

    def test_correction_replaces_known_error(self) -> None:
        config = ParserConfig(ocr_correction_map={"웰": "월"})
        blocks = [_make_ocr_block("2웰 보험료")]
        result = correct_blocks(blocks, config)
        assert "2월 보험료" in result[0].text

    def test_low_confidence_not_injected_into_text(self) -> None:
        config = ParserConfig()
        blocks = [_make_ocr_block("인식 결과", confidence=0.6)]
        result = correct_blocks(blocks, config)
        assert "[" not in result[0].text
        assert result[0].text == "인식 결과"

    def test_very_low_confidence_not_injected_into_text(self) -> None:
        config = ParserConfig()
        blocks = [_make_ocr_block("깨진 텍스트", confidence=0.3)]
        result = correct_blocks(blocks, config)
        assert "[" not in result[0].text
        assert result[0].text == "깨진 텍스트"

    def test_high_confidence_no_mark(self) -> None:
        config = ParserConfig()
        blocks = [_make_ocr_block("정상 텍스트", confidence=0.95)]
        result = correct_blocks(blocks, config)
        assert "[" not in result[0].text

    def test_bracket_fix(self) -> None:
        config = ParserConfig()
        blocks = [_make_ocr_block("제1조 (목적")]
        result = correct_blocks(blocks, config)
        assert result[0].text.count("(") == result[0].text.count(")")


class TestImmutability:
    """Verify that correction creates new instances."""

    def test_original_unchanged(self) -> None:
        config = ParserConfig(ocr_correction_map={"웰": "월"})
        original = _make_ocr_block("2웰 보험료")
        result = correct_blocks([original], config)
        assert original.text == "2웰 보험료"
        assert result[0].text != original.text
