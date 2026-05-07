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
