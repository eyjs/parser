"""Shared async parse-worker loop and configuration.

Why this module exists
----------------------
The async parse queue used to be serviced by a *single daemon thread spawned
inside the gunicorn web process* (``v1_routes._async_worker_loop`` via
``_ensure_async_worker``). Parsing is CPU bound (page loop, OCR orchestration,
Surya), so while a large document parsed, the GIL was held and gunicorn's HTTP
handler threads could not respond to ``submit``/``poll`` -- the upstream saw
"Server disconnected" (requirement.md defect A).

This module extracts the *consumer* loop and its configuration into one place so
that a **separate worker process** (``worker_main.py`` / the ``docforge-worker``
console script) can run it OUTSIDE the web process, giving GIL isolation and
true document-level parallelism. The web process then only enqueues and polls.

It also centralizes the execution-model configuration (defect B / backpressure):
parse-worker count, queue-depth ceiling, Retry-After, and the in-proc fallback
toggle -- each resolved + validated once (0/negative coerced to a sane positive).

The SQLite job store (``job_store.py``) is already multiprocess safe (WAL +
``busy_timeout`` + ``BEGIN IMMEDIATE`` atomic ``claim()`` + ``recover_orphans``),
so nothing about the queue schema or claim logic changes here -- only *who*
runs the consumer loop.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time as _time
from collections.abc import Callable
from pathlib import Path

from docforge.web.job_store import ParseJobStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (resolve + validate once)
# ---------------------------------------------------------------------------


def resolve_positive_int(value: str | int | None, default: int) -> int:
    """Coerce an env-style value to a positive int.

    ``0``, negative, ``None`` and unparseable values all fall back to
    ``default`` (which must itself be >= 1). This is the single rule used for
    every concurrency/queue knob so a footgun like ``Semaphore(0)`` (defect D)
    cannot recur from one of these settings.
    """
    if default < 1:
        raise ValueError(f"default must be >= 1, got {default}")
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "invalid integer config %r; falling back to %d", value, default,
        )
        return default
    if parsed < 1:
        return default
    return parsed


def parse_workers() -> int:
    """Number of parse-worker *processes* to run (document-level concurrency)."""
    default = min(os.cpu_count() or 1, 4)
    return resolve_positive_int(os.environ.get("DOCFORGE_PARSE_WORKERS"), default)


def queue_max() -> int:
    """Queue-depth ceiling for backpressure.

    When the number of *queued* (not yet claimed) jobs reaches this, the web
    layer rejects new submissions with 503 + Retry-After so the upstream backs
    off instead of piling on a thundering herd (defect B).
    """
    default = max(8, 2 * parse_workers())
    return resolve_positive_int(os.environ.get("DOCFORGE_QUEUE_MAX"), default)


def retry_after_sec() -> int:
    """Seconds advertised in the ``Retry-After`` header on a 503 backpressure."""
    return resolve_positive_int(os.environ.get("DOCFORGE_RETRY_AFTER_SEC"), 5)


def inproc_worker_enabled() -> bool:
    """Whether the web process spawns the legacy in-proc worker thread.

    Default ``False`` (``DOCFORGE_INPROC_WORKER=0``): the web process only
    enqueues/polls and a separate ``docforge-worker`` process consumes the
    queue. Set ``1`` for the dev fallback (single in-proc thread, no separate
    process needed).
    """
    return os.environ.get("DOCFORGE_INPROC_WORKER", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


#: Worker idle poll interval (seconds) when no job is available to claim.
ASYNC_POLL_SEC = float(os.environ.get("DOCFORGE_ASYNC_POLL_SEC", "0.5"))
#: Finished-job TTL (seconds) before the payload + row are pruned.
ASYNC_JOB_TTL = int(os.environ.get("DOCFORGE_ASYNC_JOB_TTL", "3600"))
#: Run TTL cleanup roughly every N worker iterations to bound DB churn.
ASYNC_CLEANUP_EVERY = 200


def async_store_dir() -> Path:
    """Resolve the durable store directory (DB + persisted payloads)."""
    configured = os.environ.get("DOCFORGE_ASYNC_STORE_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "docforge_async_jobs"


# ---------------------------------------------------------------------------
# Consumer loop
# ---------------------------------------------------------------------------


def _safe_cleanup(store: ParseJobStore) -> None:
    """Best-effort TTL cleanup; never lets a cleanup error stop the worker."""
    try:
        store.cleanup_expired(ASYNC_JOB_TTL)
    except Exception:  # noqa: BLE001
        logger.exception("async job TTL cleanup failed")


def run_worker_loop(
    store: ParseJobStore,
    parse_payload: Callable[[Path, str], dict],
    should_stop: Callable[[], bool] | None = None,
    poll_sec: float = ASYNC_POLL_SEC,
) -> None:
    """Claim and process durable parse jobs until ``should_stop`` is true.

    Jobs are claimed atomically from the SQLite store (multi-worker safe). A
    job that crashes the process mid-parse stays ``processing`` on disk and is
    requeued by ``recover_orphans`` on the next boot, so no work is lost.

    ``should_stop`` enables cooperative shutdown for the separate worker
    process (SIGTERM). It defaults to "run forever", preserving the legacy
    in-proc daemon-thread behaviour exactly. The *parsing* itself is delegated
    to ``parse_payload`` (the existing ``_parse_by_mime``) unchanged.
    """
    if should_stop is None:
        def should_stop() -> bool:  # pragma: no cover - trivial default
            return False

    iterations = 0
    while not should_stop():
        iterations += 1
        try:
            row = store.claim()
        except Exception:  # noqa: BLE001 -- a transient DB error must not kill the worker
            logger.exception("async worker claim failed; backing off")
            _time.sleep(poll_sec)
            continue

        if row is None:
            if iterations % ASYNC_CLEANUP_EVERY == 0:
                _safe_cleanup(store)
            _time.sleep(poll_sec)
            continue

        try:
            data = parse_payload(Path(row.payload_path), row.mime)
            store.mark_done(row.job_id, data)
        except Exception as exc:  # noqa: BLE001 -- surface any parse failure to poller
            logger.exception("async parse failed for job %s", row.job_id)
            store.mark_failed(row.job_id, str(exc) or type(exc).__name__)

        if iterations % ASYNC_CLEANUP_EVERY == 0:
            _safe_cleanup(store)
