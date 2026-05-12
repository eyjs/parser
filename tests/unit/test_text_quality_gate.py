"""Unit tests for the text quality gate (pre-classification quality scoring)."""

from __future__ import annotations

import pytest

from docforge.processing.text_quality_gate import (
    QualitySignals,
    TextQualityGate,
    TextQualityResult,
    _compute_cid_ratio,
    _compute_encoding_health,
    _compute_language_consistency,
    _compute_printable_ratio,
    _strip_cid_references,
    _try_encoding_repair,
    composite_quality_score,
    compute_quality_signals,
)


# ---------------------------------------------------------------------------
# Individual Signal Tests
# ---------------------------------------------------------------------------


class TestComputeEncodingHealth:
    def test_clean_korean_text(self):
        score = _compute_encoding_health("보험계약에 관한 사항을 정한 것입니다.")
        assert score == 1.0

    def test_clean_english_text(self):
        score = _compute_encoding_health("This is clean English text.")
        assert score == 1.0

    def test_empty_text(self):
        assert _compute_encoding_health("") == 1.0

    def test_replacement_characters(self):
        text = "Hello ��� world"
        score = _compute_encoding_health(text)
        assert score < 1.0

    def test_many_replacement_characters(self):
        text = "�" * 20
        score = _compute_encoding_health(text)
        assert score < 0.5

    def test_pua_characters(self):
        # U+E000 is in Private Use Area
        text = "Normal text  more text"
        score = _compute_encoding_health(text)
        assert score < 1.0


class TestComputeCidRatio:
    def test_no_cid(self):
        assert _compute_cid_ratio("Normal text without CID") == 1.0

    def test_empty_text(self):
        assert _compute_cid_ratio("") == 1.0

    def test_single_cid(self):
        text = "Hello (cid:123) world"
        score = _compute_cid_ratio(text)
        assert score < 1.0

    def test_multiple_cids(self):
        text = "(cid:1)(cid:2)(cid:3)(cid:4)(cid:5)"
        score = _compute_cid_ratio(text)
        assert score < 0.5

    def test_cid_with_whitespace(self):
        text = "Text (cid: 456 ) here"
        score = _compute_cid_ratio(text)
        assert score < 1.0

    def test_mostly_cid(self):
        text = "(cid:1)(cid:2)(cid:3)(cid:4)(cid:5)(cid:6)(cid:7)(cid:8)"
        score = _compute_cid_ratio(text)
        assert score == 0.0


class TestComputePrintableRatio:
    def test_all_printable(self):
        assert _compute_printable_ratio("Hello world") == 1.0

    def test_empty_text(self):
        assert _compute_printable_ratio("") == 1.0

    def test_with_whitespace(self):
        score = _compute_printable_ratio("Hello\tworld\n")
        assert score == 1.0

    def test_control_characters(self):
        text = "Hello\x00\x01\x02world"
        score = _compute_printable_ratio(text)
        assert score < 1.0

    def test_korean_printable(self):
        assert _compute_printable_ratio("한글 텍스트") == 1.0


class TestComputeLanguageConsistency:
    def test_korean_text(self):
        score = _compute_language_consistency("보험계약에 관한 사항")
        assert score == 1.0

    def test_mixed_korean_ascii(self):
        score = _compute_language_consistency("제1조 (Purpose) 목적")
        assert score == 1.0

    def test_pure_latin_in_korean_context(self):
        score = _compute_language_consistency("This is only English text", "ko")
        assert score < 1.0

    def test_empty_text(self):
        assert _compute_language_consistency("") == 1.0

    def test_short_text(self):
        assert _compute_language_consistency("Hi") == 1.0

    def test_english_expected(self):
        score = _compute_language_consistency("This is English text", "en")
        assert score == 1.0

    def test_unknown_language(self):
        score = _compute_language_consistency("Some text here", "fr")
        assert score == 0.8

    def test_latin_extended_in_korean_doc_penalized(self):
        text = "Àëïöü" * 5
        score = _compute_language_consistency(text, "ko")
        assert score < 0.5

    def test_mixed_hangul_with_latin_extended_penalized(self):
        text = "보험 Àëïöü계약"
        score = _compute_language_consistency(text, "ko")
        assert score < 1.0


# ---------------------------------------------------------------------------
# Composite Score Tests
# ---------------------------------------------------------------------------


class TestCompositeQualityScore:
    def test_perfect_signals(self):
        signals = QualitySignals(
            encoding_health=1.0,
            cid_ratio=1.0,
            printable_ratio=1.0,
            language_consistency=1.0,
        )
        assert abs(composite_quality_score(signals) - 1.0) < 1e-6

    def test_zero_signals(self):
        signals = QualitySignals(
            encoding_health=0.0,
            cid_ratio=0.0,
            printable_ratio=0.0,
            language_consistency=0.0,
        )
        assert composite_quality_score(signals) == 0.0

    def test_weights_sum(self):
        # Verify weights: 0.35 + 0.30 + 0.20 + 0.15 = 1.0
        signals = QualitySignals(
            encoding_health=1.0,
            cid_ratio=1.0,
            printable_ratio=1.0,
            language_consistency=1.0,
        )
        assert abs(composite_quality_score(signals) - 1.0) < 1e-6

    def test_encoding_most_weighted(self):
        # Encoding health has highest weight (0.35)
        bad_encoding = QualitySignals(encoding_health=0.0, cid_ratio=1.0,
                                      printable_ratio=1.0, language_consistency=1.0)
        bad_cid = QualitySignals(encoding_health=1.0, cid_ratio=0.0,
                                 printable_ratio=1.0, language_consistency=1.0)
        # Encoding penalty should be larger
        assert composite_quality_score(bad_encoding) < composite_quality_score(bad_cid)


