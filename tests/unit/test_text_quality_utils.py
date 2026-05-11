"""Tests for text_quality_utils -- garbled text detection."""

from __future__ import annotations

import pytest

from docforge.processing.text_quality_utils import garbled_ratio, is_garbled_text

# Private Use Area characters generated via chr() to avoid encoding issues
_PUA_CHARS_10 = "".join(chr(0xE000 + i) for i in range(10))
_PUA_CHARS_9 = "".join(chr(0xE000 + i) for i in range(9))
_PUA_CHARS_8 = "".join(chr(0xE000 + i) for i in range(8))
_PUA_CHARS_6 = "".join(chr(0xE000 + i) for i in range(6))
_PUA_CHARS_5 = "".join(chr(0xE000 + i) for i in range(5))
_PUA_CHARS_1 = chr(0xE000)
_PUA_FULLY_GARBLED = "".join(chr(0xE000 + i) for i in range(20))


class TestIsGarbledText:
    def test_normal_korean_text_returns_false(self) -> None:
        # Arrange
        text = "이 문서는 정상적인 한국어 텍스트입니다. 계약서 제1조 내용."
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_normal_english_text_returns_false(self) -> None:
        # Arrange
        text = "This is a normal English document with standard characters."
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_private_use_area_unicode_returns_true(self) -> None:
        # Arrange -- all PUA characters, garbled_ratio = 1.0
        text = _PUA_FULLY_GARBLED
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is True

    def test_empty_string_returns_false(self) -> None:
        # Arrange
        text = ""
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_whitespace_only_returns_false(self) -> None:
        # Arrange
        text = "   \n\t  "
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_mostly_garbled_mixed_text_returns_true(self) -> None:
        # Arrange -- 1 readable + 9 garbled PUA = ratio 0.1 < 0.3 -> garbled
        text = "A" + _PUA_CHARS_9
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is True

    def test_mostly_readable_mixed_text_returns_false(self) -> None:
        # Arrange -- 9 readable + 1 garbled PUA = ratio 0.89 >= 0.3 -> not garbled
        readable = "Hello한국!"
        text = readable + _PUA_CHARS_1
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_digits_and_punctuation_are_readable(self) -> None:
        # Arrange
        text = "1234567890.,;:!?()-/\\[]{}@#$%&*+=<>~`'\""
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_circled_numbers_are_readable(self) -> None:
        # Arrange -- U+2460 (①) ~ U+2473 (⑳) are in the readable set
        text = "①②③④⑤⑥⑦⑧⑨⑩"
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False

    def test_threshold_boundary_below_0_3_is_garbled(self) -> None:
        # Arrange -- 2 readable out of 10 total = 0.2 < 0.3 -> garbled
        text = "AB" + _PUA_CHARS_8
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is True

    def test_threshold_boundary_above_0_3_is_not_garbled(self) -> None:
        # Arrange -- 4 readable out of 10 total = 0.4 >= 0.3 -> not garbled
        text = "ABCD" + _PUA_CHARS_6
        # Act
        result = is_garbled_text(text)
        # Assert
        assert result is False


class TestGarbledRatio:
    def test_normal_text_returns_low_ratio(self) -> None:
        # Arrange
        text = "정상적인 한국어 텍스트입니다. Normal English too."
        # Act
        ratio = garbled_ratio(text)
        # Assert
        assert 0.0 <= ratio <= 0.1

    def test_fully_garbled_returns_high_ratio(self) -> None:
        # Arrange -- all PUA characters
        text = _PUA_FULLY_GARBLED
        # Act
        ratio = garbled_ratio(text)
        # Assert
        assert ratio == 1.0

    def test_empty_string_returns_zero(self) -> None:
        # Arrange
        text = ""
        # Act
        ratio = garbled_ratio(text)
        # Assert
        assert ratio == 0.0

    def test_whitespace_only_returns_zero(self) -> None:
        # Arrange
        text = "   \t\n  "
        # Act
        ratio = garbled_ratio(text)
        # Assert
        assert ratio == 0.0

    def test_ratio_is_between_zero_and_one(self) -> None:
        # Arrange
        texts = [
            "Hello World",
            "한국어 텍스트",
            "ABC",
            "",
            "Mixed text",
        ]
        for text in texts:
            # Act
            ratio = garbled_ratio(text)
            # Assert
            assert 0.0 <= ratio <= 1.0, f"ratio={ratio} out of range for text={text!r}"

    def test_half_garbled_returns_near_half(self) -> None:
        # Arrange -- 5 readable + 5 garbled = ratio exactly 0.5
        text = "ABCDE" + _PUA_CHARS_5
        # Act
        ratio = garbled_ratio(text)
        # Assert
        assert abs(ratio - 0.5) < 0.01

    def test_fully_readable_returns_zero(self) -> None:
        # Arrange
        text = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        # Act
        ratio = garbled_ratio(text)
        # Assert
        assert ratio == 0.0

    def test_garbled_ratio_consistency_with_is_garbled(self) -> None:
        # Arrange -- fully garbled text should be consistent
        text = _PUA_FULLY_GARBLED
        # Act
        ratio = garbled_ratio(text)
        garbled = is_garbled_text(text)
        # Assert
        assert ratio > 0.7
        assert garbled is True


