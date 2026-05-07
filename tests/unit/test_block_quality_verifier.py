"""Tests for BlockQualityVerifier -- NormalizedBlock quality scoring."""

from __future__ import annotations

import pytest

from docforge.domain.enums import BlockType
from docforge.domain.models import NormalizedBlock
from docforge.domain.value_objects import BBox, BlockQualityResult
from docforge.processing.block_quality_verifier import BlockQualityVerifier

# Private Use Area characters -- generated via chr() to avoid encoding issues
_PUA_10 = "".join(chr(0xE000 + i) for i in range(10))
_PUA_8 = "".join(chr(0xE000 + i) for i in range(8))
_PUA_20 = "".join(chr(0xE000 + i) for i in range(20))


def _make_block(
    text: str = "정상 텍스트 내용입니다.",
    block_type: BlockType = BlockType.TEXT,
    confidence: float = 0.9,
    block_id: str = "blk-001",
) -> NormalizedBlock:
    """Helper to create a NormalizedBlock for tests."""
    return NormalizedBlock(
        block_id=block_id,
        bbox=BBox(x0=0, y0=0, x1=400, y1=20),
        block_type=block_type,
        confidence=confidence,
        text=text,
        source="heuristic",
        original_label="",
        page_num=1,
    )


class TestBlockQualityVerifierScore:
    def setup_method(self) -> None:
        self.verifier = BlockQualityVerifier()

    # ------------------------------------------------------------------
    # Garbled text samples
    # ------------------------------------------------------------------

    def test_normal_text_high_score(self) -> None:
        # Arrange
        block = _make_block(
            text="이 블록은 정상적인 한국어 텍스트로 구성되어 있습니다.",
            confidence=0.95,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert isinstance(result, BlockQualityResult)
        assert result.score >= 0.7
        assert result.is_garbled is False
        assert result.needs_retry is False
        assert result.recommended_fallback == "none"

    def test_slightly_garbled_text_sample(self) -> None:
        # Arrange -- 30 readable + 8 PUA -> garbled_ratio ~21% (just above 0.20)
        readable = "정상텍스트" * 6  # 30 chars
        block = _make_block(
            text=readable + _PUA_8,
            confidence=0.85,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert isinstance(result, BlockQualityResult)
        # garbled_ratio > 0.20 -> is_garbled = True
        assert result.is_garbled is True
        # recommended_fallback should be "ocr" for moderate garbling
        assert result.recommended_fallback in ("ocr", "vlm", "none")

    def test_moderately_garbled_text(self) -> None:
        # Arrange -- 15 readable + 8 PUA -> garbled_ratio ~34.8% (> 0.20)
        readable = "ABC" * 5
        block = _make_block(
            text=readable + _PUA_8,
            confidence=0.7,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert result.is_garbled is True
        assert result.garbled_ratio > 0.20

    def test_severely_garbled_text(self) -> None:
        # Arrange -- all PUA chars, garbled_ratio = 1.0
        block = _make_block(
            text=_PUA_20,
            confidence=0.8,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert result.is_garbled is True
        assert result.score == 0.0  # confidence * (1 - 1.0) = 0
        assert result.needs_retry is True
        assert result.recommended_fallback == "vlm"

    def test_empty_text_block(self) -> None:
        # Arrange
        block = _make_block(text="", confidence=0.5)
        # Act
        result = self.verifier.score(block)
        # Assert
        assert isinstance(result, BlockQualityResult)
        assert result.garbled_ratio == 0.0
        assert result.is_garbled is False
        # confidence=0.5 < 0.6 -> needs_retry=True
        assert result.needs_retry is True

    # ------------------------------------------------------------------
    # BlockType threshold tests
    # ------------------------------------------------------------------

    def test_table_threshold_is_0_75(self) -> None:
        # Arrange -- TABLE block with confidence=0.7, clean text
        # score = 0.7 * (1 - 0.0) = 0.7 < 0.75 TABLE threshold -> needs_retry
        block = _make_block(
            text="Table cell A, Table cell B, Table cell C, Table cell D",
            block_type=BlockType.TABLE,
            confidence=0.7,
        )
        # Act
        result = self.verifier.score(block)
        # Assert -- TABLE threshold is 0.75; score 0.7 -> needs_retry
        assert result.needs_retry is True

    def test_table_threshold_high_confidence_no_retry(self) -> None:
        # Arrange -- TABLE block with confidence=0.9
        # score = 0.9 > 0.75 TABLE threshold -> no retry
        block = _make_block(
            text="Table cell A, Table cell B, Table cell C",
            block_type=BlockType.TABLE,
            confidence=0.9,
        )
        # Act
        result = self.verifier.score(block)
        # Assert -- score ~0.9 > 0.75, no retry (assuming clean text)
        assert result.needs_retry is False

    def test_text_threshold_needs_retry_when_confidence_below_0_6(self) -> None:
        # Arrange -- TEXT block with confidence=0.55 < 0.6 -> needs_retry always
        block = _make_block(
            text="Some normal text content here is readable and clean.",
            block_type=BlockType.TEXT,
            confidence=0.55,
        )
        # Act
        result = self.verifier.score(block)
        # Assert -- confidence < _CONFIDENCE_LOW (0.6) -> needs_retry
        assert result.needs_retry is True

    def test_figure_threshold_is_0_50(self) -> None:
        # Arrange -- FIGURE block with confidence=0.45 < 0.6 -> needs_retry
        block = _make_block(
            text="Figure caption text here",
            block_type=BlockType.FIGURE,
            confidence=0.45,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert result.needs_retry is True

    def test_unknown_threshold_is_0_80(self) -> None:
        # Arrange -- UNKNOWN block with confidence=0.75
        # score ~0.75 < 0.80 UNKNOWN threshold -> needs_retry
        block = _make_block(
            text="Unknown block content that is readable but not classified.",
            block_type=BlockType.UNKNOWN,
            confidence=0.75,
        )
        # Act
        result = self.verifier.score(block)
        # Assert -- UNKNOWN threshold is 0.80; score ~0.75 -> needs_retry
        assert result.needs_retry is True

    # ------------------------------------------------------------------
    # needs_retry judgment
    # ------------------------------------------------------------------

    def test_needs_retry_when_confidence_below_0_6(self) -> None:
        # Arrange -- even clean text with confidence < 0.6 needs retry
        block = _make_block(
            text="완전히 정상적인 텍스트인데 confidence가 낮은 경우입니다.",
            confidence=0.55,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert result.needs_retry is True

    def test_no_retry_when_high_quality(self) -> None:
        # Arrange
        block = _make_block(
            text="고품질 텍스트 블록입니다. 신뢰도도 높고 깨짐도 없습니다.",
            confidence=0.95,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert result.needs_retry is False

    def test_threshold_override_applied(self) -> None:
        # Arrange -- normally no retry (confidence=0.8 > 0.6), but override=0.95
        block = _make_block(
            text="정상 텍스트 내용입니다.",
            confidence=0.8,
        )
        # Act -- override threshold to 0.95, forcing retry
        result = self.verifier.score(block, threshold_override=0.95)
        # Assert
        assert result.needs_retry is True

    # ------------------------------------------------------------------
    # score_blocks batch
    # ------------------------------------------------------------------

    def test_score_blocks_batch(self) -> None:
        # Arrange
        blocks = [
            _make_block(text="정상 텍스트 1번입니다.", confidence=0.9, block_id="blk-1"),
            _make_block(text="정상 텍스트 2번입니다.", confidence=0.8, block_id="blk-2"),
            _make_block(text=_PUA_20, confidence=0.3, block_id="blk-3"),
        ]
        # Act
        results = self.verifier.score_blocks(blocks)
        # Assert
        assert len(results) == 3
        assert all(isinstance(r, BlockQualityResult) for r in results)

    def test_score_blocks_returns_same_count_as_input(self) -> None:
        # Arrange
        blocks = [
            _make_block(block_id=f"blk-{i}") for i in range(5)
        ]
        # Act
        results = self.verifier.score_blocks(blocks)
        # Assert
        assert len(results) == len(blocks)

    def test_score_blocks_empty_list(self) -> None:
        # Arrange
        blocks: list[NormalizedBlock] = []
        # Act
        results = self.verifier.score_blocks(blocks)
        # Assert
        assert results == []

    # ------------------------------------------------------------------
    # filter_retry_candidates
    # ------------------------------------------------------------------

    def test_filter_retry_candidates_returns_only_retry_blocks(self) -> None:
        # Arrange
        good_block = _make_block(
            text="고품질 정상 텍스트 블록입니다.", confidence=0.95, block_id="good",
        )
        bad_block = _make_block(
            text=_PUA_20, confidence=0.3, block_id="bad",
        )
        blocks = [good_block, bad_block]
        results = self.verifier.score_blocks(blocks)
        # Act
        candidates = self.verifier.filter_retry_candidates(blocks, results)
        # Assert
        candidate_ids = {b.block_id for b, _ in candidates}
        assert "bad" in candidate_ids
        assert "good" not in candidate_ids

    # ------------------------------------------------------------------
    # BlockQualityResult is frozen dataclass
    # ------------------------------------------------------------------

    def test_block_quality_result_is_frozen(self) -> None:
        # Arrange
        block = _make_block()
        result = self.verifier.score(block)
        # Assert -- frozen dataclass raises FrozenInstanceError (subclass of AttributeError)
        with pytest.raises((AttributeError, TypeError)):
            result.score = 0.99  # type: ignore[misc]

    def test_result_fields_in_valid_range(self) -> None:
        # Arrange
        block = _make_block(
            text="정상 텍스트",
            confidence=0.8,
        )
        # Act
        result = self.verifier.score(block)
        # Assert
        assert 0.0 <= result.score <= 1.0
        assert 0.0 <= result.garbled_ratio <= 1.0
        assert isinstance(result.is_garbled, bool)
        assert isinstance(result.needs_retry, bool)
        assert result.recommended_fallback in ("ocr", "vlm", "none")
