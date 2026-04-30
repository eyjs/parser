"""Verify ProgressTracker accumulates events into the TaskRegistry."""

from __future__ import annotations

from docforge.web.sse import ProgressTracker
from docforge.web.task_state import TaskRegistry


class TestTrackerRegistryIntegration:
    def test_push_records_page_progress(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        tracker = ProgressTracker(task_id="t1", registry=reg)

        tracker.push("page_progress", {"page": 4, "total": 10, "pct": 40})

        s = reg.get("t1")
        assert s.completed_pages == 4
        assert s.total_pages == 10
        assert s.pct == 40
        assert s.status == "running"

    def test_push_records_page_result_markdown(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        tracker = ProgressTracker(task_id="t1", registry=reg)

        tracker.push_page_result(2, 10, "# page 2")

        s = reg.get("t1")
        assert s.page_markdowns[2] == "# page 2"

    def test_push_done_marks_state_done(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        tracker = ProgressTracker(task_id="t1", registry=reg)

        tracker.push_stage("done", "완료")

        assert reg.get("t1").status == "done"
        assert reg.get("t1").pct == 100

    def test_push_error_marks_state_error(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        tracker = ProgressTracker(task_id="t1", registry=reg)

        tracker.push_error("boom")

        s = reg.get("t1")
        assert s.status == "error"
        assert s.error_message == "boom"

    def test_unbound_tracker_does_not_touch_registry(self) -> None:
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        tracker = ProgressTracker()  # no task_id

        tracker.push("page_progress", {"page": 1, "total": 1})
        # registry untouched (still default state)
        s = reg.get("t1")
        assert s.completed_pages == 0

    def test_stream_still_yields_events(self) -> None:
        # Make sure registry persistence didn't break the live SSE queue.
        reg = TaskRegistry()
        reg.create("t1", "doc.pdf")
        tracker = ProgressTracker(task_id="t1", registry=reg)

        tracker.push("profiling", {"pct": 5, "message": "start"})
        tracker.mark_done()

        chunks = list(tracker.stream())
        assert any("profiling" in c for c in chunks)
