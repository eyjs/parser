"""Unit tests for ``docforge.web.task_state``."""

from __future__ import annotations

import threading

import pytest

from docforge.web.task_state import (
    TaskRegistry,
    TaskState,
    state_full,
    state_summary,
)


class TestTaskStateBasics:
    def test_create_returns_queued_state(self) -> None:
        reg = TaskRegistry()
        state = reg.create("t1", "doc.pdf")
        assert state.task_id == "t1"
        assert state.filename == "doc.pdf"
        assert state.status == "queued"
        assert state.completed_pages == 0
        assert state.page_markdowns == {}

    def test_create_is_idempotent(self) -> None:
        reg = TaskRegistry()
        a = reg.create("t1", "doc.pdf")
        b = reg.create("t1", "ignored.pdf")
        assert a is b
        assert b.filename == "doc.pdf"  # original wins

    def test_get_missing_returns_none(self) -> None:
        reg = TaskRegistry()
        assert reg.get("nope") is None

    def test_remove(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.remove("t1")
        assert reg.get("t1") is None


class TestApplyEvent:
    def test_page_progress_advances_state(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "page_progress", {"completed_pages": 3, "total_pages": 10, "pct": 35})
        s = reg.get("t1")
        assert s.completed_pages == 3
        assert s.total_pages == 10
        assert s.pct == 35
        assert s.status == "running"
        assert s.current_stage == "page_progress"

    def test_page_progress_uses_max_for_idempotency(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "page_progress", {"completed_pages": 5, "total_pages": 10, "pct": 50})
        reg.apply_event("t1", "page_progress", {"completed_pages": 3, "total_pages": 10, "pct": 30})
        s = reg.get("t1")
        # Out-of-order events must not regress progress
        assert s.completed_pages == 5

    def test_page_result_stores_markdown(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "page_result", {"page_num": 2, "total_pages": 10, "markdown": "# hi"})
        s = reg.get("t1")
        assert s.page_markdowns[2] == "# hi"
        assert s.total_pages == 10

    def test_stage_events_set_current_stage(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "profiling", {"pct": 5})
        assert reg.get("t1").current_stage == "profiling"
        reg.apply_event("t1", "noise_learning", {"pct": 15})
        assert reg.get("t1").current_stage == "noise_learning"

    def test_done_event_finalizes(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "done", {"pct": 100})
        s = reg.get("t1")
        assert s.status == "done"
        assert s.pct == 100
        assert s.finished_at is not None

    def test_error_event_records_message(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "error", {"message": "boom"})
        s = reg.get("t1")
        assert s.status == "error"
        assert s.error_message == "boom"
        assert s.finished_at is not None

    def test_apply_event_on_missing_task_is_noop(self) -> None:
        reg = TaskRegistry()
        # Should not raise
        reg.apply_event("missing", "page_progress", {"completed_pages": 1, "total_pages": 1})


class TestListings:
    def test_list_active_excludes_done_and_error(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "a.pdf")
        reg.create("t2", "b.pdf")
        reg.create("t3", "c.pdf")
        reg.apply_event("t2", "done", {})
        reg.apply_event("t3", "error", {"message": "x"})
        active = reg.list_active()
        ids = {s.task_id for s in active}
        assert ids == {"t1"}

    def test_list_all_includes_everything(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "a.pdf")
        reg.create("t2", "b.pdf")
        reg.apply_event("t2", "done", {})
        assert {s.task_id for s in reg.list_all()} == {"t1", "t2"}

    def test_list_active_sorted_newest_first(self) -> None:
        reg = TaskRegistry()
        s1 = reg.create("t1", "a.pdf")
        s2 = reg.create("t2", "b.pdf")
        # Force ordering — s2 is newer
        assert s2.started_at >= s1.started_at
        ids = [s.task_id for s in reg.list_active()]
        assert ids[0] == "t2"


class TestConcurrency:
    def test_concurrent_apply_event_no_lost_updates(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")

        def worker(start: int) -> None:
            for i in range(start, start + 50):
                reg.apply_event(
                    "t1", "page_result", {"page_num": i, "total_pages": 200, "markdown": f"p{i}"}
                )

        threads = [threading.Thread(target=worker, args=(i * 50 + 1,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        s = reg.get("t1")
        assert len(s.page_markdowns) == 200
        # Spot-check
        assert s.page_markdowns[1] == "p1"
        assert s.page_markdowns[200] == "p200"

    def test_concurrent_create_idempotent(self) -> None:
        reg = TaskRegistry()
        results: list[TaskState] = []
        lock = threading.Lock()

        def worker() -> None:
            s = reg.create("t1", "doc.pdf")
            with lock:
                results.append(s)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads got the same instance
        assert all(r is results[0] for r in results)


class TestSerialization:
    def test_state_summary_excludes_markdown(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "page_result", {"page_num": 1, "total_pages": 5, "markdown": "x"})
        d = state_summary(reg.get("t1"))
        assert "page_markdowns" not in d
        assert d["task_id"] == "t1"
        assert d["filename"] == "doc.pdf"
        assert d["total_pages"] == 5

    def test_state_full_includes_completed_page_numbers(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        reg.apply_event("t1", "page_result", {"page_num": 3, "total_pages": 5, "markdown": "x"})
        reg.apply_event("t1", "page_result", {"page_num": 1, "total_pages": 5, "markdown": "y"})
        d = state_full(reg.get("t1"))
        assert d["completed_page_numbers"] == [1, 3]
        # No markdown in summary
        assert "page_markdowns" not in d


class TestSnapshotConcurrency:
    """Stress: concurrent apply_event + snapshot_full must not raise.

    Pre-fix this test would intermittently raise
    ``RuntimeError: dictionary changed size during iteration`` because
    ``state_full`` iterated ``page_markdowns.keys()`` outside the lock.
    """

    def test_snapshot_full_safe_under_concurrent_writes(self) -> None:
        reg = TaskRegistry()
        reg.create("t-race", "x.pdf")
        stop = threading.Event()
        errors: list[Exception] = []

        def writer() -> None:
            i = 0
            while not stop.is_set():
                try:
                    reg.apply_event(
                        "t-race",
                        "page_result",
                        {"page_num": i, "markdown": "x" * 64},
                    )
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)
                i += 1

        def reader() -> None:
            for _ in range(2000):
                try:
                    reg.snapshot_full("t-race")
                    reg.snapshot_completed_pages("t-race")
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

        wt = threading.Thread(target=writer, daemon=True)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        rt.join(timeout=10)
        stop.set()
        wt.join(timeout=2)

        assert not errors, f"race produced errors: {errors[:3]}"
