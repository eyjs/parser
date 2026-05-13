"""Sprint 6 P0 unit tests — C1, C5, C7, A4.

Covers:
  - C1: ParserConfig max_workers / max_ocr_workers env-var defaults
  - C5: LayoutRouter _LABEL_MAP expansion + _normalize_label
  - C7: EasyOCR engine adapter + OCR factory integration
  - A4: Page reprocessor (should_reprocess / escalate_strategy / select_best_result)
  - A4: PipelineCoordinator reprocessing loop integration
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from docforge.domain.enums import BlockType
from docforge.domain.models import (
    LayoutBlock,
    NoiseStats,
    PageConfidence,
    PageContent,
    TextBlock,
)
from docforge.domain.value_objects import BBox, FontInfo


# ============================================================================
# C1: ParserConfig — env-var-driven parallelism defaults
# ============================================================================


class TestParserConfigWorkerDefaults:
    """C1: max_workers and max_ocr_workers read env vars with 0 = auto."""

    def test_default_is_zero(self) -> None:
        """With no env var set, default is 0 (auto)."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DOCFORGE_MAX_WORKERS", "DOCFORGE_MAX_OCR_WORKERS")}
        with patch.dict(os.environ, env, clear=True):
            from docforge.infrastructure.config import ParserConfig
            cfg = ParserConfig()
            assert cfg.max_workers == 0
            assert cfg.max_ocr_workers == 0

    def test_env_var_override(self) -> None:
        """Env vars should override default."""
        with patch.dict(os.environ, {
            "DOCFORGE_MAX_WORKERS": "8",
            "DOCFORGE_MAX_OCR_WORKERS": "4",
        }):
            from docforge.infrastructure.config import ParserConfig
            cfg = ParserConfig()
            assert cfg.max_workers == 8
            assert cfg.max_ocr_workers == 4

    def test_explicit_argument_overrides_env(self) -> None:
        """Explicit constructor argument wins over env var."""
        with patch.dict(os.environ, {"DOCFORGE_MAX_WORKERS": "8"}):
            from docforge.infrastructure.config import ParserConfig
            cfg = ParserConfig(max_workers=16)
            assert cfg.max_workers == 16

    def test_page_reprocess_defaults(self) -> None:
        """A4 reprocessing config fields have correct defaults."""
        from docforge.infrastructure.config import ParserConfig
        cfg = ParserConfig()
        assert cfg.page_reprocess_enabled is True
        assert cfg.page_reprocess_confidence_threshold == 0.5
        assert cfg.page_reprocess_max_retries == 2


# ============================================================================
# C5: LayoutRouter — _LABEL_MAP expansion + _normalize_label
# ============================================================================


def _tb(
    text: str = "test",
    x0: float = 0, y0: float = 0, x1: float = 100, y1: float = 100,
    block_type: BlockType = BlockType.TEXT,
    heading_level: int = 0,
    block_id: str | None = None,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="N", size=10, is_bold=False),
        block_type=block_type,
        heading_level=heading_level,
        block_id=block_id,
    )


def _lb(label: str, x0: float = 0, y0: float = 0, x1: float = 100, y1: float = 100) -> LayoutBlock:
    return LayoutBlock(bbox=BBox(x0, y0, x1, y1), label=label, confidence=0.9, page_num=1)


