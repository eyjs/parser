"""Tests for SSE heartbeat and connection timeout (task-005)."""

from __future__ import annotations

import json
import queue
import threading
import time
from unittest.mock import patch

import pytest

from docforge.web.sse import ProgressTracker, _MAX_CONNECTION_SECONDS


class TestHeartbeat:
    """Verify heartbeat emission on idle SSE connections."""

    def test_heartbeat_emitted_on_idle(self) -> None:
        """When no events arrive within the queue timeout, a heartbeat comment is emitted."""
        tracker = ProgressTracker()

        # Push done after a tiny delay so the stream eventually terminates,
        # but use a very short queue timeout via monkeypatch to trigger heartbeat.
        def _delayed_done():
            time.sleep(0.2)
            tracker.mark_done()

        t = threading.Thread(target=_delayed_done, daemon=True)
        t.start()

        # Collect chunks but use a modified stream that has a shorter timeout
        # by temporarily patching _queue.get to use a very small timeout.
        original_get = tracker._queue.get
        call_count = [0]

        def _short_timeout_get(timeout=30):
            call_count[0] += 1
            return original_get(timeout=0.05)  # 50ms instead of 30s

        with patch.object(tracker._queue, "get", side_effect=_short_timeout_get):
            chunks = list(tracker.stream())

        t.join(timeout=5)

        # At least one heartbeat should have been emitted
        heartbeats = [c for c in chunks if c.startswith(": heartbeat")]
        assert len(heartbeats) >= 1

    def test_heartbeat_is_sse_comment_format(self) -> None:
        """Heartbeat must use SSE comment format ': heartbeat\\n\\n'."""
        tracker = ProgressTracker()

        # Directly test the format by triggering a queue.Empty
        original_get = tracker._queue.get

        def _always_empty(timeout=30):
            raise queue.Empty

        # Push nothing, mark done after first heartbeat
        call_count = [0]

        def _empty_then_done(timeout=30):
            call_count[0] += 1
            if call_count[0] == 1:
                raise queue.Empty
            # On second call, simulate done
            tracker._done.set()
            return None

        with patch.object(tracker._queue, "get", side_effect=_empty_then_done):
            chunks = list(tracker.stream())

        assert len(chunks) == 1
        assert chunks[0] == ": heartbeat\n\n"

    def test_normal_events_then_done(self) -> None:
        """Normal events should be yielded as data lines, not as comments."""
        tracker = ProgressTracker()
        tracker.push("profiling", {"pct": 5, "message": "start"})
        tracker.push("done", {"pct": 100, "message": "finished"})
        tracker.mark_done()

        chunks = list(tracker.stream())
        # Should have 2 data events (profiling + done), no heartbeat
        data_chunks = [c for c in chunks if c.startswith("data:")]
        assert len(data_chunks) == 2

        # Verify first event
        first = json.loads(data_chunks[0].replace("data: ", "").strip())
        assert first["event"] == "profiling"

    def test_no_heartbeat_when_events_flow(self) -> None:
        """When events arrive promptly, no heartbeat comments are emitted."""
        tracker = ProgressTracker()
        for i in range(5):
            tracker.push("page_progress", {"page": i + 1, "total": 5, "pct": (i + 1) * 20})
        tracker.mark_done()

        chunks = list(tracker.stream())
        heartbeats = [c for c in chunks if c.startswith(": heartbeat")]
        assert len(heartbeats) == 0


class TestMaxConnectionTimeout:
    """Verify that SSE connections are closed after _MAX_CONNECTION_SECONDS."""

    def test_stream_closes_after_max_duration(self) -> None:
        """The stream must terminate once max connection time elapses."""
        tracker = ProgressTracker()

        # Patch time.monotonic to simulate elapsed time
        start_time = 1000.0
        call_count = [0]

        def _fake_monotonic():
            call_count[0] += 1
            if call_count[0] == 1:
                return start_time  # initial call in stream()
            # After first iteration, jump past the limit
            return start_time + _MAX_CONNECTION_SECONDS + 1

        with patch("docforge.web.sse.time") as mock_time:
            mock_time.monotonic = _fake_monotonic
            chunks = list(tracker.stream())

        # Stream should have terminated without events (no data, no heartbeat)
        assert len(chunks) == 0

    def test_events_before_timeout(self) -> None:
        """Events emitted before the timeout should be delivered normally."""
        tracker = ProgressTracker()
        tracker.push("profiling", {"pct": 5, "message": "start"})
        tracker.mark_done()

        # With default timeout (600s), events should flow normally
        chunks = list(tracker.stream())
        data_chunks = [c for c in chunks if c.startswith("data:")]
        assert len(data_chunks) == 1
