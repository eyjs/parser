"""Parallel page processing coordinator.

Owns the ``ThreadPoolExecutor`` lifecycle and the per-page exception
handling that surfaces page failures as ``PageError`` records instead of
silently dropping pages from the output.

The module maintains a **global** executor singleton so that repeated
``PipelineCoordinator.run()`` invocations (one per parsed document) do
not create a fresh pool each time, preventing thread explosion under
concurrent uploads.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.domain.models import NoiseStats, PageError
from docforge.domain.value_objects import DocumentStrategyReport
from docforge.usecases.page_processor import PageProcessor, PageResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global executor singleton
# ---------------------------------------------------------------------------

_global_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _default_max_workers() -> int:
    """Compute the default worker count: min(cpu_count * 2, 32), at least 4."""
    cpu = os.cpu_count() or 2
    return max(4, min(cpu * 2, 32))


def get_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """Return (or lazily create) the module-level executor singleton.

    The first call determines ``max_workers``; subsequent calls return the
    same instance regardless of the ``max_workers`` argument.
    """
    global _global_executor
    if _global_executor is not None:
        return _global_executor
    with _executor_lock:
        # Double-checked locking
        if _global_executor is not None:
            return _global_executor
        workers = max_workers if max_workers and max_workers > 0 else _default_max_workers()
        _global_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="pipeline-coord",
        )
        logger.info("Global PipelineCoordinator executor created with %d workers", workers)
        return _global_executor


def shutdown_executor() -> None:
    """Shut down the global executor (if any). Safe to call multiple times."""
    global _global_executor
    with _executor_lock:
        if _global_executor is not None:
            _global_executor.shutdown(wait=False)
            _global_executor = None
            logger.info("Global PipelineCoordinator executor shut down")


atexit.register(shutdown_executor)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class PipelineCoordinator:
    """Coordinates parallel page processing and accumulates failures."""

    def __init__(self, page_processor: PageProcessor, max_workers: int) -> None:
        self._page_processor = page_processor
        self._max_workers = max(1, max_workers)

    def run(
        self,
        pdf_path: Path,
        total_pages: int,
        ocr_semaphore: threading.Semaphore,
        log_fn: "Callable[[str], None]",
        on_page_complete: "Callable[[PageResult], None] | None" = None,
        strategy_report: DocumentStrategyReport | None = None,
    ) -> tuple[list[PageResult], list[PageError]]:
        """Process all pages and return ``(ordered_results, page_errors)``.

        When ``strategy_report`` is provided, each page receives its
        corresponding :class:`PageStrategy` for block-level adaptive
        retry. When ``None``, falls back to legacy behaviour.

        Page failures are converted to ``PageError`` entries -- the
        corresponding ``PageResult`` slot carries empty content so
        downstream aggregation is unchanged.
        """
        raw_results: list[tuple[int, PageResult]] = []
        page_errors: list[PageError] = []

        def _submit(page_idx: int) -> PageResult:
            page_strategy = None
            if strategy_report and page_idx < len(strategy_report.pages):
                page_strategy = strategy_report.pages[page_idx]
            return self._page_processor.process(
                page_idx=page_idx,
                pdf_path=pdf_path,
                ocr_semaphore=ocr_semaphore,
                log_fn=log_fn,
                total_pages=total_pages,
                page_strategy=page_strategy,
            )

        executor = get_executor(self._max_workers)
        futures = {
            executor.submit(_submit, page_idx): page_idx
            for page_idx in range(total_pages)
        }
        for future in as_completed(futures):
            page_idx = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 -- page-level boundary
                tb_str = traceback.format_exc()
                logger.error(
                    "Page %d processing failed, page will be missing from output",
                    page_idx + 1,
                    exc_info=True,
                )
                page_errors.append(PageError(
                    page_number=page_idx + 1,
                    error_type=type(exc).__name__,
                    message=str(exc) or repr(exc),
                    traceback=tb_str,
                ))
                result = PageResult(
                    page_content=None, tables_info=None,
                    noise=NoiseStats(), is_toc=False, ocr_used=False,
                )
            raw_results.append((page_idx, result))
            if on_page_complete is not None:
                try:
                    on_page_complete(result)
                except Exception:  # pragma: no cover - listener failures
                    logger.warning("on_page_complete callback failed", exc_info=True)

        raw_results.sort(key=lambda x: x[0])
        return [r for _, r in raw_results], page_errors
