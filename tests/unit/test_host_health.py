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
    get_cb_cooldown_sec,
    get_cb_threshold,
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


# ---------------------------------------------------------------------------
# Circuit breaker (P2-1) — open after N consecutive failures, half-open recovery
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Sustained failure opens the circuit (fail fast, no probe); recovery closes it."""

    def test_single_failure_does_not_open(self) -> None:
        """A lone failure must NOT trip the breaker (conservative threshold)."""
        # threshold=3, ttl small so each call re-probes.
        avail = TTLAvailability(ttl_sec=0.001, cb_threshold=3, cb_cooldown_sec=30.0)
        probe = MagicMock(side_effect=[False, True])
        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            assert avail.is_available(probe) is False  # 1 failure (< threshold)
            mono.return_value = 1.0  # TTL expired -> re-probe still allowed
            assert avail.is_available(probe) is True  # recovered, not blocked
        assert probe.call_count == 2  # the circuit never blocked a probe

    def test_opens_after_threshold_then_fails_fast(self) -> None:
        """N consecutive failures open the circuit; further calls skip the probe."""
        avail = TTLAvailability(ttl_sec=0.001, cb_threshold=3, cb_cooldown_sec=30.0)
        probe = MagicMock(return_value=False)
        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            assert avail.is_available(probe) is False  # fail 1
            mono.return_value = 1.0
            assert avail.is_available(probe) is False  # fail 2
            mono.return_value = 2.0
            assert avail.is_available(probe) is False  # fail 3 -> OPEN
            assert probe.call_count == 3

            # Circuit open + within cooldown -> immediate False, NO probe (block 0).
            mono.return_value = 5.0
            assert avail.is_available(probe) is False
            mono.return_value = 20.0
            assert avail.is_available(probe) is False
            assert probe.call_count == 3, "open circuit must not probe"

    def test_half_open_recovers_and_closes(self) -> None:
        """After cooldown a single half-open probe succeeds -> circuit closes."""
        avail = TTLAvailability(ttl_sec=0.001, cb_threshold=2, cb_cooldown_sec=30.0)
        probe = MagicMock(side_effect=[False, False, True])
        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            assert avail.is_available(probe) is False  # fail 1
            mono.return_value = 1.0
            assert avail.is_available(probe) is False  # fail 2 -> OPEN
            assert probe.call_count == 2

            # Within cooldown: no probe.
            mono.return_value = 10.0
            assert avail.is_available(probe) is False
            assert probe.call_count == 2

            # Cooldown elapsed (>=30s after open at t=1): half-open probe runs.
            mono.return_value = 40.0
            assert avail.is_available(probe) is True  # recovered -> CLOSED
            assert probe.call_count == 3

            # Closed again: normal TTL re-probe resumes (side_effect exhausted,
            # so rely on the cache within a fresh window). Confirm not blocked.
            mono.return_value = 40.0005
            assert avail.is_available(probe) is True  # cached within ttl

    def test_half_open_failure_reopens(self) -> None:
        """A failed half-open probe re-opens the circuit for another cooldown."""
        avail = TTLAvailability(ttl_sec=0.001, cb_threshold=2, cb_cooldown_sec=30.0)
        probe = MagicMock(return_value=False)
        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            avail.is_available(probe)  # fail 1
            mono.return_value = 1.0
            avail.is_available(probe)  # fail 2 -> OPEN
            assert probe.call_count == 2

            mono.return_value = 40.0  # cooldown elapsed -> half-open probe (fails)
            assert avail.is_available(probe) is False
            assert probe.call_count == 3  # one half-open probe ran

            # Re-opened: within the new cooldown, no further probe.
            mono.return_value = 45.0
            assert avail.is_available(probe) is False
            assert probe.call_count == 3

    def test_open_transition_is_logged(self, caplog) -> None:
        avail = TTLAvailability(ttl_sec=0.001, cb_threshold=2, cb_cooldown_sec=30.0)
        probe = MagicMock(return_value=False)
        with patch("docforge.adapters.host_health.time.monotonic") as mono:
            mono.return_value = 0.0
            avail.is_available(probe)
            mono.return_value = 1.0
            with caplog.at_level(logging.WARNING, logger="docforge.adapters.host_health"):
                avail.is_available(probe)  # -> OPEN
        assert any("circuit OPEN" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Circuit-breaker + call-timeout configuration (env overrides, sane defaults)
# ---------------------------------------------------------------------------


class TestCircuitBreakerConfig:
    def test_cb_threshold_default(self, monkeypatch) -> None:
        monkeypatch.delenv("DOCFORGE_HOST_CB_THRESHOLD", raising=False)
        assert get_cb_threshold() == 3

    def test_cb_threshold_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("DOCFORGE_HOST_CB_THRESHOLD", "5")
        assert get_cb_threshold() == 5

    def test_cb_threshold_invalid_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv("DOCFORGE_HOST_CB_THRESHOLD", "nope")
        assert get_cb_threshold() == 3

    def test_cb_threshold_below_one_clamped(self, monkeypatch) -> None:
        monkeypatch.setenv("DOCFORGE_HOST_CB_THRESHOLD", "0")
        assert get_cb_threshold() == 3

    def test_cb_cooldown_default(self, monkeypatch) -> None:
        monkeypatch.delenv("DOCFORGE_HOST_CB_COOLDOWN_SEC", raising=False)
        assert get_cb_cooldown_sec() == 30.0

    def test_cb_cooldown_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("DOCFORGE_HOST_CB_COOLDOWN_SEC", "12")
        assert get_cb_cooldown_sec() == 12.0


class TestCallTimeouts:
    """OCR/VLM call timeouts are shortened with env overrides (defect F)."""

    def test_ocr_call_timeout_default_is_30(self, monkeypatch) -> None:
        from docforge.adapters import apple_vision_remote

        monkeypatch.delenv("DOCFORGE_OCR_CALL_TIMEOUT_SEC", raising=False)
        assert apple_vision_remote._get_call_timeout_sec() == 30.0

    def test_ocr_call_timeout_env_override(self, monkeypatch) -> None:
        from docforge.adapters import apple_vision_remote

        monkeypatch.setenv("DOCFORGE_OCR_CALL_TIMEOUT_SEC", "15")
        assert apple_vision_remote._get_call_timeout_sec() == 15.0

    def test_ocr_call_timeout_invalid_falls_back(self, monkeypatch) -> None:
        from docforge.adapters import apple_vision_remote

        monkeypatch.setenv("DOCFORGE_OCR_CALL_TIMEOUT_SEC", "bad")
        assert apple_vision_remote._get_call_timeout_sec() == 30.0

    def test_vlm_call_timeout_default_is_45(self, monkeypatch) -> None:
        from docforge.adapters import host_vlm_engine

        monkeypatch.delenv("DOCFORGE_VLM_CALL_TIMEOUT_SEC", raising=False)
        assert host_vlm_engine._get_call_timeout_sec() == 45.0

    def test_vlm_call_timeout_env_override(self, monkeypatch) -> None:
        from docforge.adapters import host_vlm_engine

        monkeypatch.setenv("DOCFORGE_VLM_CALL_TIMEOUT_SEC", "20")
        assert host_vlm_engine._get_call_timeout_sec() == 20.0


class TestTimeoutBoundsDeadHostCall:
    """A dead host returns within the shortened timeout (no long block)."""

    def test_ocr_call_honours_shortened_timeout(self, monkeypatch) -> None:
        """The OCR remote call passes the shortened timeout to urlopen."""
        import numpy as np

        from docforge.adapters.apple_vision_remote import AppleVisionRemoteEngine

        monkeypatch.setenv("DOCFORGE_OCR_CALL_TIMEOUT_SEC", "30")
        engine = AppleVisionRemoteEngine()
        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ANN001
            captured["timeout"] = timeout
            raise TimeoutError("simulated dead host")

        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with patch.object(engine, "_probe", return_value=True), patch(
            "docforge.adapters.apple_vision_remote.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            # Dead host -> graceful [] (no raise), and the call used the 30s bound.
            assert engine.recognize(image) == []
        assert captured["timeout"] == 30.0
