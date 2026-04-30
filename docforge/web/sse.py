"""SSE (Server-Sent Events) helpers for real-time progress streaming."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Generator, Optional

from docforge.web.task_state import TaskRegistry, registry as _default_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Progress event constants
# ---------------------------------------------------------------------------

EVT_PROFILING = "profiling"
EVT_NOISE_LEARNING = "noise_learning"
EVT_PAGE = "page_progress"
EVT_TABLE_MERGING = "table_merging"
EVT_ASSEMBLING = "assembling"
EVT_DONE = "done"
EVT_ERROR = "error"
EVT_HEARTBEAT = "heartbeat"
EVT_PAGE_RESULT = "page_result"

# 단계별 진행률(%) 기준값
_STAGE_PCT: dict[str, int] = {
    EVT_PROFILING: 5,
    EVT_NOISE_LEARNING: 15,
    EVT_TABLE_MERGING: 85,
    EVT_ASSEMBLING: 95,
    EVT_DONE: 100,
}


class ProgressTracker:
    """Thread-safe progress tracker that drives an SSE stream.

    When created with a ``task_id`` and ``registry``, every ``push`` call
    also accumulates the event into the registry's ``TaskState`` so that
    a disconnected client can later catch up via REST.
    """

    def __init__(
        self,
        task_id: Optional[str] = None,
        registry: Optional[TaskRegistry] = None,
    ) -> None:
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._done = threading.Event()
        self.task_id = task_id
        self._registry = registry if registry is not None else _default_registry
        self._persistence_warned = False  # log first persistence failure only

    # ------------------------------------------------------------------
    # Producer API (called from background thread)
    # ------------------------------------------------------------------

    def push(self, event: str, data: dict) -> None:
        """Enqueue a named event with its payload.

        If a ``task_id`` is bound, the event is also persisted into the
        ``TaskRegistry`` for later catch-up.
        """
        self._queue.put({"event": event, "data": data})
        if self.task_id is not None:
            try:
                self._registry.apply_event(self.task_id, event, data)
            except Exception:
                # Persistence must never break the live stream — but log
                # the first failure so the issue is at least visible.
                if not self._persistence_warned:
                    logger.warning(
                        "TaskRegistry.apply_event failed for task=%s event=%s",
                        self.task_id, event, exc_info=True,
                    )
                    self._persistence_warned = True

    def push_stage(self, event: str, message: str) -> None:
        """Convenience wrapper for well-known pipeline stages."""
        pct = _STAGE_PCT.get(event, 0)
        self.push(event, {"message": message, "pct": pct})

    def push_page(self, page_num: int, total_pages: int, message: str = "") -> None:
        """Emit a page-level progress event with calculated percentage."""
        if total_pages > 0:
            # Pages occupy 15% – 85% of the progress range
            page_range = 85 - 15
            pct = 15 + int((page_num / total_pages) * page_range)
        else:
            pct = 50
        self.push(
            EVT_PAGE,
            {
                "page": page_num,
                "total": total_pages,
                "pct": pct,
                "message": message or f"{page_num}/{total_pages} 페이지 처리 중",
            },
        )

    def push_page_result(self, page_num: int, total_pages: int, markdown: str) -> None:
        """Emit a page result event with the page's markdown content."""
        self.push(
            EVT_PAGE_RESULT,
            {
                "page": page_num,
                "total": total_pages,
                "markdown": markdown,
            },
        )

    def push_error(self, message: str) -> None:
        """Emit an error event and signal the stream to terminate."""
        self.push(EVT_ERROR, {"message": message, "pct": 0})
        self.mark_done()

    def mark_done(self) -> None:
        """Signal that the producer has finished; terminates the stream."""
        self._done.set()
        self._queue.put(None)  # sentinel value

    # ------------------------------------------------------------------
    # Consumer API (called from Flask route / SSE generator)
    # ------------------------------------------------------------------

    def stream(self) -> Generator[str, None, None]:
        """Yield SSE-formatted strings until the producer signals done."""
        while not self._done.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                yield f"data: {json.dumps({'event': EVT_HEARTBEAT})}\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"


# ---------------------------------------------------------------------------
# Progress callback → SSE mapping
# ---------------------------------------------------------------------------


def progress_line_to_sse(tracker: ProgressTracker, line: str) -> None:
    """Convert a parse_pdf progress line into an SSE event on the tracker."""
    line = line.strip()
    if not line:
        return

    if "[1/6]" in line:
        tracker.push_stage(EVT_PROFILING, line)
    elif "[2/6]" in line or "[3/6]" in line:
        tracker.push_stage(EVT_NOISE_LEARNING, line)
    elif line.startswith("[page]"):
        parts = line[len("[page]"):].strip().split("/")
        if len(parts) == 2:
            try:
                page_num = int(parts[0])
                total = int(parts[1])
                tracker.push_page(page_num, total)
            except ValueError:
                tracker.push(EVT_PAGE, {"message": line, "pct": None})
    elif "[4/6]" in line:
        tracker.push_stage(EVT_PAGE, line)
    elif "[5/6]" in line:
        tracker.push_stage(EVT_TABLE_MERGING, line)
    elif "[6/6]" in line:
        tracker.push_stage(EVT_ASSEMBLING, line)
    elif "Done!" in line or "pages parsed" in line:
        tracker.push_stage(EVT_ASSEMBLING, line)
    else:
        tracker.push("progress", {"message": line, "pct": None})
