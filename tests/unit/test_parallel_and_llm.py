"""Tests for parallel processing config and LLM fallback router."""

from __future__ import annotations

import numpy as np
import pytest

from docforge.domain.enums import BlockType, PageType
from docforge.domain.models import (
    LLMFallbackRecord,
    PageConfidence,
    PageContent,
    TextBlock,
)
from docforge.domain.value_objects import BBox, FontInfo, RawImage
from docforge.infrastructure.config import ParserConfig
from docforge.processing.llm_fallback_router import (
    run_llm_fallback,
    should_invoke_llm,
)


def _make_block(text: str = "test", confidence: float = 0.9) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=100, y1=20),
        font=FontInfo(size=10.0, is_bold=False, name="test"),
        block_type=BlockType.TEXT,
        heading_level=0,
        confidence=confidence,
    )


def _make_page(confidence_overall: float = 0.5) -> PageContent:
    return PageContent(
        page_num=1,
        page_type=PageType.SCANNED,
        blocks=(_make_block("테스트 텍스트", 0.5),),
        tables=(),
        raw_text="테스트 텍스트",
        width=595.0,
        height=842.0,
        confidence=PageConfidence(
            overall=confidence_overall,
            ocr_confidence=confidence_overall,
        ),
    )


def _make_raw_image() -> RawImage:
    return RawImage(
        data=np.zeros((100, 100, 3), dtype=np.uint8),
        width=100,
        height=100,
        channels=3,
    )


class TestParallelConfig:
    def test_default_max_workers_is_one(self):
        config = ParserConfig()
        assert config.max_workers == 1

    def test_default_max_ocr_workers_is_one(self):
        config = ParserConfig()
        assert config.max_ocr_workers == 1

    def test_custom_max_workers(self):
        config = ParserConfig(max_workers=4, max_ocr_workers=2)
        assert config.max_workers == 4
        assert config.max_ocr_workers == 2


class TestLLMConfig:
    def test_llm_fallback_enabled_by_default(self):
        config = ParserConfig()
        assert config.llm_fallback_enabled is True

    def test_llm_config_fields(self):
        config = ParserConfig(
            llm_fallback_enabled=True,
            llm_confidence_threshold=0.6,
            llm_confidence_margin=0.1,
            llm_char_loss_threshold=0.9,
            llm_domain_hint="법률 문서",
        )
        assert config.llm_fallback_enabled is True
        assert config.llm_confidence_threshold == 0.6
        assert config.llm_confidence_margin == 0.1
        assert config.llm_char_loss_threshold == 0.9
        assert config.llm_domain_hint == "법률 문서"


class TestShouldInvokeLLM:
    def test_disabled_returns_false(self):
        page = _make_page(0.5)
        config = ParserConfig(llm_fallback_enabled=False)
        assert should_invoke_llm(page, config) is False

    def test_no_confidence_returns_false(self):
        page = PageContent(
            page_num=1, page_type=PageType.SCANNED,
            blocks=(), tables=(), raw_text="",
            confidence=None,
        )
        config = ParserConfig(llm_fallback_enabled=True)
        assert should_invoke_llm(page, config) is False

    def test_high_confidence_returns_false(self):
        page = _make_page(0.9)
        config = ParserConfig(llm_fallback_enabled=True, llm_confidence_threshold=0.7)
        assert should_invoke_llm(page, config) is False

    def test_low_confidence_returns_true(self):
        page = _make_page(0.5)
        config = ParserConfig(llm_fallback_enabled=True, llm_confidence_threshold=0.7)
        assert should_invoke_llm(page, config) is True

    def test_exact_threshold_returns_false(self):
        page = _make_page(0.7)
        config = ParserConfig(llm_fallback_enabled=True, llm_confidence_threshold=0.7)
        assert should_invoke_llm(page, config) is False


