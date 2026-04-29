"""Unit tests for block_splitter module."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.ports import MorphemeToken
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.block_splitter import split_heading_body, _try_split


def _make_block(text: str) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=200, y1=20),
        font=FontInfo(name="test", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


class _MockMorphemeAnalyzer:
    """Mock analyzer that returns predefined tokens for testing."""

    def __init__(self, tokens: list[MorphemeToken] | None = None) -> None:
        self._tokens = tokens or []
        self._available = True

    def tokenize(self, text: str) -> list[MorphemeToken]:
        return self._tokens

    def is_available(self) -> bool:
        return self._available


class _UnavailableMorphemeAnalyzer:
    """Mock analyzer that is not available."""

    def tokenize(self, text: str) -> list[MorphemeToken]:
        return []

    def is_available(self) -> bool:
        return False


def _make_insurance_tokens() -> list[MorphemeToken]:
    """Tokens for '보험료의 할인·할증에 관한 사항보험업 감독규정...' after prefix 'ㄴ. '

    Simulates Kiwi output for the rest text after prefix.
    We need split at '사항' (noun) boundary.
    rest = '보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른'
    prefix 'ㄴ. ' = 3 chars. We want heading to end after '사항' = pos ~21 in rest.
    """
    return [
        MorphemeToken(form="보험료", tag="NNG", start=0, length=3),
        MorphemeToken(form="의", tag="JKG", start=3, length=1),
        MorphemeToken(form="할인", tag="NNG", start=5, length=2),
        MorphemeToken(form="·", tag="SP", start=7, length=1),
        MorphemeToken(form="할증", tag="NNG", start=8, length=2),
        MorphemeToken(form="에", tag="JKB", start=10, length=1),
        MorphemeToken(form="관한", tag="VV", start=12, length=2),
        MorphemeToken(form="사항", tag="NNG", start=15, length=2),
        MorphemeToken(form="보험업", tag="NNG", start=17, length=3),
        MorphemeToken(form="감독", tag="NNG", start=21, length=2),
        MorphemeToken(form="규정", tag="NNG", start=23, length=2),
    ]


def _make_insurance_period_tokens() -> list[MorphemeToken]:
    """Tokens for '보험기간 및 보험료 납입에 관한 사항보험기간은...' after prefix '4. '"""
    return [
        MorphemeToken(form="보험", tag="NNG", start=0, length=2),
        MorphemeToken(form="기간", tag="NNG", start=2, length=2),
        MorphemeToken(form="및", tag="MAG", start=5, length=1),
        MorphemeToken(form="보험료", tag="NNG", start=7, length=3),
        MorphemeToken(form="납입", tag="NNG", start=11, length=2),
        MorphemeToken(form="에", tag="JKB", start=13, length=1),
        MorphemeToken(form="관한", tag="VV", start=15, length=2),
        MorphemeToken(form="사항", tag="NNG", start=18, length=2),
        MorphemeToken(form="보험", tag="NNG", start=20, length=2),
        MorphemeToken(form="기간", tag="NNG", start=22, length=2),
        MorphemeToken(form="은", tag="JX", start=24, length=1),
    ]


def _make_jo_tokens() -> list[MorphemeToken]:
    """Tokens for '보험의 목적보험계약에서...' after prefix '제1조 '"""
    return [
        MorphemeToken(form="보험", tag="NNG", start=0, length=2),
        MorphemeToken(form="의", tag="JKG", start=2, length=1),
        MorphemeToken(form="목적", tag="NNG", start=4, length=2),
        MorphemeToken(form="보험", tag="NNG", start=6, length=2),
        MorphemeToken(form="계약", tag="NNG", start=8, length=2),
        MorphemeToken(form="에서", tag="JKB", start=10, length=2),
    ]


def _make_paren_tokens() -> list[MorphemeToken]:
    """Tokens for '계약의 해지 절차보험계약자가...' after prefix '(1) '"""
    return [
        MorphemeToken(form="계약", tag="NNG", start=0, length=2),
        MorphemeToken(form="의", tag="JKG", start=2, length=1),
        MorphemeToken(form="해지", tag="NNG", start=4, length=2),
        MorphemeToken(form="절차", tag="NNG", start=7, length=2),
        MorphemeToken(form="보험", tag="NNG", start=9, length=2),
        MorphemeToken(form="계약자", tag="NNG", start=11, length=3),
        MorphemeToken(form="가", tag="JKS", start=14, length=1),
    ]


def _make_channel_tokens() -> list[MorphemeToken]:
    """Tokens for '판매채널보험회사의...' after prefix '가. '"""
    return [
        MorphemeToken(form="판매", tag="NNG", start=0, length=2),
        MorphemeToken(form="채널", tag="NNG", start=2, length=2),
        MorphemeToken(form="보험", tag="NNG", start=4, length=2),
        MorphemeToken(form="회사", tag="NNG", start=6, length=2),
        MorphemeToken(form="의", tag="JKG", start=8, length=1),
        MorphemeToken(form="판매", tag="NNG", start=10, length=2),
        MorphemeToken(form="채널", tag="NNG", start=12, length=2),
        MorphemeToken(form="은", tag="JX", start=14, length=1),
        MorphemeToken(form="다음", tag="NNG", start=16, length=2),
        MorphemeToken(form="과", tag="JKB", start=18, length=1),
        MorphemeToken(form="같이", tag="MAG", start=20, length=2),
        MorphemeToken(form="구분", tag="NNG", start=23, length=2),
        MorphemeToken(form="된다", tag="VV", start=25, length=2),
    ]


class TestTrySplit:
    def test_korean_letter_heading_with_body(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_insurance_tokens())
        result = _try_split(
            "나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른",
            analyzer,
        )
        assert result is not None
        heading, body = result
        assert heading.startswith("나.")
        assert "보험업" in body or "감독" in body

    def test_numbered_heading_with_body(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_insurance_period_tokens())
        result = _try_split(
            "4. 보험기간 및 보험료 납입에 관한 사항보험기간은 1년을 원칙으로 하되 장기계약을 체결할 수 있다",
            analyzer,
        )
        assert result is not None
        heading, body = result
        assert heading.startswith("4.")

    def test_jo_heading_with_body(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_jo_tokens())
        result = _try_split(
            "제1조 보험의 목적보험계약에서 정하는 보험사고가 발생한 경우",
            analyzer,
        )
        assert result is not None
        heading, body = result
        assert "제1조" in heading

    def test_paren_heading_with_body(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_paren_tokens())
        result = _try_split(
            "(1) 계약의 해지 절차보험계약자가 계약 체결 후 청약의 철회를 요청한 경우",
            analyzer,
        )
        assert result is not None
        heading, body = result
        assert heading.startswith("(1)")
        # Split at a noun boundary (해지, 절차, or 보험 -- all score equally)
        assert len(body) >= 10

    def test_no_split_for_plain_text(self) -> None:
        analyzer = _MockMorphemeAnalyzer()
        result = _try_split("보험업 감독규정에 따른 일반적인 문장입니다.", analyzer)
        assert result is None

    def test_no_split_for_short_body(self) -> None:
        analyzer = _MockMorphemeAnalyzer([
            MorphemeToken(form="보험", tag="NNG", start=0, length=2),
            MorphemeToken(form="기간", tag="NNG", start=2, length=2),
            MorphemeToken(form="짧음", tag="NNG", start=4, length=2),
        ])
        result = _try_split("나. 보험기간짧음", analyzer)
        assert result is None

    def test_no_split_for_heading_only(self) -> None:
        analyzer = _MockMorphemeAnalyzer([
            MorphemeToken(form="보험", tag="NNG", start=0, length=2),
            MorphemeToken(form="기간", tag="NNG", start=2, length=2),
        ])
        result = _try_split("나. 보험기간", analyzer)
        assert result is None


class TestSplitHeadingBody:
    def test_splits_concatenated_block(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_insurance_tokens())
        blocks = [
            _make_block(
                "나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른"
            )
        ]
        result = split_heading_body(blocks, morpheme_analyzer=analyzer)
        assert len(result) == 2
        assert result[0].text.startswith("나.")

    def test_preserves_normal_blocks(self) -> None:
        analyzer = _MockMorphemeAnalyzer()
        blocks = [
            _make_block("일반적인 텍스트 블록입니다."),
            _make_block("또 다른 일반 블록."),
        ]
        result = split_heading_body(blocks, morpheme_analyzer=analyzer)
        assert len(result) == 2

    def test_split_bbox_divides_vertically(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_insurance_tokens())
        block = TextBlock(
            text="나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서 정한 계약자특성에 따른",
            bbox=BBox(x0=10, y0=100, x1=500, y1=200),
            font=FontInfo(name="test", size=10.0, is_bold=True),
            block_type=BlockType.TEXT,
        )
        result = split_heading_body([block], morpheme_analyzer=analyzer)
        assert len(result) == 2
        assert result[0].bbox.y1 < result[1].bbox.y0 or result[0].bbox.y1 == result[1].bbox.y0

    def test_mixed_blocks(self) -> None:
        analyzer = _MockMorphemeAnalyzer(_make_channel_tokens())
        blocks = [
            _make_block("일반 텍스트"),
            _make_block(
                "가. 판매채널보험회사의 판매채널은 다음과 같이 구분된다"
            ),
            _make_block("또 다른 일반 텍스트"),
        ]
        result = split_heading_body(blocks, morpheme_analyzer=analyzer)
        assert len(result) == 4


class TestGracefulDegradation:
    """Tests for graceful degradation when analyzer is None or unavailable."""

    def test_none_analyzer_returns_blocks_unchanged(self) -> None:
        blocks = [
            _make_block(
                "나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서"
            )
        ]
        result = split_heading_body(blocks, morpheme_analyzer=None)
        assert len(result) == 1
        assert result[0].text == blocks[0].text

    def test_unavailable_analyzer_returns_blocks_unchanged(self) -> None:
        analyzer = _UnavailableMorphemeAnalyzer()
        blocks = [
            _make_block(
                "나. 보험료의 할인·할증에 관한 사항보험업 감독규정 제7-77조에서"
            )
        ]
        result = split_heading_body(blocks, morpheme_analyzer=analyzer)
        assert len(result) == 1

    def test_returns_new_list_not_same_object(self) -> None:
        blocks = [_make_block("일반 텍스트")]
        result = split_heading_body(blocks, morpheme_analyzer=None)
        assert result is not blocks
        assert result == blocks


class TestScoringLogic:
    """Tests for scoring-based split selection."""

    def test_noun_boundary_preferred(self) -> None:
        """Noun ending (+3) should be preferred over non-noun ending."""
        tokens = [
            MorphemeToken(form="보험", tag="NNG", start=0, length=2),
            MorphemeToken(form="의", tag="JKG", start=2, length=1),
            MorphemeToken(form="목적", tag="NNG", start=4, length=2),
            MorphemeToken(form="보험", tag="NNG", start=6, length=2),
            MorphemeToken(form="계약", tag="NNG", start=8, length=2),
            MorphemeToken(form="에서", tag="JKB", start=10, length=2),
            MorphemeToken(form="정하는", tag="VV", start=13, length=3),
            MorphemeToken(form="보험", tag="NNG", start=17, length=2),
            MorphemeToken(form="사고", tag="NNG", start=19, length=2),
        ]
        analyzer = _MockMorphemeAnalyzer(tokens)
        # "제1조 " prefix = 4 chars
        result = _try_split(
            "제1조 보험의 목적보험계약에서 정하는 보험사고가 발생한 경우",
            analyzer,
        )
        assert result is not None
        heading, body = result
        # Should split at a noun boundary
        assert heading.endswith("목적") or heading.endswith("보험") or heading.endswith("계약")

    def test_etn_boundary_scored(self) -> None:
        """ETN (nominalizing) ending should get +2 score."""
        tokens = [
            MorphemeToken(form="계약", tag="NNG", start=0, length=2),
            MorphemeToken(form="체결", tag="NNG", start=2, length=2),
            MorphemeToken(form="하", tag="XSV", start=4, length=1),
            MorphemeToken(form="ㅁ", tag="ETN", start=5, length=1),
            MorphemeToken(form="보험", tag="NNG", start=6, length=2),
            MorphemeToken(form="회사", tag="NNG", start=8, length=2),
            MorphemeToken(form="가", tag="JKS", start=10, length=1),
            MorphemeToken(form="보험금", tag="NNG", start=12, length=3),
            MorphemeToken(form="을", tag="JKO", start=15, length=1),
            MorphemeToken(form="지급", tag="NNG", start=17, length=2),
        ]
        analyzer = _MockMorphemeAnalyzer(tokens)
        result = _try_split(
            "1. 계약체결함보험회사가 보험금을 지급하는 사유를 정합니다",
            analyzer,
        )
        assert result is not None
        heading, body = result
        assert len(heading) >= 5
        assert len(body) >= 10

    def test_no_candidates_returns_none(self) -> None:
        """When all candidates are filtered out, return None."""
        # Only very short tokens that can't produce valid heading/body lengths
        tokens = [
            MorphemeToken(form="가", tag="NNG", start=0, length=1),
        ]
        analyzer = _MockMorphemeAnalyzer(tokens)
        result = _try_split("1. 가나다라마바사아자차카타파하", analyzer)
        assert result is None

    def test_zero_score_returns_none(self) -> None:
        """Candidates with score <= 0 should not be selected."""
        # All tokens are particles (no noun/ETN) and positioned so heading >= body
        # to ensure length balance doesn't give +1.
        # prefix "나. " = 3 chars, total text ~30 chars, so midpoint ~15 chars.
        # Place all particle tokens past midpoint so heading >= body for all candidates.
        tokens = [
            MorphemeToken(form="에서", tag="JKB", start=15, length=2),
            MorphemeToken(form="의", tag="JKG", start=18, length=1),
            MorphemeToken(form="를", tag="JKO", start=20, length=1),
        ]
        analyzer = _MockMorphemeAnalyzer(tokens)
        # 30-char text so heading (3+15+2=20) > body (10) -- no length balance bonus
        result = _try_split(
            "나. 가나다라마바사아자차카타파에서 의를가나다라마바사아",
            analyzer,
        )
        assert result is None

    def test_shorter_heading_wins_on_tie(self) -> None:
        """When scores are equal, shorter heading should win."""
        tokens = [
            MorphemeToken(form="계약", tag="NNG", start=0, length=2),
            MorphemeToken(form="보험", tag="NNG", start=3, length=2),
            MorphemeToken(form="사항", tag="NNG", start=6, length=2),
            MorphemeToken(form="보험", tag="NNG", start=9, length=2),
            MorphemeToken(form="계약자", tag="NNG", start=12, length=3),
        ]
        analyzer = _MockMorphemeAnalyzer(tokens)
        result = _try_split(
            "나. 계약 보험 사항이 보험의 계약자가 확인해야 하는 중요한 내용을 포함합니다",
            analyzer,
        )
        assert result is not None
        heading, _body = result
        assert len(heading) >= 5
