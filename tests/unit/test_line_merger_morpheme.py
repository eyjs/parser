"""Tests for line_merger morpheme-tag based merge decisions and graceful fallback.

Validates P0-2:
- When a real (available) MorphemeAnalyzer is injected, merge decisions
  must use Kiwi POS tags (JX/JKB/JKG/JKO/JKS/JKC, MAJ/MAG, EC) instead of
  the legacy hardcoded whitelists in ParserConfig.
- When analyzer is None OR is_available()==False, the legacy whitelist
  fallback path must remain active (no regression).
"""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.ports import MorphemeToken
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.line_merger import merge_lines


def _make_block(
    text: str,
    y0: float = 100.0,
    x0: float = 50.0,
    font_size: float = 10.0,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=500.0, y1=y0 + 12.0),
        font=FontInfo(name="Arial", size=font_size, is_bold=False),
        block_type=BlockType.TEXT,
        heading_level=0,
    )


class _StubAnalyzer:
    """Mock MorphemeAnalyzer with per-text canned responses."""

    def __init__(
        self,
        responses: dict[str, list[MorphemeToken]] | None = None,
        available: bool = True,
    ) -> None:
        self._responses = responses or {}
        self._available = available

    def tokenize(self, text: str) -> list[MorphemeToken]:
        return self._responses.get(text, [])

    def is_available(self) -> bool:
        return self._available


class TestMorphemeBasedMerge:
    """When analyzer is available, decisions use POS tags."""

    def test_postposition_tag_triggers_merge(self) -> None:
        """Curr line whose first token is JKG (조사) -> merge with prev."""
        config = ParserConfig()
        prev_text = "보험계약자는 다음의 사항을"  # ends without sentence terminator
        curr_text = "의 권리를 가진다"  # starts with JKG
        analyzer = _StubAnalyzer({
            curr_text: [MorphemeToken(form="의", tag="JKG", start=0, length=1)],
            prev_text: [MorphemeToken(form="을", tag="JKO", start=10, length=1)],
        })
        blocks = [_make_block(prev_text, y0=100.0), _make_block(curr_text, y0=115.0)]

        result = merge_lines(blocks, 10.0, 12.0, config, morpheme_analyzer=analyzer)

        assert len(result) == 1, "JKG-prefixed continuation must merge"

    def test_conjunction_tag_triggers_merge(self) -> None:
        """Curr line whose first token is MAJ (접속부사) -> merge."""
        config = ParserConfig()
        prev_text = "이전 문단의 내용"
        curr_text = "그러나 다음과 같이 다르다"
        analyzer = _StubAnalyzer({
            curr_text: [MorphemeToken(form="그러나", tag="MAJ", start=0, length=3)],
            prev_text: [MorphemeToken(form="용", tag="NNG", start=6, length=1)],
        })
        blocks = [_make_block(prev_text, y0=100.0), _make_block(curr_text, y0=115.0)]

        result = merge_lines(blocks, 10.0, 12.0, config, morpheme_analyzer=analyzer)

        assert len(result) == 1, "MAJ-prefixed conjunction line must merge"

    def test_connective_ending_tag_triggers_merge(self) -> None:
        """Prev line whose last token is EC (연결어미) -> merge."""
        config = ParserConfig()
        prev_text = "보험금을 지급하고"  # ends with EC '고'
        curr_text = "보험계약을 해지한다"
        analyzer = _StubAnalyzer({
            prev_text: [
                MorphemeToken(form="지급", tag="NNG", start=5, length=2),
                MorphemeToken(form="하", tag="XSV", start=7, length=1),
                MorphemeToken(form="고", tag="EC", start=8, length=1),
            ],
            curr_text: [MorphemeToken(form="보험", tag="NNG", start=0, length=2)],
        })
        blocks = [_make_block(prev_text, y0=100.0), _make_block(curr_text, y0=115.0)]

        result = merge_lines(blocks, 10.0, 12.0, config, morpheme_analyzer=analyzer)

        assert len(result) == 1, "EC-ending continuation must merge"

    def test_unrelated_tag_does_not_force_merge(self) -> None:
        """When morpheme tags don't match merge signals AND prev ends with terminator,
        block stays split."""
        config = ParserConfig()
        prev_text = "이전 문장입니다."  # explicit sentence terminator
        curr_text = "새로운 단락이 시작된다."
        analyzer = _StubAnalyzer({
            curr_text: [MorphemeToken(form="새롭", tag="VA", start=0, length=2)],
            prev_text: [MorphemeToken(form="이전", tag="NNG", start=0, length=2)],
        })
        # Force a layout split via heading-style: but we expect terminator path to split
        blocks = [
            _make_block(prev_text, y0=100.0, font_size=10.0),
            _make_block(curr_text, y0=115.0, font_size=14.0),  # font change triggers split
        ]

        result = merge_lines(blocks, 10.0, 12.0, config, morpheme_analyzer=analyzer)

        assert len(result) == 2


class TestWhitelistFallback:
    """When analyzer is None or unavailable, hardcoded whitelist still works."""

    def test_none_analyzer_uses_whitelist_postposition(self) -> None:
        """Same behavior as before P0-2: '의 권리' merges via config.korean_postpositions."""
        config = ParserConfig()
        # '의' is in config.korean_postpositions
        prev = _make_block("보험계약자는 다음의 사항을", y0=100.0)
        curr = _make_block("의 권리를 가진다", y0=115.0)

        result = merge_lines([prev, curr], 10.0, 12.0, config, morpheme_analyzer=None)

        assert len(result) == 1, "None analyzer must fall back to whitelist merge"

    def test_unavailable_analyzer_uses_whitelist(self) -> None:
        """is_available()==False -> same as None: whitelist path active."""
        config = ParserConfig()
        analyzer = _StubAnalyzer(responses={}, available=False)
        prev = _make_block("보험계약자는 다음의 사항을", y0=100.0)
        curr = _make_block("의 권리를 가진다", y0=115.0)

        result = merge_lines([prev, curr], 10.0, 12.0, config, morpheme_analyzer=analyzer)

        assert len(result) == 1, "Unavailable analyzer must fall back to whitelist"

    def test_default_call_signature_unchanged(self) -> None:
        """Existing callers that don't pass morpheme_analyzer must still work."""
        config = ParserConfig()
        blocks = [_make_block("이 약관에서 사용하는", y0=100.0),
                  _make_block("용어의 뜻은 다음과 같습니다", y0=115.0)]

        # No morpheme_analyzer kwarg at all
        result = merge_lines(blocks, 10.0, 12.0, config)

        assert len(result) == 1


class TestAnalyzerErrorIsolation:
    """Analyzer exceptions must not break merging."""

    class _ExplodingAnalyzer:
        def tokenize(self, text: str) -> list[MorphemeToken]:
            raise RuntimeError("kiwi crashed")

        def is_available(self) -> bool:
            return True

    def test_tokenize_exception_falls_through(self) -> None:
        """If analyzer.tokenize() raises, merge_lines must not propagate."""
        config = ParserConfig()
        blocks = [_make_block("이전 내용", y0=100.0), _make_block("계속되는 내용", y0=115.0)]

        # Should not raise
        result = merge_lines(
            blocks, 10.0, 12.0, config,
            morpheme_analyzer=self._ExplodingAnalyzer(),
        )

        # Behavior is best-effort; we only assert no exception and a list returned.
        assert isinstance(result, list)