class FakeLLMEngine:
    """Fake VisionLLMEngine for testing."""

    def __init__(self, blocks: list[TextBlock] | None = None, should_fail: bool = False):
        self._blocks = blocks or []
        self._should_fail = should_fail

    def correct_page(self, image: RawImage, ocr_blocks: list[TextBlock], prompt_hint: str = "") -> list[TextBlock]:
        if self._should_fail:
            raise RuntimeError("LLM failed")
        return self._blocks

    def is_available(self) -> bool:
        return True


class TestRunLLMFallback:
    def test_llm_failure_keeps_original(self):
        page = _make_page(0.5)
        config = ParserConfig(llm_fallback_enabled=True)
        engine = FakeLLMEngine(should_fail=True)
        image = _make_raw_image()

        blocks, record = run_llm_fallback(page, image, engine, config)
        assert record.adopted is False
        assert "failed" in record.reason.lower()
        assert len(blocks) == len(page.blocks)

    def test_llm_char_loss_rejected(self):
        page = _make_page(0.5)
        config = ParserConfig(llm_fallback_enabled=True, llm_char_loss_threshold=0.8)
        short_block = _make_block("짧", 0.95)
        engine = FakeLLMEngine(blocks=[short_block])
        image = _make_raw_image()

        blocks, record = run_llm_fallback(page, image, engine, config)
        assert record.adopted is False
        assert "char loss" in record.reason.lower()

    def test_llm_adopted_when_better(self):
        page = _make_page(0.5)
        config = ParserConfig(
            llm_fallback_enabled=True,
            llm_confidence_margin=0.05,
            llm_char_loss_threshold=0.5,
        )
        better_block = _make_block("테스트 텍스트 교정됨", 0.95)
        engine = FakeLLMEngine(blocks=[better_block])
        image = _make_raw_image()

        blocks, record = run_llm_fallback(page, image, engine, config)
        assert record.adopted is True
        assert blocks[0].text == "테스트 텍스트 교정됨"

    def test_llm_rejected_no_improvement(self):
        page = _make_page(0.5)
        config = ParserConfig(
            llm_fallback_enabled=True,
            llm_confidence_margin=0.05,
            llm_char_loss_threshold=0.5,
        )
        same_block = _make_block("테스트 텍스트", 0.52)
        engine = FakeLLMEngine(blocks=[same_block])
        image = _make_raw_image()

        blocks, record = run_llm_fallback(page, image, engine, config)
        assert record.adopted is False
        assert "no significant" in record.reason.lower()

    def test_llm_fallback_record_fields(self):
        record = LLMFallbackRecord(
            page_num=3,
            trigger_confidence=0.45,
            llm_confidence=0.85,
            adopted=True,
            reason="test",
        )
        assert record.page_num == 3
        assert record.trigger_confidence == 0.45
        assert record.llm_confidence == 0.85
        assert record.adopted is True


class TestVisionLLMEngineProtocol:
    def test_protocol_defined(self):
        from docforge.domain.ports import VisionLLMEngine
        assert hasattr(VisionLLMEngine, 'correct_page')
        assert hasattr(VisionLLMEngine, 'is_available')
        assert hasattr(VisionLLMEngine, 'describe_image')


class TestParseResultWithLLMRecords:
    def test_parse_result_default_empty_records(self):
        from docforge.domain.models import ParseResult, Metadata, NoiseStats, ParseStats
        from docforge.domain.value_objects import DocumentProfile
        from docforge.domain.enums import DocumentComplexity

        result = ParseResult(
            pages=(),
            markdown="",
            metadata=Metadata(
                source="test.pdf", source_type="digital_pdf", pages=0,
                parsed_at="2026-01-01", parser_version="1.0.0",
                ocr_used=False, tables_extracted=0, tables_need_review=0,
                noise_removed=NoiseStats(),
            ),
            stats=ParseStats(),
            profile=DocumentProfile(
                total_pages=0, text_pages=0, image_only_pages=0,
                total_chars=0, has_tables=False, avg_chars_per_page=0.0,
                image_area_ratio=0.0, complexity=DocumentComplexity.TEXT_ONLY,
                recommended_parser="digital",
            ),
        )
        assert result.llm_fallback_records == ()
