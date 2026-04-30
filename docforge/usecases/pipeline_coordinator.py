"""Parallel page processing coordinator.

Owns the ``ThreadPoolExecutor`` lifecycle and the per-page exception
handling that surfaces page failures as ``PageError`` records instead of
silently dropping pages from the output.
"""

from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.domain.models import NoiseStats, PageError
from docforge.usecases.page_processor import PageProcessor, PageResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


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
    ) -> tuple[list[PageResult], list[PageError]]:
        """Process all pages and return ``(ordered_results, page_errors)``.

        Page failures are converted to ``PageError`` entries — the
        corresponding ``PageResult`` slot carries empty content so
        downstream aggregation is unchanged.
        """
        raw_results: list[tuple[int, PageResult]] = []
        page_errors: list[PageError] = []

        def _submit(page_idx: int) -> PageResult:
            return self._page_processor.process(
                page_idx=page_idx,
                pdf_path=pdf_path,
                ocr_semaphore=ocr_semaphore,
                log_fn=log_fn,
                total_pages=total_pages,
            )

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(_submit, page_idx): page_idx
                for page_idx in range(total_pages)
            }
            for future in as_completed(futures):
                page_idx = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 — page-level boundary
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
