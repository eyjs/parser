"""Tests for SSE heartbeat and idle-disconnect behaviour."""

from __future__ import annotations

import json
import queue
import threading
import time
from unittest.mock import patch

import pytest

from docforge.web.sse import ProgressTracker, _MAX_IDLE_HEARTBEATS


def _parse_named_event(chunk: str) -> tuple[str, dict] | None:
    """Parse a named SSE event chunk into (event_type, payload)."""
    lines = chunk.strip().split("\n")
    event_type = ""
    data_line = ""
    for line in lines:
        if line.startswith("event: "):
            event_type = line[len("event: "):]
        elif line.startswith("data: "):
            data_line = line[len("data: "):]
    if event_type and data_line:
        return event_type, json.loads(data_line)
    return None


class TestHeartbeat:
    """Verify heartbeat emission on idle SSE connections."""

    def test_heartbeat_emitted_on_idle(self) -> None:
        """When no events arrive within the queue timeout, a heartbeat comment is emitted."""
        tracker = ProgressTracker()

        def _delayed_done():
            time.sleep(0.2)
            tracker.mark_done()

        t = threading.Thread(target=_delayed_done, daemon=True)
        t.start()

        original_get = tracker._queue.get

        def _short_timeout_get(timeout=30):
            return original_get(timeout=0.05)

        with patch.object(tracker._queue, "get", side_effect=_short_timeout_get):
            chunks = list(tracker.stream())

        t.join(timeout=5)

        heartbeats = [c for c in chunks if c.startswith(": heartbeat")]
        assert len(heartbeats) >= 1

    def test_heartbeat_is_sse_comment_format(self) -> None:
        """Heartbeat must use SSE comment format ': heartbeat\\n\\n'."""
        tracker = ProgressTracker()

        call_count = [0]

        def _empty_then_done(timeout=30):
            call_count[0] += 1
            if call_count[0] == 1:
                raise queue.Empty
            tracker._done.set()
            return None

        with patch.object(tracker._queue, "get", side_effect=_empty_then_done):
            chunks = list(tracker.stream())

        assert len(chunks) == 1
        assert chunks[0] == ": heartbeat\n\n"

    def test_normal_events_use_named_sse_format(self) -> None:
        """Normal events should be yielded as named SSE events."""
        tracker = ProgressTracker()
        tracker.push("profiling", {"pct": 5, "message": "start"})
        tracker.push("done", {"pct": 100, "message": "finished"})
        tracker.mark_done()

        chunks = list(tracker.stream())
        named_chunks = [c for c in chunks if c.startswith("event:")]
        assert len(named_chunks) == 2

        parsed = _parse_named_event(named_chunks[0])
        assert parsed is not None
        assert parsed[0] == "profiling"
        assert parsed[1]["pct"] == 5

    def test_no_heartbeat_when_events_flow(self) -> None:
        """When events arrive promptly, no heartbeat comments are emitted."""
        tracker = ProgressTracker()
        for i in range(5):
            tracker.push("page_progress", {"page": i + 1, "total": 5, "pct": (i + 1) * 20})
        tracker.mark_done()

        chunks = list(tracker.stream())
        heartbeats = [c for c in chunks if c.startswith(": heartbeat")]
        assert len(heartbeats) == 0

    def test_idle_counter_resets_on_real_event(self) -> None:
        """A real event between heartbeats must reset the idle counter."""
        tracker = ProgressTracker()

        call_count = [0]

        def _interleaved(timeout=30):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise queue.Empty
            if call_count[0] == 4:
                return {"event": "progress", "data": {"pct": 50}}
            if call_count[0] <= 6:
                raise queue.Empty
            tracker._done.set()
            return None

        with patch.object(tracker._queue, "get", side_effect=_interleaved):
            chunks = list(tracker.stream())

        heartbeats = [c for c in chunks if c.startswith(": heartbeat")]
        named_chunks = [c for c in chunks if c.startswith("event:")]
        assert len(heartbeats) == 5
        assert len(named_chunks) == 1

    def test_page_progress_field_names(self) -> None:
        """push_page should emit total_pages and completed_pages fields."""
        tracker = ProgressTracker()
        tracker.push_page(3, 10)
        tracker.mark_done()

        chunks = list(tracker.stream())
        named = [c for c in chunks if c.startswith("event:")]
        assert len(named) >= 1

        parsed = _parse_named_event(named[0])
        assert parsed is not None
        assert parsed[0] == "page_progress"
        assert parsed[1]["total_pages"] == 10
        assert parsed[1]["completed_pages"] == 3

    def test_page_result_field_names(self) -> None:
        """push_page_result should emit page_num and total_pages fields."""
        tracker = ProgressTracker()
        tracker.push_page_result(5, 20, "# Page 5")
        tracker.mark_done()

        chunks = list(tracker.stream())
        named = [c for c in chunks if c.startswith("event:")]
        assert len(named) >= 1

        parsed = _parse_named_event(named[0])
        assert parsed is not None
        assert parsed[0] == "page_result"
        assert parsed[1]["page_num"] == 5
        assert parsed[1]["total_pages"] == 20
        assert parsed[1]["markdown"] == "# Page 5"


class TestMaxIdleDisconnect:
    """Verify that SSE connections close after too many idle heartbeats."""

    def test_stream_closes_after_max_idle_heartbeats(self) -> None:
        """The stream must terminate after _MAX_IDLE_HEARTBEATS consecutive heartbeats."""
        tracker = ProgressTracker()

        def _always_empty(timeout=30):
            raise queue.Empty

        with patch.object(tracker._queue, "get", side_effect=_always_empty):
            chunks = list(tracker.stream())

        heartbeats = [c for c in chunks if c.startswith(": heartbeat")]
        assert len(heartbeats) == _MAX_IDLE_HEARTBEATS - 1

    def test_events_before_idle_limit(self) -> None:
        """Events emitted normally should not trigger idle disconnect."""
        tracker = ProgressTracker()
        tracker.push("profiling", {"pct": 5, "message": "start"})
        tracker.mark_done()

        chunks = list(tracker.stream())
        named_chunks = [c for c in chunks if c.startswith("event:")]
        assert len(named_chunks) == 1