class TestKoreanGarbledDetection:
    """Korean-specific garbled text detection tests (P0-3)."""

    def test_garbled_ocr_output_detected(self) -> None:
        """Typical garbled Korean OCR output with digit-syllable mixing."""
        text = "0서 서저 0노포가 으려하는 고"
        ratio = garbled_ratio(text)
        assert ratio >= 0.3, f"Expected garbled ratio >= 0.3, got {ratio}"
        assert is_garbled_text(text) is True

    def test_isolated_jamo_detected(self) -> None:
        """Pure isolated jamo characters are garbled."""
        text = "ㄱㄴㄷㄹ ㅁㅂㅅ ㅇㅈㅊ"
        ratio = garbled_ratio(text)
        assert ratio >= 0.3, f"Expected garbled ratio >= 0.3, got {ratio}"
        assert is_garbled_text(text) is True

    def test_digit_hangul_interleave_detected(self) -> None:
        """Digits interleaved with Korean syllables: garbled."""
        text = "7으3려2하1는"
        ratio = garbled_ratio(text)
        assert ratio >= 0.3, f"Expected garbled ratio >= 0.3, got {ratio}"
        assert is_garbled_text(text) is True

    def test_jamo_inserted_in_syllables_detected(self) -> None:
        """Jamo inserted between syllables: garbled."""
        text = "ㅂ텍ㄹ스트 ㅇ질"
        ratio = garbled_ratio(text)
        assert ratio >= 0.3, f"Expected garbled ratio >= 0.3, got {ratio}"
        assert is_garbled_text(text) is True

    def test_leading_zeros_garbled(self) -> None:
        """Leading zeros mixed with garbled Korean: garbled."""
        text = "0 0 0 서저노포가 으려하"
        ratio = garbled_ratio(text)
        assert ratio >= 0.3, f"Expected garbled ratio >= 0.3, got {ratio}"
        assert is_garbled_text(text) is True

    # --- False-positive prevention (normal Korean must NOT be flagged) ---

    def test_normal_greeting_not_garbled(self) -> None:
        """Standard Korean greeting is not garbled."""
        text = "안녕하세요. 반갑습니다."
        assert is_garbled_text(text) is False
        assert garbled_ratio(text) < 0.3

    def test_chapter_numbering_not_garbled(self) -> None:
        """'제1장 서론' is a natural digit-Korean pattern."""
        text = "제1장 서론"
        assert is_garbled_text(text) is False
        assert garbled_ratio(text) < 0.3

    def test_date_expression_not_garbled(self) -> None:
        """Date expressions like '2024년 3월' are natural."""
        text = "2024년 3월 보고서"
        assert is_garbled_text(text) is False
        assert garbled_ratio(text) < 0.3

    def test_price_expression_not_garbled(self) -> None:
        """Price expressions like '10,000원' are natural."""
        text = "가격: 10,000원"
        assert is_garbled_text(text) is False
        assert garbled_ratio(text) < 0.3

    def test_address_with_numbers_not_garbled(self) -> None:
        """Address with numbers is natural Korean."""
        text = "서울특별시 강남구 테헤란로 123"
        assert is_garbled_text(text) is False
        assert garbled_ratio(text) < 0.3
