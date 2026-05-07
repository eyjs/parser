"""In-memory task state registry for background parsing jobs.

This module provides ``TaskState`` (per-task accumulating snapshot) and
``TaskRegistry`` (thread-safe singleton store) so that the web layer can
answer "what's the current state of task X?" even after an SSE consumer
has disconnected (e.g. on page refresh).

Design notes:
- Pure standard library (no external dependencies).
- All mutating operations on the registry hold ``_lock`` for the duration
  of the read-modify-write so concurrent producers cannot tear state.
- ``TaskState`` is mutable by design — the registry guards it with a lock
  rather than relying on copy-on-write. Snapshot getters return shallow
  copies of the relevant fields so callers cannot mutate live state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from docforge.web.events import (
    EVT_ASSEMBLING,
    EVT_NOISE_LEARNING,
    EVT_PROFILING,
    EVT_STRATEGY_REPORT,
    EVT_TABLE_MERGING,
)


TaskStatus = Literal["queued", "running", "done", "error", "cancelled"]


@dataclass
class TaskState:
    """Mutable accumulating state for a single parsing task."""

    task_id: str
    filename: str
    status: TaskStatus = "queued"
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    total_pages: int = 0
    completed_pages: int = 0
    current_stage: str = ""
    pct: int = 0
    page_markdowns: dict[int, str] = field(default_factory=dict)
    error_message: Optional[str] = None
    last_event: Optional[dict] = None


# Events that signal pipeline stages (used by ``apply_event``).
_STAGE_EVENTS = {EVT_PROFILING, EVT_NOISE_LEARNING, EVT_TABLE_MERGING, EVT_ASSEMBLING, EVT_STRATEGY_REPORT}


class TaskRegistry:
    """Thread-safe in-memory store for ``TaskState`` instances.

    All mutating methods acquire ``_lock`` before reading and writing
    state so that concurrent producer threads cannot corrupt the store.
    Lookup methods (``get``, ``list_active``, ``list_all``) also take
    the lock to ensure they observe a consistent snapshot.
    """

    def __init__(self) -> None:
        self._store: dict[str, TaskState] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create(self, task_id: str, filename: str) -> TaskState:
        """Register a brand-new task and return its state.

        If a state for ``task_id`` already exists it is returned as-is
        (idempotent — useful when ``POST /api/parse`` is retried).
        """
        with self._lock:
            existing = self._store.get(task_id)
            if existing is not None:
                return existing
            state = TaskState(task_id=task_id, filename=filename)
            self._store[task_id] = state
            return state

    def get(self, task_id: str) -> Optional[TaskState]:
        with self._lock:
            return self._store.get(task_id)

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._store.pop(task_id, None)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def update(self, task_id: str, **fields) -> Optional[TaskState]:
        """Apply attribute updates to a task state under the lock."""
        with self._lock:
            state = self._store.get(task_id)
            if state is None:
                return None
            for key, value in fields.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            return state

    def apply_event(self, task_id: str, event: str, data: dict) -> None:
        """Translate an SSE event into accumulated state changes.

        Idempotent for ``page_progress`` (uses ``max``) so out-of-order
        delivery does not regress progress.
        """
        with self._lock:
            state = self._store.get(task_id)
            if state is None:
                return

            state.last_event = {"event": event, "data": dict(data)}

            if event == "page_progress":
                page = int(data.get("completed_pages") or 0)
                total = int(data.get("total_pages") or 0)
                if total > 0:
                    state.total_pages = max(state.total_pages, total)
                if page > 0:
                    state.completed_pages = max(state.completed_pages, page)
                pct = data.get("pct")
                if isinstance(pct, int):
                    state.pct = pct
                state.current_stage = "page_progress"
                if state.status == "queued":
                    state.status = "running"

            elif event == "page_result":
                page = data.get("page_num")
                markdown = data.get("markdown", "")
                if isinstance(page, int) and page > 0:
                    state.page_markdowns[page] = markdown
                total = int(data.get("total_pages") or 0)
                if total > 0:
                    state.total_pages = max(state.total_pages, total)

            elif event in _STAGE_EVENTS:
                state.current_stage = event
                pct = data.get("pct")
                if isinstance(pct, int):
                    state.pct = pct
                if state.status == "queued":
                    state.status = "running"

            elif event == "done":
                state.status = "done"
                state.finished_at = datetime.now()
                state.pct = 100
                state.current_stage = "done"

            elif event == "error":
                state.status = "error"
                state.error_message = str(data.get("message") or "")
                state.finished_at = datetime.now()

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def list_active(self) -> list[TaskState]:
        """Return queued/running tasks, newest first."""
        with self._lock:
            items = [s for s in self._store.values() if s.status in {"queued", "running"}]
        return sorted(items, key=lambda s: s.started_at, reverse=True)

    def list_all(self) -> list[TaskState]:
        with self._lock:
            return sorted(self._store.values(), key=lambda s: s.started_at, reverse=True)

    # ------------------------------------------------------------------
    # Lock-safe serialization (kills the page_markdowns race)
    # ------------------------------------------------------------------

    def snapshot_summary(self, task_id: str) -> Optional[dict]:
        """Return a summary dict, serialized inside the lock."""
        with self._lock:
            state = self._store.get(task_id)
            if state is None:
                return None
            return _serialize_summary(state)

    def snapshot_full(self, task_id: str) -> Optional[dict]:
        """Return the full snapshot (no markdowns), serialized inside the lock.

        Crucial: ``page_markdowns.keys()`` is iterated under the lock so a
        concurrent ``apply_event`` cannot trigger
        ``RuntimeError: dictionary changed size during iteration``.
        """
        with self._lock:
            state = self._store.get(task_id)
            if state is None:
                return None
            return _serialize_full(state)

    def snapshot_active(self) -> list[dict]:
        """Return summaries of queued/running tasks, newest first."""
        with self._lock:
            summaries = [
                _serialize_summary(s)
                for s in self._store.values()
                if s.status in {"queued", "running"}
            ]
        return sorted(summaries, key=lambda d: d["started_at"] or "", reverse=True)

    def snapshot_completed_pages(self, task_id: str) -> Optional[dict]:
        """Return ``{completed, count, total_pages}`` under the lock."""
        with self._lock:
            state = self._store.get(task_id)
            if state is None:
                return None
            completed = sorted(state.page_markdowns.keys())
            return {
                "completed": completed,
                "count": len(completed),
                "total_pages": state.total_pages,
            }

    def get_page_markdown(self, task_id: str, page_num: int) -> Optional[str]:
        """Return one page's markdown copy under the lock."""
        with self._lock:
            state = self._store.get(task_id)
            if state is None:
                return None
            return state.page_markdowns.get(page_num)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