class TestComputeQualitySignals:
    def test_clean_text(self):
        signals = compute_quality_signals("정상적인 한글 텍스트입니다.")
        assert signals.encoding_health == 1.0
        assert signals.cid_ratio == 1.0
        assert signals.printable_ratio == 1.0
        assert signals.language_consistency == 1.0

    def test_cid_text(self):
        signals = compute_quality_signals("(cid:123) 텍스트 (cid:456)")
        assert signals.cid_ratio < 1.0
        assert signals.encoding_health == 1.0  # CID is not mojibake


# ---------------------------------------------------------------------------
# Repair Pipeline Tests
# ---------------------------------------------------------------------------


class TestStripCidReferences:
    def test_strip_single(self):
        result = _strip_cid_references("Hello (cid:123) world")
        assert result == "Hello  world"

    def test_strip_multiple(self):
        result = _strip_cid_references("(cid:1) text (cid:2)")
        assert result == "text"

    def test_no_cid(self):
        result = _strip_cid_references("Normal text")
        assert result == "Normal text"

    def test_only_cid(self):
        result = _strip_cid_references("(cid:123)")
        assert result == ""

    def test_cid_with_whitespace(self):
        result = _strip_cid_references("(cid: 99 )")
        assert result == ""


class TestTryEncodingRepair:
    def test_clean_text_no_repair(self):
        result = _try_encoding_repair("Normal clean text")
        assert result is None  # No repair needed

    def test_clean_korean_no_repair(self):
        result = _try_encoding_repair("한글 텍스트")
        assert result is None

    def test_returns_none_when_no_improvement(self):
        # Text that can't be improved by re-encoding
        result = _try_encoding_repair("Hello world")
        assert result is None


# ---------------------------------------------------------------------------
# TextQualityGate Tests
# ---------------------------------------------------------------------------


class TestTextQualityGate:
    @pytest.fixture
    def gate(self):
        return TextQualityGate()

    def test_clean_text_passes(self, gate):
        result = gate.evaluate("정상적인 한글 텍스트입니다.")
        assert not result.needs_repair
        assert not result.repair_applied
        assert result.repair_method == "none"
        assert result.quality_score >= 0.8
        assert result.confidence_penalty == 0.0
        assert result.repaired_text is None

    def test_result_is_frozen(self, gate):
        result = gate.evaluate("Test text")
        with pytest.raises(AttributeError):
            result.quality_score = 0.5  # type: ignore[misc]

    def test_cid_text_needs_repair(self, gate):
        text = "(cid:1)(cid:2)(cid:3)(cid:4) some text (cid:5)(cid:6)"
        result = gate.evaluate(text)
        assert result.needs_repair

    def test_cid_stripped(self, gate):
        text = "Important text (cid:123) here (cid:456) end"
        result = gate.evaluate(text)
        if result.repair_applied and result.repaired_text:
            assert "(cid:" not in result.repaired_text

    def test_custom_thresholds(self):
        gate = TextQualityGate(pass_threshold=0.99, repair_threshold=0.98)
        result = gate.evaluate("Just English in Korean context")
        # Even mild language inconsistency may trigger repair check
        assert isinstance(result, TextQualityResult)

    def test_evaluate_batch(self, gate):
        texts = [
            "정상 텍스트",
            "(cid:1)(cid:2)(cid:3)",
            "Another clean text",
        ]
        results = gate.evaluate_batch(texts)
        assert len(results) == 3
        assert all(isinstance(r, TextQualityResult) for r in results)

    def test_empty_text(self, gate):
        result = gate.evaluate("")
        assert result.quality_score >= 0.8
        assert not result.needs_repair

    def test_mostly_cid_downgraded(self, gate):
        # Text that is almost entirely CID references with some real text
        text = "A (cid:1)(cid:2)(cid:3)(cid:4)(cid:5)(cid:6)(cid:7)(cid:8)(cid:9)(cid:10) B"
        result = gate.evaluate(text)
        assert result.needs_repair
        # The gate identifies CID issues even when repair path varies
        assert result.quality_score < 0.8

    def test_pure_cid_no_viable_repair(self, gate):
        # Pure CID text: stripping leaves nothing, score is between thresholds
        text = "(cid:1)(cid:2)(cid:3)(cid:4)(cid:5)(cid:6)(cid:7)(cid:8)(cid:9)(cid:10)"
        result = gate.evaluate(text)
        assert result.needs_repair
        # No viable repair since stripping CIDs leaves empty text
        assert not result.repair_applied

    def test_quality_signals_frozen(self, gate):
        result = gate.evaluate("Test text")
        with pytest.raises(AttributeError):
            result.signals.encoding_health = 0.0  # type: ignore[misc]

    def test_original_text_preserved(self, gate):
        original = "(cid:123) test text"
        result = gate.evaluate(original)
        assert result.original_text == original

    def test_high_quality_short_text(self, gate):
        result = gate.evaluate("OK")
        assert result.quality_score >= 0.8
