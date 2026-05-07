"""Tests for the new persistence/resume REST endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docforge.web.app import create_app
from docforge.web.task_state import registry as task_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset module-level registry between tests."""
    # Snapshot existing keys, restore on teardown by removing what we add.
    before = set(task_registry._store.keys())
    yield
    after = set(task_registry._store.keys())
    for key in after - before:
        task_registry.remove(key)


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(upload_dir=tmp_path / "uploads")
    app.config["TESTING"] = True
    return app.test_client()


class TestActiveEndpoint:
    def test_returns_empty_when_no_tasks(self, client) -> None:
        resp = client.get("/api/parse/active")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["success"] is True
        assert body["data"] == []

    def test_lists_only_queued_and_running(self, client) -> None:
        task_registry.create("a", "a.pdf")
        task_registry.create("b", "b.pdf")
        task_registry.apply_event("b", "page_progress", {"completed_pages": 1, "total_pages": 10, "pct": 20})
        task_registry.create("c", "c.pdf")
        task_registry.apply_event("c", "done", {})

        resp = client.get("/api/parse/active")
        body = json.loads(resp.data)
        ids = {item["task_id"] for item in body["data"]}
        assert ids == {"a", "b"}


class TestStateEndpoint:
    def test_404_when_unknown(self, client) -> None:
        resp = client.get("/api/parse/missing/state")
        assert resp.status_code == 404

    def test_returns_completed_page_numbers(self, client) -> None:
        task_registry.create("t1", "doc.pdf")
        task_registry.apply_event("t1", "page_result", {"page_num": 3, "total_pages": 5, "markdown": "x"})
        task_registry.apply_event("t1", "page_result", {"page_num": 1, "total_pages": 5, "markdown": "y"})

        resp = client.get("/api/parse/t1/state")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["data"]["completed_page_numbers"] == [1, 3]
        # Page markdowns are NOT included to keep payload small.
        assert "page_markdowns" not in body["data"]


class TestPagesEndpoint:
    def test_404_when_unknown(self, client) -> None:
        resp = client.get("/api/parse/missing/pages")
        assert resp.status_code == 404

    def test_returns_sorted_completed(self, client) -> None:
        task_registry.create("t1", "doc.pdf")
        task_registry.apply_event("t1", "page_result", {"page_num": 5, "total_pages": 10, "markdown": "x"})
        task_registry.apply_event("t1", "page_result", {"page_num": 2, "total_pages": 10, "markdown": "y"})

        resp = client.get("/api/parse/t1/pages")
        body = json.loads(resp.data)
        assert body["data"]["completed"] == [2, 5]
        assert body["data"]["count"] == 2
        assert body["data"]["total_pages"] == 10


class TestPageEndpoint:
    def test_404_when_task_unknown(self, client) -> None:
        resp = client.get("/api/parse/missing/page/1")
        assert resp.status_code == 404

    def test_404_when_page_not_ready(self, client) -> None:
        task_registry.create("t1", "doc.pdf")
        resp = client.get("/api/parse/t1/page/3")
        assert resp.status_code == 404
        body = json.loads(resp.data)
        assert body["error"]["code"] == "PAGE_NOT_READY"

    def test_returns_markdown(self, client) -> None:
        task_registry.create("t1", "doc.pdf")
        task_registry.apply_event("t1", "page_result", {"page_num": 7, "total_pages": 10, "markdown": "## hello"})

        resp = client.get("/api/parse/t1/page/7")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["data"] == {"page": 7, "markdown": "## hello"}


class TestSseCatchupReplay:
    def test_catchup_event_emitted_for_known_task(self, client) -> None:
        task_registry.create("t1", "doc.pdf")
        task_registry.apply_event("t1", "page_result", {"page_num": 1, "total_pages": 3, "markdown": "x"})
        task_registry.apply_event("t1", "done", {})

        resp = client.get("/api/parse/t1/status")
        # SSE returns a streaming response. Read body fully.
        body = resp.get_data(as_text=True)
        assert "event: catchup" in body
        # The catchup payload includes completed_page_numbers
        assert "completed_page_numbers" in body

    def test_404_when_truly_unknown(self, client) -> None:
        resp = client.get("/api/parse/never-existed/status")
        assert resp.status_code == 404