registry = TaskRegistry()


# ---------------------------------------------------------------------------
# Serialization helpers (used by routes)
# ---------------------------------------------------------------------------


def _serialize_summary(state: TaskState) -> dict:
    """Internal serializer — caller must hold the registry lock."""
    return {
        "task_id": state.task_id,
        "filename": state.filename,
        "status": state.status,
        "pct": state.pct,
        "completed_pages": state.completed_pages,
        "total_pages": state.total_pages,
        "current_stage": state.current_stage,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "finished_at": state.finished_at.isoformat() if state.finished_at else None,
        "error_message": state.error_message,
    }


def _serialize_full(state: TaskState) -> dict:
    """Internal full serializer — caller must hold the registry lock.

    ``completed_page_numbers`` is built from ``state.page_markdowns.keys()``
    while the caller holds the lock so concurrent ``apply_event`` calls
    cannot mutate the dict mid-iteration.
    """
    out = _serialize_summary(state)
    out["completed_page_numbers"] = sorted(state.page_markdowns.keys())
    out["last_event"] = state.last_event
    return out


# Backward-compatible facades. These are NOT lock-safe on their own — prefer
# the registry's ``snapshot_*`` methods. Kept so existing imports/tests work.
def state_summary(state: TaskState) -> dict:
    return _serialize_summary(state)


def state_full(state: TaskState) -> dict:
    return _serialize_full(state)