class TestLabelMapExpansion:
    """C5: New labels in _LABEL_MAP are mapped correctly."""

    def test_normalize_label_strips_and_lowercases(self) -> None:
        from docforge.processing.layout_router import _normalize_label
        assert _normalize_label("  Title  ") == "title"
        assert _normalize_label("Section-Header") == "section-header"

    def test_title_maps_to_heading_1(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b1")
        lb = _lb("Title")  # should normalize to "title"
        rebuilt, label_map = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.HEADING
        assert rebuilt[0].heading_level == 1
        assert label_map.get("b1") == "Title"

    def test_section_header_maps_to_heading_2(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b2")
        lb = _lb("Section-Header")
        rebuilt, label_map = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.HEADING
        assert rebuilt[0].heading_level == 2

    def test_sectionheader_variant(self) -> None:
        """DocLayNet uses 'SectionHeader' without dash."""
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b3")
        lb = _lb("SectionHeader")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.HEADING
        assert rebuilt[0].heading_level == 2

    def test_caption_maps_to_caption(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b4")
        lb = _lb("Caption")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.CAPTION

    def test_footnote_maps_to_footnote(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b5")
        lb = _lb("Footnote")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.FOOTNOTE

    def test_list_item_maps_to_list(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b6")
        lb = _lb("List-Item")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.LIST

    def test_page_header_maps_correctly(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b7")
        lb = _lb("Page-Header")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.PAGE_HEADER

    def test_page_footer_underscore_variant(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b8")
        lb = _lb("page_footer")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.PAGE_FOOTER

    def test_unknown_label_preserves_original(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b9", block_type=BlockType.TEXT)
        lb = _lb("SomeUnknownLabel")
        rebuilt, label_map = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.TEXT  # unchanged
        assert label_map.get("b9") == "SomeUnknownLabel"

    def test_existing_heading_level_preserved(self) -> None:
        """If block already has heading_level=3, layout 'Title' should not demote to 1."""
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b10", block_type=BlockType.TEXT, heading_level=3)
        lb = _lb("Title")
        rebuilt, _ = merge_and_label([tb], [lb])
        assert rebuilt[0].block_type == BlockType.HEADING
        assert rebuilt[0].heading_level == 3  # preserved, not overridden to 1

    def test_empty_layout_blocks_passthrough(self) -> None:
        from docforge.processing.layout_router import merge_and_label
        tb = _tb(block_id="b11")
        rebuilt, label_map = merge_and_label([tb], [])
        assert rebuilt == [tb]
        assert label_map == {}


# ============================================================================
# C7: EasyOCR engine adapter
# ============================================================================


class TestEasyOCREngine:
    """C7: EasyOCR adapter — availability, conversion, error handling."""

    def test_unavailable_when_not_installed(self) -> None:
        """If easyocr is not importable, is_available returns False."""
        from docforge.adapters.easyocr_engine import EasyOCREngine
        engine = EasyOCREngine()
        # Force re-check
        engine._available = None
        with patch.dict("sys.modules", {"easyocr": None}):
            # Import will raise ImportError for None module
            engine._available = None
            # Try without the actual module
            try:
                import easyocr  # noqa: F401
                pytest.skip("easyocr is installed")
            except ImportError:
                assert engine.is_available() is False

    def test_recognize_returns_empty_when_unavailable(self) -> None:
        from docforge.adapters.easyocr_engine import EasyOCREngine
        engine = EasyOCREngine()
        engine._available = False
        assert engine.recognize(None) == []

    def test_convert_results_empty_input(self) -> None:
        from docforge.adapters.easyocr_engine import EasyOCREngine
        assert EasyOCREngine._convert_results([]) == []

    def test_convert_results_single_item(self) -> None:
        from docforge.adapters.easyocr_engine import EasyOCREngine
        polygon = [[10, 20], [110, 20], [110, 50], [10, 50]]
        results = [(polygon, "Hello World", 0.95)]
        blocks = EasyOCREngine._convert_results(results)
        assert len(blocks) == 1
        assert blocks[0].text == "Hello World"
        assert blocks[0].confidence == 0.95
        assert blocks[0].bbox.x0 == 10.0
        assert blocks[0].bbox.y0 == 20.0
        assert blocks[0].bbox.x1 == 110.0
        assert blocks[0].bbox.y1 == 50.0
        assert blocks[0].block_type == BlockType.TEXT

    def test_convert_results_skips_empty_text(self) -> None:
        from docforge.adapters.easyocr_engine import EasyOCREngine
        results = [
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "", 0.9),
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "   ", 0.9),
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "valid", 0.8),
        ]
        blocks = EasyOCREngine._convert_results(results)
        assert len(blocks) == 1
        assert blocks[0].text == "valid"


class TestOCRFactoryEasyOCR:
    """C7: EasyOCR is registered in the OCR factory."""

    def test_easyocr_in_supported_backends(self) -> None:
        from docforge.usecases.ocr_factory import SUPPORTED_BACKENDS
        assert "easyocr" in SUPPORTED_BACKENDS

    def test_explicit_easyocr_backend(self) -> None:
        """Requesting 'easyocr' explicitly should attempt creation."""
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        with patch(
            "docforge.usecases.ocr_factory._create_easyocr",
            return_value=mock_engine,
        ):
            from docforge.usecases.ocr_factory import create_ocr_engine
            engine = create_ocr_engine("easyocr")
            assert engine is mock_engine

    def test_auto_linux_tries_easyocr_after_remote(self) -> None:
        """On Linux, auto order: apple_vision_remote -> easyocr -> paddleocr."""
        call_order = []

        def make_factory(name, available):
            def factory():
                call_order.append(name)
                if available:
                    eng = MagicMock()
                    eng.is_available.return_value = True
                    return eng
                return None
            return factory

        with patch("platform.system", return_value="Linux"), \
             patch("docforge.usecases.ocr_factory._create_apple_vision_remote",
                   side_effect=make_factory("remote", False)), \
             patch("docforge.usecases.ocr_factory._create_easyocr",
                   side_effect=make_factory("easyocr", True)), \
             patch("docforge.usecases.ocr_factory._create_paddleocr",
                   side_effect=make_factory("paddle", False)):
            from docforge.usecases.ocr_factory import create_ocr_engine
            engine = create_ocr_engine("auto")
            assert engine is not None
            assert engine.is_available()
            # easyocr tried after remote, before paddle
            assert call_order == ["remote", "easyocr"]


# ============================================================================
# A4: Page reprocessor — pure functions
# ============================================================================


def _make_page_result(confidence: float | None = None) -> "PageResult":
    from docforge.usecases.page_processor import PageResult
    if confidence is None:
        return PageResult(
            page_content=None, tables_info=None,
            noise=NoiseStats(), is_toc=False, ocr_used=False,
        )
    page_conf = PageConfidence(overall=confidence)
    page_content = PageContent(
        page_num=1,
        page_type=__import__("docforge.domain.enums", fromlist=["PageType"]).PageType.DIGITAL,
        blocks=(),
        tables=(),
        raw_text="test",
        confidence=page_conf,
    )
    return PageResult(
        page_content=page_content, tables_info=None,
        noise=NoiseStats(), is_toc=False, ocr_used=False,
    )


class TestShouldReprocess:
    """A4: should_reprocess correctness."""

    def test_below_threshold_returns_true(self) -> None:
        from docforge.processing.page_reprocessor import should_reprocess
        result = _make_page_result(confidence=0.3)
        assert should_reprocess(result, threshold=0.5) is True

    def test_above_threshold_returns_false(self) -> None:
        from docforge.processing.page_reprocessor import should_reprocess
        result = _make_page_result(confidence=0.8)
        assert should_reprocess(result, threshold=0.5) is False

    def test_equal_threshold_returns_false(self) -> None:
        from docforge.processing.page_reprocessor import should_reprocess
        result = _make_page_result(confidence=0.5)
        assert should_reprocess(result, threshold=0.5) is False

    def test_no_content_returns_false(self) -> None:
        from docforge.processing.page_reprocessor import should_reprocess
        result = _make_page_result(confidence=None)  # page_content=None
        assert should_reprocess(result, threshold=0.5) is False


class TestEscalateStrategy:
    """A4: escalate_strategy returns correct hints per attempt."""

    def test_attempt_1_force_ocr(self) -> None:
        from docforge.processing.page_reprocessor import escalate_strategy
        hints = escalate_strategy(1)
        assert hints["force_ocr"] is True
        assert hints["layout_detection_all_pages"] is False

    def test_attempt_2_force_ocr_plus_layout(self) -> None:
        from docforge.processing.page_reprocessor import escalate_strategy
        hints = escalate_strategy(2)
        assert hints["force_ocr"] is True
        assert hints["layout_detection_all_pages"] is True

    def test_attempt_3_same_as_2(self) -> None:
        """Attempts beyond 2 use the same max escalation."""
        from docforge.processing.page_reprocessor import escalate_strategy
        hints = escalate_strategy(3)
        assert hints["force_ocr"] is True
        assert hints["layout_detection_all_pages"] is True


class TestSelectBestResult:
    """A4: select_best_result picks highest confidence."""

    def test_picks_highest(self) -> None:
        from docforge.processing.page_reprocessor import select_best_result
        r1 = _make_page_result(confidence=0.3)
        r2 = _make_page_result(confidence=0.7)
        r3 = _make_page_result(confidence=0.5)
        assert select_best_result([r1, r2, r3]) is r2

    def test_single_result(self) -> None:
        from docforge.processing.page_reprocessor import select_best_result
        r1 = _make_page_result(confidence=0.4)
        assert select_best_result([r1]) is r1

    def test_empty_raises(self) -> None:
        from docforge.processing.page_reprocessor import select_best_result
        with pytest.raises(ValueError, match="at least one"):
            select_best_result([])

    def test_none_content_ranked_last(self) -> None:
        from docforge.processing.page_reprocessor import select_best_result
        r_none = _make_page_result(confidence=None)
        r_low = _make_page_result(confidence=0.2)
        assert select_best_result([r_none, r_low]) is r_low


# ============================================================================
# A4: PipelineCoordinator reprocessing loop integration
# ============================================================================


class FakeProcessorWithConfidence:
    """Mock PageProcessor that returns results with specified confidence."""

    def __init__(
        self,
        initial_confidences: dict[int, float],
        retry_confidences: dict[int, float] | None = None,
    ) -> None:
        self._initial_conf = initial_confidences
        self._retry_conf = retry_confidences or {}
        self._call_counts: dict[int, int] = {}
        self._lock = threading.Lock()

    def process(
        self,
        page_idx: int,
        pdf_path: Path,
        ocr_semaphore: threading.Semaphore,
        log_fn: Callable[[str], None],
        total_pages: int,
        **kwargs,
    ) -> "PageResult":
        from docforge.domain.enums import PageType
        from docforge.usecases.page_processor import PageResult

        with self._lock:
            count = self._call_counts.get(page_idx, 0)
            self._call_counts[page_idx] = count + 1

        # First call uses initial confidence; retries use retry confidence
        if count == 0:
            conf = self._initial_conf.get(page_idx, 0.9)
        else:
            conf = self._retry_conf.get(page_idx, conf if 'conf' in dir() else 0.9)

        page_conf = PageConfidence(overall=conf)
        page_content = PageContent(
            page_num=page_idx + 1,
            page_type=PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text=f"page {page_idx + 1}",
            confidence=page_conf,
        )
        return PageResult(
            page_content=page_content, tables_info=None,
            noise=NoiseStats(), is_toc=False, ocr_used=False,
        )


class TestPipelineCoordinatorReprocessing:
    """A4: PipelineCoordinator reprocessing loop."""

    @pytest.fixture(autouse=True)
    def _reset_executor(self):
        from docforge.usecases.pipeline_coordinator import shutdown_executor
        shutdown_executor()
        yield
        shutdown_executor()

    def test_reprocessing_disabled_skips(self) -> None:
        """When page_reprocess_enabled=False, no reprocessing occurs."""
        from docforge.infrastructure.config import ParserConfig
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorWithConfidence(
            initial_confidences={0: 0.3},  # below threshold
        )
        config = ParserConfig(page_reprocess_enabled=False)
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        results, _ = coord.run(
            Path("dummy.pdf"), 1, threading.Semaphore(1), lambda _: None,
        )
        assert len(results) == 1
        # Should NOT have been reprocessed (only 1 call)
        assert proc._call_counts[0] == 1

    def test_reprocessing_improves_low_confidence(self) -> None:
        """Low-confidence page gets reprocessed and improved result adopted."""
        from docforge.infrastructure.config import ParserConfig
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorWithConfidence(
            initial_confidences={0: 0.3, 1: 0.9},
            retry_confidences={0: 0.7},
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
            page_reprocess_max_retries=2,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        results, _ = coord.run(
            Path("dummy.pdf"), 2, threading.Semaphore(1), lambda _: None,
        )
        assert len(results) == 2
        # Page 0 should have been reprocessed
        assert proc._call_counts[0] > 1
        # Page 1 was above threshold, no retry
        assert proc._call_counts[1] == 1
        # The adopted result for page 0 should be the better one
        assert results[0].page_content.confidence.overall == 0.7

    def test_no_config_means_no_reprocessing(self) -> None:
        """When config is None, reprocessing is skipped."""
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorWithConfidence(
            initial_confidences={0: 0.2},
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=None)
        results, _ = coord.run(
            Path("dummy.pdf"), 1, threading.Semaphore(1), lambda _: None,
        )
        assert len(results) == 1
        assert proc._call_counts[0] == 1

    def test_high_confidence_pages_not_retried(self) -> None:
        """Pages above threshold are left alone."""
        from docforge.infrastructure.config import ParserConfig
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorWithConfidence(
            initial_confidences={0: 0.8, 1: 0.9, 2: 0.95},
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        results, _ = coord.run(
            Path("dummy.pdf"), 3, threading.Semaphore(1), lambda _: None,
        )
        for i in range(3):
            assert proc._call_counts[i] == 1


# ============================================================================
# A4 Sprint 7: override_hints integration — verify hints flow end-to-end
# ============================================================================


class FakeProcessorCaptureHints:
    """Mock that captures override_hints from each process() call."""

    def __init__(
        self,
        initial_confidences: dict[int, float],
        retry_confidences: dict[int, float] | None = None,
    ) -> None:
        self._initial_conf = initial_confidences
        self._retry_conf = retry_confidences or {}
        self._call_counts: dict[int, int] = {}
        self.hint_log: list[tuple[int, int, dict | None]] = []  # (page_idx, call_n, hints)
        self._lock = threading.Lock()

    def process(
        self,
        page_idx: int,
        pdf_path: Path,
        ocr_semaphore: threading.Semaphore,
        log_fn: Callable[[str], None],
        total_pages: int,
        **kwargs,
    ) -> "PageResult":
        from docforge.domain.enums import PageType
        from docforge.usecases.page_processor import PageResult

        with self._lock:
            count = self._call_counts.get(page_idx, 0)
            self._call_counts[page_idx] = count + 1
            self.hint_log.append((page_idx, count, kwargs.get("override_hints")))

        if count == 0:
            conf = self._initial_conf.get(page_idx, 0.9)
        else:
            conf = self._retry_conf.get(page_idx, 0.9)

        page_conf = PageConfidence(overall=conf)
        page_content = PageContent(
            page_num=page_idx + 1,
            page_type=__import__("docforge.domain.enums", fromlist=["PageType"]).PageType.DIGITAL,
            blocks=(),
            tables=(),
            raw_text=f"page {page_idx + 1}",
            confidence=page_conf,
        )
        return PageResult(
            page_content=page_content, tables_info=None,
            noise=NoiseStats(), is_toc=False, ocr_used=False,
        )


class TestReprocessingHintsIntegration:
    """A4 Sprint 7: override_hints end-to-end through the reprocessing loop."""

    @pytest.fixture(autouse=True)
    def _reset_executor(self):
        from docforge.usecases.pipeline_coordinator import shutdown_executor
        shutdown_executor()
        yield
        shutdown_executor()

    def test_escalation_sequence_matches_strategy(self) -> None:
        """Full escalation: attempt 1 = force_ocr, attempt 2 = force_ocr+layout."""
        from docforge.infrastructure.config import ParserConfig
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorCaptureHints(
            initial_confidences={0: 0.2},  # well below threshold
            retry_confidences={0: 0.2},     # never improves
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
            page_reprocess_max_retries=2,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        coord.run(Path("dummy.pdf"), 1, threading.Semaphore(1), lambda _: None)

        # 3 calls total: initial + 2 retries
        assert proc._call_counts[0] == 3

        # Filter hints for page 0 only
        page0_hints = [(call_n, hints) for pid, call_n, hints in proc.hint_log if pid == 0]
        assert len(page0_hints) == 3

        # call 0: initial — no override_hints
        assert page0_hints[0] == (0, None)

        # call 1: attempt 1 — force_ocr=True, layout_detection_all_pages=False
        assert page0_hints[1][0] == 1
        assert page0_hints[1][1] == {"force_ocr": True, "layout_detection_all_pages": False}

        # call 2: attempt 2 — force_ocr=True, layout_detection_all_pages=True
        assert page0_hints[2][0] == 2
        assert page0_hints[2][1] == {"force_ocr": True, "layout_detection_all_pages": True}

    def test_early_exit_on_improved_confidence(self) -> None:
        """If attempt 1 improves confidence, attempt 2 is skipped."""
        from docforge.infrastructure.config import ParserConfig
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorCaptureHints(
            initial_confidences={0: 0.3},
            retry_confidences={0: 0.8},  # improves past threshold on first retry
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
            page_reprocess_max_retries=2,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        results, _ = coord.run(
            Path("dummy.pdf"), 1, threading.Semaphore(1), lambda _: None,
        )

        # Only 2 calls: initial + 1 retry (early exit)
        assert proc._call_counts[0] == 2

        # Adopted result has improved confidence
        assert results[0].page_content.confidence.overall == 0.8

        # Only 1 retry hint captured
        page0_hints = [(call_n, hints) for pid, call_n, hints in proc.hint_log if pid == 0]
        assert len(page0_hints) == 2
        assert page0_hints[1][1] == {"force_ocr": True, "layout_detection_all_pages": False}

    def test_multi_page_only_low_pages_get_hints(self) -> None:
        """In a multi-page doc, only low-confidence pages receive override_hints."""
        from docforge.infrastructure.config import ParserConfig
        from docforge.usecases.pipeline_coordinator import PipelineCoordinator

        proc = FakeProcessorCaptureHints(
            initial_confidences={0: 0.9, 1: 0.2, 2: 0.8},  # only page 1 is low
            retry_confidences={1: 0.7},
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
            page_reprocess_max_retries=2,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        coord.run(Path("dummy.pdf"), 3, threading.Semaphore(1), lambda _: None)

        # Pages 0 and 2: 1 call each, no override_hints
        assert proc._call_counts[0] == 1
        assert proc._call_counts[2] == 1

        # Page 1: retried (initial + 1 retry = 2 calls)
        assert proc._call_counts[1] == 2

        # Verify page 1 got override_hints on retry
        page1_hints = [(call_n, hints) for pid, call_n, hints in proc.hint_log if pid == 1]
        assert page1_hints[0] == (0, None)  # initial
        assert page1_hints[1][1] == {"force_ocr": True, "layout_detection_all_pages": False}
