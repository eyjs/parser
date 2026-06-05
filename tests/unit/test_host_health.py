"""Tests for host service health probing + availability self-recovery (Step 20, G23).

Covers the TTL re-probe cache that replaces the old permanent ``bool | None``
availability cache (G23 root cause): a recovered host service must be
re-detected automatically. All network access is mocked / monotonic is patched
so these tests are deterministic and live-host independent.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import numpy as np

from docforge.adapters.host_health import (
    HostHealthPoller,
    TTLAvailability,
    probe_health,
)

# ---------------------------------------------------------------------------
# TTLAvailability — TTL re-probe + recovery (the G23 fix)
# ---------------------------------------------------------------------------


class TestTTLAvailability:
    """TTL cache re-probes after expiry so a recovered service is re-detected."""

    def test_caches_within_ttl(self) -> None:
        """Within the TTL the probe_fn is not called again."""
        probe = MagicMock(return_value=True)
        avail = TTLAvailability(ttl_sec=30.0)

        with patch("docforge.adapters.host_health.time.monotonic", return_value=100.0):
            assert avail.is_available(probe) is True
            assert avail.is_available(probe) is True  # cached

        assert probe.call_count == 1, "probe should be cached within the TTL"

    def test_reprobes_after_ttl_expiry(self) -> None:
        """Once the TTL elapses the probe runs again (no permanent cache)."""
        probe = MagicMock(return_value=False)
        avail = TTLAvailability(ttl_sec=30.0)

        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 100.0
            assert avail.is_available(probe) is False
            assert avail.is_available(probe) is False  # cached, no re-probe
            assert probe.call_count == 1

            mono.return_value = 131.0  # 31s later -> TTL (30s) expired
            assert avail.is_available(probe) is False  # re-probe happens
            assert probe.call_count == 2

    def test_down_then_recovery_transition(self) -> None:
        """Down -> (health restored) -> re-probe after TTL returns True.

        This is the G23 self-recovery contract: the old permanent cache pinned
        False forever; the TTL re-probe must pick up the recovered service.
        """
        probe = MagicMock(side_effect=[False, True])
        avail = TTLAvailability(ttl_sec=30.0)

        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            assert avail.is_available(probe) is False  # down, cached False

            mono.return_value = 50.0  # TTL expired, service recovered
            assert avail.is_available(probe) is True  # re-probe -> recovered

        assert probe.call_count == 2

    def test_invalidate_forces_immediate_reprobe(self) -> None:
        """invalidate() re-probes on the next call even within the TTL."""
        probe = MagicMock(side_effect=[False, True])
        avail = TTLAvailability(ttl_sec=30.0)

        with patch("docforge.adapters.host_health.time.monotonic", return_value=100.0):
            assert avail.is_available(probe) is False  # cached False
            assert avail.is_available(probe) is False  # still cached
            assert probe.call_count == 1

            avail.invalidate()
            # Same monotonic time, but invalidate forces a re-probe.
            assert avail.is_available(probe) is True
            assert probe.call_count == 2

    def test_recovery_transition_is_logged(self, caplog) -> None:
        """A down->up re-probe transition logs a recovery message."""
        probe = MagicMock(side_effect=[False, True])
        avail = TTLAvailability(ttl_sec=10.0)

        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            avail.is_available(probe)
            mono.return_value = 20.0
            with caplog.at_level(logging.INFO, logger="docforge.adapters.host_health"):
                avail.is_available(probe)

        assert any("recovered" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# probe_health — accepts both "ok" and "healthy" status schemas
# ---------------------------------------------------------------------------


class TestProbeHealth:
    """Health probe tolerates OCR/VLM ("ok") and embedding ("healthy") schemas."""

    def _patch_urlopen(self, body: bytes):
        resp = MagicMock()
        resp.read.return_value = body
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=resp)
        ctx.__exit__ = MagicMock(return_value=False)
        return patch("docforge.adapters.host_health.urllib.request.urlopen", return_value=ctx)

    def test_status_ok_is_healthy(self) -> None:
        with self._patch_urlopen(json.dumps({"status": "ok"}).encode()):
            assert probe_health("http://host:5052") is True

    def test_status_healthy_is_healthy(self) -> None:
        with self._patch_urlopen(json.dumps({"status": "healthy"}).encode()):
            assert probe_health("http://host:8103") is True

    def test_status_degraded_is_unhealthy(self) -> None:
        with self._patch_urlopen(json.dumps({"status": "degraded"}).encode()):
            assert probe_health("http://host:5052") is False

    def test_transport_error_is_unhealthy(self) -> None:
        with patch(
            "docforge.adapters.host_health.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            assert probe_health("http://host:5052") is False

    def test_non_dict_body_is_unhealthy(self) -> None:
        with self._patch_urlopen(json.dumps(["not", "a", "dict"]).encode()):
            assert probe_health("http://host:5052") is False


# ---------------------------------------------------------------------------
# HostHealthPoller — logs only state transitions
# ---------------------------------------------------------------------------


class TestHostHealthPoller:
    """Poller pings targets and logs up<->down transitions only."""

    def test_poll_once_logs_initial_then_transitions(self, caplog) -> None:
        # up -> down -> up sequence for a single target.
        seq = [True, False, True]
        with patch(
            "docforge.adapters.host_health.probe_health",
            side_effect=seq,
        ):
            poller = HostHealthPoller({"ocr": "http://host:5052"}, interval_sec=0.01)
            with caplog.at_level(logging.INFO, logger="docforge.adapters.host_health"):
                s1 = poller.poll_once()  # initial: up
                s2 = poller.poll_once()  # transition: up -> down
                s3 = poller.poll_once()  # transition: down -> up

        assert s1 == {"ocr": True}
        assert s2 == {"ocr": False}
        assert s3 == {"ocr": True}
        messages = " ".join(r.message for r in caplog.records)
        assert "initial state: up" in messages
        assert "lost: up -> down" in messages
        assert "recovered: down -> up" in messages

    def test_poll_once_no_log_when_stable(self, caplog) -> None:
        """No transition logging when the state is unchanged."""
        with patch(
            "docforge.adapters.host_health.probe_health",
            side_effect=[True, True, True],
        ):
            poller = HostHealthPoller({"ocr": "http://host:5052"}, interval_sec=0.01)
            poller.poll_once()  # initial logged
            with caplog.at_level(logging.INFO, logger="docforge.adapters.host_health"):
                poller.poll_once()
                poller.poll_once()

        # After the initial observation, stable states produce no further logs.
        assert not [r for r in caplog.records if "->" in r.message]

    def test_start_stop_runs_loop_and_terminates(self) -> None:
        """start() runs the daemon loop, stop() terminates it promptly."""
        with patch(
            "docforge.adapters.host_health.probe_health",
            return_value=True,
        ):
            poller = HostHealthPoller({"ocr": "http://host:5052"}, interval_sec=0.01)
            poller.start()
            poller.start()  # idempotent — must not spawn a second thread
            poller.stop(join_timeout=2.0)

        assert poller._thread is None


# ---------------------------------------------------------------------------
# Adapter-level recovery (the real seam) — down -> recovered self-detection
# ---------------------------------------------------------------------------


class TestAdapterSelfRecovery:
    """Both remote adapters re-detect a recovered host service via TTL re-probe."""

    def test_apple_vision_remote_recovers(self) -> None:
        from docforge.adapters.apple_vision_remote import AppleVisionRemoteEngine

        engine = AppleVisionRemoteEngine()
        with patch.object(engine, "_probe", side_effect=[False, True]) as probe, patch(
            "docforge.adapters.host_health.time.monotonic"
        ) as mono:
            mono.return_value = 0.0
            assert engine.is_available() is False  # OCR down -> cached False
            mono.return_value = 100.0  # TTL expired, OCR restarted
            assert engine.is_available() is True  # self-recovered
        assert probe.call_count == 2

    def test_host_vlm_engine_recovers(self) -> None:
        from docforge.adapters.host_vlm_engine import HostVLMEngine

        engine = HostVLMEngine()
        with patch.object(engine, "_probe", side_effect=[False, True]) as probe, patch(
            "docforge.adapters.host_health.time.monotonic"
        ) as mono:
            mono.return_value = 0.0
            assert engine.is_available() is False
            mono.return_value = 100.0
            assert engine.is_available() is True
        assert probe.call_count == 2

    def test_apple_vision_call_failure_invalidates_cache(self) -> None:
        """A failed recognize() invalidates the cache so recovery is immediate."""
        from docforge.adapters.apple_vision_remote import AppleVisionRemoteEngine

        engine = AppleVisionRemoteEngine()
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with patch.object(engine, "_probe", side_effect=[True, True]) as probe, patch.object(
            engine, "_call_remote", side_effect=RuntimeError("host gone")
        ), patch("docforge.adapters.host_health.time.monotonic", return_value=5.0):
            # First call: available, but remote call fails -> graceful [] + invalidate.
            assert engine.recognize(image) == []
            # invalidate() forces a re-probe on the next is_available despite same time.
            assert engine.is_available() is True
        assert probe.call_count == 2
