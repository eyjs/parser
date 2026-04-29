"""Unit tests for morpheme analyzer adapters."""

from __future__ import annotations

import pytest

from docforge.adapters.morpheme_analyzer import NullMorphemeAnalyzer


class TestNullMorphemeAnalyzer:
    def test_is_not_available(self) -> None:
        analyzer = NullMorphemeAnalyzer()
        assert analyzer.is_available() is False

    def test_tokenize_returns_empty(self) -> None:
        analyzer = NullMorphemeAnalyzer()
        result = analyzer.tokenize("한국어 텍스트입니다")
        assert result == []

    def test_tokenize_empty_string(self) -> None:
        analyzer = NullMorphemeAnalyzer()
        result = analyzer.tokenize("")
        assert result == []


class TestKiwiMorphemeAnalyzer:
    """Tests for KiwiMorphemeAnalyzer. Only run when kiwipiepy is installed."""

    @pytest.fixture
    def kiwi_analyzer(self):
        try:
            from docforge.adapters.morpheme_analyzer import KiwiMorphemeAnalyzer
            analyzer = KiwiMorphemeAnalyzer()
            if not analyzer.is_available():
                pytest.skip("kiwipiepy not available")
            return analyzer
        except ImportError:
            pytest.skip("kiwipiepy not installed")

    def test_is_available(self, kiwi_analyzer) -> None:
        assert kiwi_analyzer.is_available() is True

    def test_tokenize_returns_morpheme_tokens(self, kiwi_analyzer) -> None:
        from docforge.domain.ports import MorphemeToken
        result = kiwi_analyzer.tokenize("보험계약자")
        assert len(result) > 0
        assert all(isinstance(t, MorphemeToken) for t in result)

    def test_tokenize_has_correct_fields(self, kiwi_analyzer) -> None:
        result = kiwi_analyzer.tokenize("보험료")
        assert len(result) > 0
        token = result[0]
        assert isinstance(token.form, str)
        assert isinstance(token.tag, str)
        assert isinstance(token.start, int)
        assert isinstance(token.length, int)

    def test_tokenize_empty_string(self, kiwi_analyzer) -> None:
        result = kiwi_analyzer.tokenize("")
        assert result == []

    def test_noun_tags_present(self, kiwi_analyzer) -> None:
        """Korean nouns should get NN* tags."""
        result = kiwi_analyzer.tokenize("보험계약")
        noun_tags = [t for t in result if t.tag.startswith("NN")]
        assert len(noun_tags) > 0

    def test_token_immutability(self, kiwi_analyzer) -> None:
        """MorphemeToken should be frozen (immutable)."""
        result = kiwi_analyzer.tokenize("보험")
        if result:
            with pytest.raises(AttributeError):
                result[0].form = "다른값"  # type: ignore[misc]
