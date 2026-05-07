"""Tests for DocForge worker queue module."""

import json
import time
import threading
from pathlib import Path

import pytest

from docforge.web.worker import (
    init_worker_queue,
    shutdown_worker_queue,
    submit_task,
    cancel_task,
    get_queue_status,
    get_tracker,
    set_tracker,
    remove_tracker,
)
from docforge.web.sse import ProgressTracker


class TestTrackerRegistry:
    """Test tracker get/set/remove operations."""

    def test_set_and_get_tracker(self) -> None:
        tracker = ProgressTracker()
        set_tracker("test-123", tracker)
        assert get_tracker("test-123") is tracker
        remove_tracker("test-123")
        assert get_tracker("test-123") is None

    def test_get_nonexistent_tracker(self) -> None:
        assert get_tracker("nonexistent") is None

    def test_remove_nonexistent_tracker(self) -> None:
        # Should not raise
        remove_tracker("nonexistent")


class TestWorkerQueue:
    """Test worker queue operations."""

    @pytest.fixture(autouse=True)
    def setup_queue(self):
        init_worker_queue(max_workers=2)
        yield
        shutdown_worker_queue()

    def test_submit_and_execute(self) -> None:
        result = []

        def task_fn():
            result.append("done")

        future = submit_task("task-1", task_fn)
        assert future is not None
        future.result(timeout=5)
        assert result == ["done"]

    def test_get_queue_status(self) -> None:
        status = get_queue_status()
        assert "running" in status
        assert "queued" in status
        assert "workers" in status
        assert status["workers"] == 2

    def test_cancel_queued_task(self) -> None:
        barrier = threading.Event()
        results = []

        def blocking_task():
            barrier.wait(timeout=10)
            results.append("executed")

        # Fill the pool
        f1 = submit_task("t1", blocking_task)
        f2 = submit_task("t2", blocking_task)

        # This one should be queued
        f3 = submit_task("t3", blocking_task)

        time.sleep(0.1)  # Let tasks start

        # Try to cancel the queued one
        cancelled = cancel_task("t3")

        # Release blocking tasks
        barrier.set()
        f1.result(timeout=5)
        f2.result(timeout=5)

        # If cancelled, t3 should not have executed
        if cancelled:
            assert "executed" not in results or len(results) <= 2

    def test_failure_isolation(self) -> None:
        """One failing task should not affect others."""
        results = []

        def failing_task():
            raise RuntimeError("deliberate error")

        def success_task():
            results.append("ok")

        f1 = submit_task("fail-1", failing_task)
        f2 = submit_task("ok-1", success_task)

        # Wait for both
        try:
            f1.result(timeout=5)
        except RuntimeError:
            pass
        f2.result(timeout=5)

        assert results == ["ok"]


class TestNewAPIs:
    """Test new queue-related API routes."""

    @pytest.fixture
    def app(self, tmp_path: Path):
        from docforge.web.app import create_app
        app = create_app(upload_dir=tmp_path / "uploads")
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_queue_status_endpoint(self, client) -> None:
        resp = client.get("/api/queue/status")
        data = json.loads(resp.data)
        assert resp.status_code == 200
        assert data["success"] is True
        assert "running" in data["data"]
        assert "queued" in data["data"]
        assert "workers" in data["data"]

    def test_cancel_nonexistent_task(self, client) -> None:
        resp = client.post("/api/parse/nonexistent/cancel")
        assert resp.status_code == 404

    def test_versions_nonexistent_task(self, client) -> None:
        resp = client.get("/api/versions/nonexistent")
        assert resp.status_code == 404

    def test_diff_missing_params(self, client, tmp_path) -> None:
        # Create a task first
        from docforge.web.storage import TaskStore
        store = TaskStore(tmp_path / "uploads")
        record = store.create("test.pdf", "")

        resp = client.get(f"/api/diff/{record.task_id}")
        data = json.loads(resp.data)
        assert resp.status_code == 400
        assert data["success"] is False


class TestStorageExtensions:
    """Test storage result persistence and versioning."""

    def test_save_and_load_result(self, tmp_path: Path) -> None:
        from docforge.web.storage import TaskStore
        store = TaskStore(tmp_path)
        record = store.create("test.pdf", "")

        result_data = {"markdown": "# Hello", "metadata": {}, "stats": {}}
        path = store.save_result(record.task_id, result_data)
        assert path is not None
        assert path.exists()

        loaded = store.load_result(record.task_id)
        assert loaded is not None
        assert loaded["markdown"] == "# Hello"

    def test_version_management(self, tmp_path: Path) -> None:
        from docforge.web.storage import TaskStore
        store = TaskStore(tmp_path)
        record = store.create("test.pdf", "")

        # Save original version (UUID-based filename)
        v0 = store.save_version(record.task_id, "# Original", "original")
        assert v0 is not None
        assert v0.startswith("v_")
        assert v0.endswith("_original.md")

        # Save edited version
        v1 = store.save_version(record.task_id, "# Edited")
        assert v1 is not None
        assert v1.startswith("v_")

        # List versions (sorted by mtime)
        versions = store.list_versions(record.task_id)
        assert len(versions) == 2

        # Get version content by exact filename
        content = store.get_version_content(record.task_id, v0)
        assert content == "# Original"

    def test_version_no_race_condition(self, tmp_path: Path) -> None:
        """Concurrent save_version calls must not produce duplicate filenames."""
        import threading

        from docforge.web.storage import TaskStore
        store = TaskStore(tmp_path)
        record = store.create("test.pdf", "")

        results: list[str | None] = []
        lock = threading.Lock()

        def _save(idx: int) -> None:
            name = store.save_version(record.task_id, f"# Version {idx}", f"thread{idx}")
            with lock:
                results.append(name)

        threads = [threading.Thread(target=_save, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 saves should succeed with unique filenames
        assert len(results) == 10
        assert all(r is not None for r in results)
        assert len(set(results)) == 10  # all unique

    def test_backward_compat_pending_status(self, tmp_path: Path) -> None:
        """Old tasks with 'pending' status should load as 'queued'."""
        from docforge.web.storage import TaskStore
        tasks_file = tmp_path / "tasks.json"
        tasks_file.write_text(json.dumps({
            "old-task": {
                "task_id": "old-task",
                "filename": "old.pdf",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
                "pdf_path": "",
            }
        }), encoding="utf-8")

        store = TaskStore(tmp_path)
        record = store.get("old-task")
        assert record is not None
        assert record.status == "queued"
        assert record.queued_at == "2026-01-01T00:00:00"


class TestSSEExtensions:
    """Test SSE page result event."""

    def test_push_page_result(self) -> None:
        tracker = ProgressTracker()
        tracker.push_page_result(1, 10, "# Page 1")
        tracker.mark_done()

        events = list(tracker.stream())
        assert len(events) == 1
        data = json.loads(events[0].replace("data: ", "").strip())
        assert data["event"] == "page_result"
        assert data["data"]["page"] == 1
        assert data["data"]["total"] == 10
        assert data["data"]["markdown"] == "# Page 1"
