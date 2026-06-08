"""Host service health probing + availability self-recovery (Step 20, G23).

Why this exists
---------------
docforge runs inside a Docker container and reaches the macOS host's external
engines over HTTP: Apple Vision OCR (:5052), Qwen2-VL (:5053) and the embedding
service (:8103). The remote adapters previously cached ``is_available()`` in a
``bool | None`` field *forever*: a single failed probe pinned availability to
``False`` permanently, so a restarted host service was never re-detected and
docforge stayed degraded until its own process restarted (G23).

This module replaces the permanent cache with a TTL re-probe and adds an
optional lightweight background poller. After the TTL elapses (or after a call
failure invalidates the cache) the next ``is_available()`` re-probes the host,
so a recovered service is picked up automatically. graceful degrade (empty
results while down) is preserved; only the first call after the re-probe
interval pays the recovery latency.

stdlib only (urllib / json / threading / time / logging) — no new dependency,
mirroring the existing docforge persistence/threading patterns.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

# A ``/health`` body is considered healthy when its ``status`` field is one of
# these. The embedding service (:8103) reports ``"healthy"`` while the OCR/VLM
# services report ``"ok"`` — accept both so one probe works across all hosts.
_HEALTHY_STATUSES = ("ok", "healthy")

#: Default re-probe TTL (seconds). Overridable per process via env.
_DEFAULT_PROBE_TTL_SEC = 30.0

#: Default background health poll interval (seconds).
_DEFAULT_POLL_INTERVAL_SEC = 15.0

#: Circuit-breaker defaults (P2-1). Conservative so a healthy host is never
#: tripped: it takes ``N`` *consecutive* probe failures to open, then the host
#: is skipped for ``cooldown`` seconds (graceful, zero-block) before a single
#: half-open re-probe decides recovery. A single transient failure (the common
#: case, e.g. one slow request) never opens the circuit.
_DEFAULT_CB_THRESHOLD = 3
_DEFAULT_CB_COOLDOWN_SEC = 30.0


def get_probe_ttl_sec() -> float:
    """Re-probe TTL for availability caches (env ``DOCFORGE_HOST_PROBE_TTL_SEC``)."""
    raw = os.environ.get("DOCFORGE_HOST_PROBE_TTL_SEC")
    if not raw:
        return _DEFAULT_PROBE_TTL_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid DOCFORGE_HOST_PROBE_TTL_SEC=%r, using default %.0fs",
            raw,
            _DEFAULT_PROBE_TTL_SEC,
        )
        return _DEFAULT_PROBE_TTL_SEC
    # A non-positive TTL would re-probe on every call (degenerate). Clamp to a
    # tiny positive floor so callers still get *some* caching.
    return value if value > 0 else 0.001


def get_poll_interval_sec() -> float:
    """Background poll interval (env ``DOCFORGE_HEALTH_POLL_SEC``)."""
    raw = os.environ.get("DOCFORGE_HEALTH_POLL_SEC")
    if not raw:
        return _DEFAULT_POLL_INTERVAL_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid DOCFORGE_HEALTH_POLL_SEC=%r, using default %.0fs",
            raw,
            _DEFAULT_POLL_INTERVAL_SEC,
        )
        return _DEFAULT_POLL_INTERVAL_SEC
    return value if value > 0 else _DEFAULT_POLL_INTERVAL_SEC


def get_cb_threshold() -> int:
    """Consecutive failures that open the circuit (env ``DOCFORGE_HOST_CB_THRESHOLD``)."""
    raw = os.environ.get("DOCFORGE_HOST_CB_THRESHOLD")
    if not raw:
        return _DEFAULT_CB_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "invalid DOCFORGE_HOST_CB_THRESHOLD=%r, using default %d",
            raw,
            _DEFAULT_CB_THRESHOLD,
        )
        return _DEFAULT_CB_THRESHOLD
    # A threshold < 1 would open on the first (or zeroth) failure, defeating the
    # "conservative" contract; clamp up to the default floor.
    return value if value >= 1 else _DEFAULT_CB_THRESHOLD


def get_cb_cooldown_sec() -> float:
    """Open-circuit cooldown before a half-open re-probe (env ``DOCFORGE_HOST_CB_COOLDOWN_SEC``)."""
    raw = os.environ.get("DOCFORGE_HOST_CB_COOLDOWN_SEC")
    if not raw:
        return _DEFAULT_CB_COOLDOWN_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid DOCFORGE_HOST_CB_COOLDOWN_SEC=%r, using default %.0fs",
            raw,
            _DEFAULT_CB_COOLDOWN_SEC,
        )
        return _DEFAULT_CB_COOLDOWN_SEC
    return value if value > 0 else _DEFAULT_CB_COOLDOWN_SEC


def probe_health(url: str, timeout: float = 3.0) -> bool:
    """Return ``True`` when ``{url}/health`` reports a healthy status.

    Any transport error, non-JSON body, or non-healthy ``status`` field yields
    ``False`` (the caller degrades gracefully). Never raises.
    """
    health_url = f"{url.rstrip('/')}/health"
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted host URL)
            data = json.loads(resp.read())
    except Exception:  # noqa: BLE001 — health probe must never raise
        logger.debug("host health probe failed: %s", health_url)
        return False

    status = data.get("status") if isinstance(data, dict) else None
    return status in _HEALTHY_STATUSES


class TTLAvailability:
    """TTL-cached availability with re-probe self-recovery + a circuit breaker.

    Replaces the previous permanent ``bool | None`` cache. ``is_available`` runs
    the supplied ``probe_fn`` again once the TTL has elapsed (or after
    :meth:`invalidate`), so a recovered host service is re-detected
    automatically (G23). Uses ``time.monotonic`` to stay correct across
    wall-clock jumps.

    Circuit breaker (P2-1)
    ----------------------
    To stop a dead/slow host from being re-probed (and then *called*) on every
    page, the breaker counts *consecutive* probe failures. After
    ``cb_threshold`` (default 3) failures it **opens**: ``is_available`` returns
    ``False`` immediately for ``cb_cooldown_sec`` (default 30s) *without probing*
    — zero network block. When the cooldown elapses the next call is **half-open**:
    it probes once; success closes the breaker (recovered) and resets the
    counter, failure re-opens it for another cooldown. A successful probe always
    resets the failure count, so a healthy host is never tripped by isolated
    blips — the breaker only fires on *sustained* failure, dovetailing with the
    G23 TTL re-probe rather than replacing it.

    This object owns only its own cache fields; it never mutates the caller's
    state.
    """

    def __init__(
        self,
        ttl_sec: float | None = None,
        cb_threshold: int | None = None,
        cb_cooldown_sec: float | None = None,
    ) -> None:
        self._ttl_sec = get_probe_ttl_sec() if ttl_sec is None else ttl_sec
        self._cb_threshold = get_cb_threshold() if cb_threshold is None else cb_threshold
        self._cb_cooldown_sec = (
            get_cb_cooldown_sec() if cb_cooldown_sec is None else cb_cooldown_sec
        )
        self._cached: bool | None = None
        self._probed_at: float | None = None
        # Circuit-breaker state.
        self._consecutive_failures = 0
        self._opened_at: float | None = None  # monotonic time the circuit opened
        self._lock = threading.Lock()

    def is_available(self, probe_fn: Callable[[], bool]) -> bool:
        """Return availability, re-probing via ``probe_fn`` when due.

        Skips the probe entirely (returns ``False``) while the circuit is open
        and still within its cooldown, so a down host imposes no per-call block.
        """
        circuit_event: str | None = None
        with self._lock:
            now = time.monotonic()

            # Circuit open + still cooling down -> fail fast, do NOT probe.
            if self._opened_at is not None:
                if (now - self._opened_at) < self._cb_cooldown_sec:
                    return False
                # Cooldown elapsed -> fall through to a single half-open probe.

            fresh = (
                self._opened_at is None
                and self._cached is not None
                and self._probed_at is not None
                and (now - self._probed_at) < self._ttl_sec
            )
            if fresh:
                return self._cached  # type: ignore[return-value]

            result = bool(probe_fn())
            previous = self._cached
            self._cached = result
            self._probed_at = now

            if result:
                # Success closes the breaker and clears the failure streak.
                if self._opened_at is not None:
                    circuit_event = "closed"
                self._consecutive_failures = 0
                self._opened_at = None
            else:
                self._consecutive_failures += 1
                if self._opened_at is not None:
                    # Half-open probe failed -> re-open for another cooldown.
                    self._opened_at = now
                    circuit_event = "reopened"
                elif self._consecutive_failures >= self._cb_threshold:
                    self._opened_at = now
                    circuit_event = "opened"

        # Log transitions outside the lock (no I/O under lock).
        if previous is not None and previous != result:
            if result:
                logger.info("host engine recovered (re-probe): now available")
            else:
                logger.warning("host engine became unavailable (re-probe)")
        if circuit_event == "opened":
            logger.warning(
                "host engine circuit OPEN after %d consecutive failures; "
                "skipping calls for %.0fs",
                self._consecutive_failures,
                self._cb_cooldown_sec,
            )
        elif circuit_event == "reopened":
            logger.warning(
                "host engine circuit re-opened (half-open probe failed); "
                "cooling down %.0fs",
                self._cb_cooldown_sec,
            )
        elif circuit_event == "closed":
            logger.info("host engine circuit CLOSED (half-open probe succeeded)")
        return result

    def invalidate(self) -> None:
        """Force the next :meth:`is_available` to re-probe immediately.

        Called by adapters after a remote call fails, so a service that went
        down mid-use is re-probed on the very next call rather than waiting out
        the full TTL. While the circuit is open, the cooldown still takes
        precedence (the open check runs before the TTL gate), so ``invalidate``
        cannot stampede a known-down host -- it just clears the stale cache.
        """
        with self._lock:
            self._probed_at = None


class HostHealthPoller:
    """Lightweight background poller that pings host services and logs status.

    Pings each target's ``/health`` every ``interval_sec`` on a daemon thread,
    logging only *transitions* (up→down / down→up) to avoid log noise. This is
    observability only — it does **not** auto-start (callers opt in via
    :meth:`start`) and it does **not** auto-launch host services (out of scope,
    documented instead). Because availability caches re-probe on their own TTL,
    the poller is not required for recovery; it just makes host health visible.
    """

    def __init__(
        self,
        targets: dict[str, str],
        interval_sec: float | None = None,
        probe_timeout_sec: float = 3.0,
    ) -> None:
        self._targets = dict(targets)
        self._interval = get_poll_interval_sec() if interval_sec is None else interval_sec
        self._probe_timeout = probe_timeout_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_state: dict[str, bool] = {}

    def start(self) -> None:
        """Start the daemon poll loop (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="docforge-host-health-poller", daemon=True
        )
        self._thread.start()
        logger.info(
            "host health poller started: targets=%s interval=%.0fs",
            list(self._targets),
            self._interval,
        )

    def stop(self, join_timeout: float = 2.0) -> None:
        """Signal the loop to stop and join the thread."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=join_timeout)
        self._thread = None

    def poll_once(self) -> dict[str, bool]:
        """Probe every target once, log transitions, return current states."""
        states: dict[str, bool] = {}
        for name, url in self._targets.items():
            up = probe_health(url, timeout=self._probe_timeout)
            states[name] = up
            previous = self._last_state.get(name)
            if previous is None:
                # First observation: report the baseline so operators see it.
                logger.info("host service %s initial state: %s", name, "up" if up else "down")
            elif previous != up:
                if up:
                    logger.info("host service %s recovered: down -> up", name)
                else:
                    logger.warning("host service %s lost: up -> down", name)
            self._last_state[name] = up
        return states

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — poller must never crash the daemon
                logger.exception("host health poll iteration failed")
            # Interruptible sleep so stop() returns promptly.
            self._stop.wait(self._interval)
