"""Tests for Korean text structure recognition."""

from docforge.domain.enums import BlockType
from docforge.processing.text_structurer import classify_block


class TestHeadingPatterns:
    """Test heading pattern recognition for Korean legal documents."""

    def test_pyeon_h1(self) -> None:
        block_type, level = classify_block("제1편 총칙")
        assert block_type == BlockType.HEADING
        assert level == 1

    def test_jang_h2(self) -> None:
        block_type, level = classify_block("제2장 보험금의 지급")
        assert block_type == BlockType.HEADING
        assert level == 2

    def test_jeol_h3(self) -> None:
        block_type, level = classify_block("제3절 보험료의 납입")
        assert block_type == BlockType.HEADING
        assert level == 3

    def test_gwan_h3(self) -> None:
        block_type, level = classify_block("제1관 통칙")
        assert block_type == BlockType.HEADING
        assert level == 3

    def test_jo_h4(self) -> None:
        block_type, level = classify_block("제1조 (목적) 이 약관은...")
        assert block_type == BlockType.HEADING
        assert level == 4

    def test_jo_with_suffix(self) -> None:
        block_type, level = classify_block("제10조의2 (특별약관) ")
        assert block_type == BlockType.HEADING
        assert level == 4

    def test_jo_standalone(self) -> None:
        block_type, level = classify_block("제5조(보험금 청구)")
        assert block_type == BlockType.HEADING
        assert level == 4


class TestClausePatterns:
    """Test clause (hang) pattern recognition."""

    def test_circled_number_1(self) -> None:
        block_type, _ = classify_block("① 보험계약자는...")
        assert block_type == BlockType.CLAUSE

    def test_circled_number_10(self) -> None:
        block_type, _ = classify_block("⑩ 회사는 제1항에...")
        assert block_type == BlockType.CLAUSE

    def test_circled_number_20(self) -> None:
        block_type, _ = classify_block("⑳ 기타 사항은...")
        assert block_type == BlockType.CLAUSE


class TestSubclausePatterns:
    """Test subclause (ho) pattern recognition."""

    def test_numbered_dot(self) -> None:
        block_type, _ = classify_block("1. 피보험자의 고의")
        assert block_type == BlockType.SUBCLAUSE

    def test_korean_letter_dot(self) -> None:
        block_type, _ = classify_block("가. 질병으로 인한 경우")
        assert block_type == BlockType.SUBCLAUSE

    def test_korean_letter_na(self) -> None:
        block_type, _ = classify_block("나. 상해로 인한 경우")
        assert block_type == BlockType.SUBCLAUSE


class TestItemPatterns:
    """Test item (mok) pattern recognition."""

    def test_korean_paren(self) -> None:
        block_type, _ = classify_block("가) 입원 치료비")
        assert block_type == BlockType.ITEM

    def test_korean_paren_na(self) -> None:
        block_type, _ = classify_block("나) 통원 치료비")
        assert block_type == BlockType.ITEM


class TestFontBasedDetection:
    """Test font-based heading detection."""

    def test_bold_large_font(self) -> None:
        block_type, level = classify_block(
            "보험금의 지급", font_size=16.0, is_bold=True, avg_font_size=10.0
        )
        assert block_type == BlockType.HEADING
        assert level == 2

    def test_large_font_not_bold(self) -> None:
        block_type, level = classify_block(
            "보험금의 지급", font_size=13.0, is_bold=False, avg_font_size=10.0
        )
        assert block_type == BlockType.HEADING
        assert level == 3

    def test_normal_font(self) -> None:
        block_type, _ = classify_block(
            "이 약관에서 사용하는 용어", font_size=10.0, is_bold=False, avg_font_size=10.0
        )
        assert block_type == BlockType.TEXT


class TestPlainText:
    """Test plain text classification."""

    def test_plain_text(self) -> None:
        block_type, level = classify_block("이 약관에서 사용하는 용어의 뜻은 다음과 같습니다.")
        assert block_type == BlockType.TEXT
        assert level == 0

    def test_empty_text(self) -> None:
        block_type, level = classify_block("")
        assert block_type == BlockType.TEXT
        assert level == 0

    def test_whitespace_only(self) -> None:
        block_type, level = classify_block("   ")
        assert block_type == BlockType.TEXT
        assert level == 0
