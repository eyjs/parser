"""Tests for DocForge web GUI."""

import json
import tempfile
from pathlib import Path

import pytest

from docforge.web.app import create_app
from docforge.web.storage import TaskStore, TaskRecord
from docforge.web.sse import ProgressTracker, EVT_DONE


class TestFlaskApp:
    """Test Flask application factory and routes."""

    @pytest.fixture
    def app(self, tmp_path: Path):
        app = create_app(upload_dir=tmp_path / "uploads")
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_dashboard_returns_200(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_verify_returns_200(self, client) -> None:
        resp = client.get("/verify/test-id")
        assert resp.status_code == 200

    def test_editor_redirects_to_verify(self, client) -> None:
        resp = client.get("/edit/test-id")
        assert resp.status_code == 302
        assert "/verify/test-id" in resp.headers["Location"]

    def test_api_parse_no_file_returns_400(self, client) -> None:
        resp = client.post("/api/parse")
        data = json.loads(resp.data)
        assert resp.status_code == 400
        assert data["success"] is False

    def test_api_history_returns_empty(self, client) -> None:
        resp = client.get("/api/history")
        data = json.loads(resp.data)
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["data"] == []

    def test_api_result_not_found(self, client) -> None:
        resp = client.get("/api/parse/nonexistent/result")
        assert resp.status_code == 404

    def test_api_export_not_found(self, client) -> None:
        resp = client.get("/api/export/nonexistent")
        assert resp.status_code == 404


class TestTaskStore:
    """Test task storage."""

    def test_create_and_get(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        record = store.create("test.pdf", "/path/test.pdf")
        assert record.filename == "test.pdf"
        assert record.status == "queued"

        fetched = store.get(record.task_id)
        assert fetched is not None
        assert fetched.filename == "test.pdf"

    def test_update(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        record = store.create("test.pdf", "")
        store.update(record.task_id, status="done", progress_pct=100)

        fetched = store.get(record.task_id)
        assert fetched is not None
        assert fetched.status == "done"
        assert fetched.progress_pct == 100

    def test_list_all_sorted(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        store.create("first.pdf", "")
        store.create("second.pdf", "")

        records = store.list_all()
        assert len(records) == 2
        assert records[0].filename == "second.pdf"

    def test_delete(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        record = store.create("test.pdf", "")
        assert store.delete(record.task_id) is True
        assert store.get(record.task_id) is None
        assert store.delete("nonexistent") is False

    def test_persistence(self, tmp_path: Path) -> None:
        store1 = TaskStore(tmp_path)
        record = store1.create("test.pdf", "/path")
        task_id = record.task_id

        store2 = TaskStore(tmp_path)
        fetched = store2.get(task_id)
        assert fetched is not None
        assert fetched.filename == "test.pdf"


class TestProgressTracker:
    """Test SSE progress tracker."""

    def test_push_and_stream(self) -> None:
        tracker = ProgressTracker()
        tracker.push("test", {"msg": "hello"})
        tracker.mark_done()

        events = list(tracker.stream())
        assert len(events) == 1
        lines = events[0].strip().split("\n")
        assert lines[0] == "event: test"
        data = json.loads(lines[1].removeprefix("data: "))
        assert data["msg"] == "hello"

    def test_push_stage(self) -> None:
        tracker = ProgressTracker()
        tracker.push_stage(EVT_DONE, "완료!")
        tracker.mark_done()

        events = list(tracker.stream())
        assert len(events) == 1
        lines = events[0].strip().split("\n")
        assert lines[0] == "event: done"
        data = json.loads(lines[1].removeprefix("data: "))
        assert data["pct"] == 100
