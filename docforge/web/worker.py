"""ThreadPoolExecutor-based worker queue for DocForge PDF parsing tasks."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from docforge.web.sse import ProgressTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tracker registry (moved from routes.py)
# ---------------------------------------------------------------------------

_TRACKERS: dict[str, ProgressTracker] = {}
_TRACKER_LOCK = threading.Lock()


def get_tracker(task_id: str) -> ProgressTracker | None:
    """Return the ProgressTracker for a task, or None."""
    with _TRACKER_LOCK:
        return _TRACKERS.get(task_id)


def set_tracker(task_id: str, tracker: ProgressTracker) -> None:
    """Register a ProgressTracker for a task."""
    with _TRACKER_LOCK:
        _TRACKERS[task_id] = tracker


def remove_tracker(task_id: str) -> None:
    """Unregister a ProgressTracker for a task."""
    with _TRACKER_LOCK:
        _TRACKERS.pop(task_id, None)


# ---------------------------------------------------------------------------
# Worker queue
# ---------------------------------------------------------------------------

_executor: ThreadPoolExecutor | None = None
_futures: dict[str, Future] = {}
_futures_lock = threading.Lock()


def init_worker_queue(max_workers: int | None = None) -> None:
    """Initialize the worker pool. Call once at app startup."""
    global _executor
    if _executor is not None:
        return

    if max_workers is None:
        max_workers = min(4, os.cpu_count() or 2)

    _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="docforge-worker")
    logger.info("Worker queue initialized with %d workers", max_workers)


def shutdown_worker_queue() -> None:
    """Shutdown the worker pool gracefully. Call at app teardown."""
    global _executor
    if _executor is None:
        return
    _executor.shutdown(wait=False)
    _executor = None
    logger.info("Worker queue shut down")


def submit_task(task_id: str, fn: Callable, *args, **kwargs) -> Future | None:
    """Submit a task to the worker queue. Returns the Future."""
    if _executor is None:
        logger.error("Worker queue not initialized")
        return None

    future = _executor.submit(fn, *args, **kwargs)
    with _futures_lock:
        _futures[task_id] = future

    def _on_done(f: Future) -> None:
        with _futures_lock:
            _futures.pop(task_id, None)

    future.add_done_callback(_on_done)
    return future


def cancel_task(task_id: str) -> bool:
    """Attempt to cancel a queued task. Returns True if cancelled, False if running/done."""
    with _futures_lock:
        future = _futures.get(task_id)
    if future is None:
        return False
    return future.cancel()


def get_queue_status() -> dict:
    """Return current queue status: running, queued, workers."""
    with _futures_lock:
        futures_copy = list(_futures.values())

    running = sum(1 for f in futures_copy if f.running())
    queued = sum(1 for f in futures_copy if not f.done() and not f.running())
    max_workers = _executor._max_workers if _executor else 0

    return {
        "running": running,
        "queued": queued,
        "workers": max_workers,
    }
