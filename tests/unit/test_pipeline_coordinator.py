"""Tests for PipelineCoordinator (P1-3) and global executor singleton."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pytest

from docforge.domain.models import NoiseStats
from docforge.usecases.page_processor import PageResult
from docforge.usecases.pipeline_coordinator import (
    PipelineCoordinator,
    get_executor,
    shutdown_executor,
)


class FakeProcessor:
    """Mock PageProcessor that records inputs and returns canned results."""

    def __init__(self, total: int, failing_pages: set[int] | None = None) -> None:
        self._total = total
        self._failing = failing_pages or set()
        self.processed_pages: list[int] = []
        self._lock = threading.Lock()

    def process(
        self,
        page_idx: int,
        pdf_path: Path,
        ocr_semaphore: threading.Semaphore,
        log_fn: Callable[[str], None],
        total_pages: int,
        **kwargs,
    ) -> PageResult:
        with self._lock:
            self.processed_pages.append(page_idx)
        if page_idx in self._failing:
            raise RuntimeError(f"boom on page {page_idx}")
        return PageResult(
            page_content=None, tables_info=None,
            noise=NoiseStats(), is_toc=False, ocr_used=False,
        )


def _silent(_msg: str) -> None:
    pass


@pytest.fixture(autouse=True)
def _reset_global_executor():
    """Ensure each test starts with a fresh global executor."""
    shutdown_executor()
    yield
    shutdown_executor()


class TestPipelineCoordinatorRun:

    def test_processes_all_pages(self) -> None:
        proc = FakeProcessor(total=5)
        coord = PipelineCoordinator(page_processor=proc, max_workers=2)
        results, errors = coord.run(
            Path("dummy.pdf"), 5, threading.Semaphore(1), _silent,
        )
        assert len(results) == 5
        assert errors == []
        assert sorted(proc.processed_pages) == [0, 1, 2, 3, 4]

    def test_results_sorted_by_page_index(self) -> None:
        proc = FakeProcessor(total=4)
        coord = PipelineCoordinator(page_processor=proc, max_workers=4)
        results, _ = coord.run(
            Path("dummy.pdf"), 4, threading.Semaphore(2), _silent,
        )
        assert len(results) == 4
        # Ordering invariant guarantees downstream aggregation works
        # regardless of completion order.
        for r in results:
            assert isinstance(r, PageResult)

    def test_failed_page_becomes_page_error(self) -> None:
        proc = FakeProcessor(total=3, failing_pages={1})
        coord = PipelineCoordinator(page_processor=proc, max_workers=1)
        results, errors = coord.run(
            Path("dummy.pdf"), 3, threading.Semaphore(1), _silent,
        )
        # Failed pages still occupy a slot, so total result count == total_pages.
        assert len(results) == 3
        assert len(errors) == 1
        err = errors[0]
        assert err.page_number == 2  # page_idx=1 -> 1-based page 2
        assert err.error_type == "RuntimeError"
        assert "boom" in err.message
        assert err.traceback

    def test_max_workers_clamped_to_at_least_one(self) -> None:
        proc = FakeProcessor(total=2)
        coord = PipelineCoordinator(page_processor=proc, max_workers=0)
        results, errors = coord.run(
            Path("dummy.pdf"), 2, threading.Semaphore(1), _silent,
        )
        assert len(results) == 2
        assert errors == []

    def test_zero_pages(self) -> None:
        proc = FakeProcessor(total=0)
        coord = PipelineCoordinator(page_processor=proc, max_workers=2)
        results, errors = coord.run(
            Path("dummy.pdf"), 0, threading.Semaphore(1), _silent,
        )
        assert results == []
        assert errors == []


class TestGlobalExecutorSingleton:

    def test_get_executor_returns_same_instance(self) -> None:
        """Two calls to get_executor must return the identical object."""
        ex1 = get_executor(4)
        ex2 = get_executor(8)  # max_workers ignored on second call
        assert ex1 is ex2

    def test_shutdown_and_recreate(self) -> None:
        """After shutdown, get_executor creates a fresh instance."""
        ex1 = get_executor(4)
        shutdown_executor()
        ex2 = get_executor(4)
        assert ex1 is not ex2

    def test_concurrent_coordinators_share_pool(self) -> None:
        """Two PipelineCoordinators running concurrently share the same pool."""
        proc_a = FakeProcessor(total=3)
        proc_b = FakeProcessor(total=3)
        coord_a = PipelineCoordinator(page_processor=proc_a, max_workers=2)
        coord_b = PipelineCoordinator(page_processor=proc_b, max_workers=2)

        results_a, errors_a = coord_a.run(
            Path("a.pdf"), 3, threading.Semaphore(1), _silent,
        )
        results_b, errors_b = coord_b.run(
            Path("b.pdf"), 3, threading.Semaphore(1), _silent,
        )
        assert len(results_a) == 3
        assert len(results_b) == 3
        assert errors_a == []
        assert errors_b == []


# ============================================================================
# A4 Sprint 7: override_hints is passed through the reprocessing loop
# ============================================================================


class FakeProcessorCapturingHints:
    """Mock PageProcessor that captures override_hints passed to process()."""

    def __init__(
        self,
        initial_confidences: dict[int, float],
        retry_confidences: dict[int, float] | None = None,
    ) -> None:
        from docforge.domain.enums import PageType

        self._initial_conf = initial_confidences
        self._retry_conf = retry_confidences or {}
        self._call_counts: dict[int, int] = {}
        self._captured_hints: list[dict[str, object] | None] = []
        self._lock = threading.Lock()

    def process(
        self,
        page_idx: int,
        pdf_path: Path,
        ocr_semaphore: threading.Semaphore,
        log_fn: Callable[[str], None],
        total_pages: int,
        **kwargs,
    ) -> PageResult:
        from docforge.domain.enums import PageType
        from docforge.domain.models import PageConfidence, PageContent

        with self._lock:
            count = self._call_counts.get(page_idx, 0)
            self._call_counts[page_idx] = count + 1
            self._captured_hints.append(kwargs.get("override_hints"))

        if count == 0:
            conf = self._initial_conf.get(page_idx, 0.9)
        else:
            conf = self._retry_conf.get(page_idx, 0.9)

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


class TestPipelineCoordinatorOverrideHints:
    """A4 Sprint 7: verify override_hints is forwarded to process()."""

    @pytest.fixture(autouse=True)
    def _reset_executor(self):
        shutdown_executor()
        yield
        shutdown_executor()

    def test_initial_call_has_no_override_hints(self) -> None:
        """First (non-retry) call should not pass override_hints."""
        from docforge.infrastructure.config import ParserConfig

        proc = FakeProcessorCapturingHints(
            initial_confidences={0: 0.9},  # above threshold — no retry
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        coord.run(Path("dummy.pdf"), 1, threading.Semaphore(1), _silent)

        # Only 1 call, no override_hints
        assert len(proc._captured_hints) == 1
        assert proc._captured_hints[0] is None

    def test_retry_attempt_1_passes_force_ocr(self) -> None:
        """Retry attempt 1 should pass force_ocr=True, layout_detection_all_pages=False."""
        from docforge.infrastructure.config import ParserConfig

        proc = FakeProcessorCapturingHints(
            initial_confidences={0: 0.3},  # below threshold
            retry_confidences={0: 0.8},     # improved — exits after attempt 1
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
            page_reprocess_max_retries=2,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        coord.run(Path("dummy.pdf"), 1, threading.Semaphore(1), _silent)

        # 2 calls: initial + 1 retry
        assert proc._call_counts[0] == 2
        # First call: no override_hints
        assert proc._captured_hints[0] is None
        # Second call (attempt 1): force_ocr=True, layout_detection_all_pages=False
        hints_1 = proc._captured_hints[1]
        assert hints_1 is not None
        assert hints_1["force_ocr"] is True
        assert hints_1["layout_detection_all_pages"] is False

    def test_retry_attempt_2_passes_force_ocr_plus_layout(self) -> None:
        """Retry attempt 2 should pass force_ocr=True, layout_detection_all_pages=True."""
        from docforge.infrastructure.config import ParserConfig

        proc = FakeProcessorCapturingHints(
            initial_confidences={0: 0.3},  # below threshold
            retry_confidences={0: 0.3},     # stays low — both retries happen
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
            page_reprocess_max_retries=2,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        coord.run(Path("dummy.pdf"), 1, threading.Semaphore(1), _silent)

        # 3 calls: initial + 2 retries
        assert proc._call_counts[0] == 3
        # First call: no override_hints
        assert proc._captured_hints[0] is None
        # Second call (attempt 1): force_ocr only
        hints_1 = proc._captured_hints[1]
        assert hints_1 is not None
        assert hints_1["force_ocr"] is True
        assert hints_1["layout_detection_all_pages"] is False
        # Third call (attempt 2): force_ocr + layout
        hints_2 = proc._captured_hints[2]
        assert hints_2 is not None
        assert hints_2["force_ocr"] is True
        assert hints_2["layout_detection_all_pages"] is True

    def test_high_confidence_page_no_hints_captured(self) -> None:
        """Pages above threshold should only have the initial call (no hints)."""
        from docforge.infrastructure.config import ParserConfig

        proc = FakeProcessorCapturingHints(
            initial_confidences={0: 0.8, 1: 0.9},
        )
        config = ParserConfig(
            page_reprocess_enabled=True,
            page_reprocess_confidence_threshold=0.5,
        )
        coord = PipelineCoordinator(page_processor=proc, max_workers=1, config=config)
        coord.run(Path("dummy.pdf"), 2, threading.Semaphore(1), _silent)

        # Each page called once, no override_hints
        assert proc._call_counts[0] == 1
        assert proc._call_counts[1] == 1
        for hint in proc._captured_hints:
            assert hint is None
