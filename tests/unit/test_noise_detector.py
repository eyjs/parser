"""Tests for noise detection and removal."""

from docforge.domain.enums import BlockType
from docforge.domain.models import NoiseStats, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.noise_detector import (
    LearnedPatterns,
    _normalize_for_matching,
    classify_noise,
    filter_noise_from_blocks,
    is_page_number,
    learn_patterns,
)


def _make_block(text: str, y0: float = 100.0, y1: float = 110.0) -> TextBlock:
    """Helper to create a TextBlock at a specific y position."""
    return TextBlock(
        text=text,
        bbox=BBox(x0=50.0, y0=y0, x1=500.0, y1=y1),
        font=FontInfo(name="Arial", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


class TestPageNumberDetection:
    """Test page number pattern matching."""

    def test_simple_number(self) -> None:
        assert is_page_number("42")

    def test_dashed_number(self) -> None:
        assert is_page_number("- 5 -")

    def test_page_keyword(self) -> None:
        assert is_page_number("Page 12")

    def test_fraction(self) -> None:
        assert is_page_number("3/20")

    def test_korean_page(self) -> None:
        assert is_page_number("제3쪽")

    def test_not_page_number(self) -> None:
        assert not is_page_number("제1조 (목적)")

    def test_long_text(self) -> None:
        assert not is_page_number("이 약관에서 사용하는 용어")


class TestNormalizeForMatching:
    """Test text normalization that strips trailing/leading page numbers."""

    def test_strips_trailing_page_number(self) -> None:
        assert _normalize_for_matching("상품명 31") == "상품명"

    def test_strips_trailing_page_number_multi_digit(self) -> None:
        assert _normalize_for_matching("상품명 199") == "상품명"

    def test_normalizes_same_text_with_different_page_numbers(self) -> None:
        a = _normalize_for_matching("무배당 프로미라이프 31")
        b = _normalize_for_matching("무배당 프로미라이프 51")
        assert a == b == "무배당 프로미라이프"

    def test_strips_leading_page_number(self) -> None:
        assert _normalize_for_matching("31 무배당 프로미라이프") == "무배당 프로미라이프"

    def test_pure_number_returns_empty(self) -> None:
        assert _normalize_for_matching("42") == ""

    def test_preserves_embedded_numbers(self) -> None:
        assert _normalize_for_matching("제1조 목적") == "제1조 목적"

    def test_preserves_normal_text(self) -> None:
        assert _normalize_for_matching("상품요약서") == "상품요약서"

    def test_unifies_whitespace(self) -> None:
        assert _normalize_for_matching("무배당   프로미라이프   31") == "무배당 프로미라이프"


class TestNoiseClassification:
    """Test noise classification logic."""

    def test_page_number_classified(self) -> None:
        patterns = LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        )
        config = ParserConfig()

        result = classify_noise("42", 750.0, 800.0, patterns, config)
        assert result == "page_number"

    def test_header_classified(self) -> None:
        patterns = LearnedPatterns(
            header_patterns=frozenset({"보험약관"}),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        )
        config = ParserConfig()

        result = classify_noise("보험약관", 20.0, 800.0, patterns, config)
        assert result == "header"

    def test_footer_classified(self) -> None:
        patterns = LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset({"DB손해보험"}),
            watermark_patterns=frozenset(),
        )
        config = ParserConfig()

        result = classify_noise("DB손해보험", 770.0, 800.0, patterns, config)
        assert result == "footer"

    def test_content_not_classified(self) -> None:
        patterns = LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        )
        config = ParserConfig()

        result = classify_noise("제1조 (목적)", 400.0, 800.0, patterns, config)
        assert result is None


class TestPatternLearning:
    """Test noise pattern learning from page data."""

    def test_learns_repeated_header(self) -> None:
        config = ParserConfig(min_noise_repeat=2)
        pages_data = [
            {"lines": [("보험약관", 20.0, 10.0)], "page_height": 800.0},
            {"lines": [("보험약관", 20.0, 10.0)], "page_height": 800.0},
            {"lines": [("보험약관", 20.0, 10.0)], "page_height": 800.0},
        ]

        patterns = learn_patterns(pages_data, config)
        assert "보험약관" in patterns.header_patterns

    def test_learns_footer_with_variable_page_numbers(self) -> None:
        config = ParserConfig(min_noise_repeat=3)
        pages_data = [
            {"lines": [("상품명 1", 780.0, 10.0)], "page_height": 800.0},
            {"lines": [("상품명 2", 780.0, 10.0)], "page_height": 800.0},
            {"lines": [("상품명 3", 780.0, 10.0)], "page_height": 800.0},
        ]

        patterns = learn_patterns(pages_data, config)
        assert "상품명" in patterns.footer_patterns

    def test_ignores_non_repeated(self) -> None:
        config = ParserConfig(min_noise_repeat=3)
        pages_data = [
            {"lines": [("헤더 텍스트", 20.0, 10.0)], "page_height": 800.0},
        ]

        patterns = learn_patterns(pages_data, config)
        assert len(patterns.header_patterns) == 0


class TestBlockFiltering:
    """Test noise filtering from text blocks."""

    def test_filters_page_numbers(self) -> None:
        blocks = [
            _make_block("제1조 (목적)", y0=100.0, y1=110.0),
            _make_block("42", y0=750.0, y1=760.0),
        ]
        patterns = LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        )
        config = ParserConfig()

        clean, stats = filter_noise_from_blocks(blocks, 800.0, patterns, config)
        assert len(clean) == 1
        assert clean[0].text == "제1조 (목적)"
        assert stats.page_numbers == 1

    def test_preserves_content(self) -> None:
        blocks = [
            _make_block("제1조 (목적)", y0=100.0, y1=110.0),
            _make_block("이 약관에서 사용하는 용어", y0=200.0, y1=210.0),
        ]
        patterns = LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        )
        config = ParserConfig()

        clean, stats = filter_noise_from_blocks(blocks, 800.0, patterns, config)
        assert len(clean) == 2
