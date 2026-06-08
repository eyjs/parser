"""``docforge-worker`` entrypoint: a separate parse-worker *process* pool.

Why a separate process
----------------------
Parsing is CPU bound. Running the parse consumer as a daemon thread *inside* the
gunicorn web process means a large document holds the GIL and starves gunicorn's
HTTP handler threads, so ``submit``/``poll`` time out ("Server disconnected" --
requirement.md defect A). Moving the consumer into its own process gives GIL
isolation: the web process stays responsive while ``DOCFORGE_PARSE_WORKERS``
processes parse documents in parallel.

Topology
--------
Run this alongside the gunicorn web process (e.g. a second compose service
``docforge-worker`` built from the same image, command ``docforge-worker``),
both pointing at the *same* ``DOCFORGE_ASYNC_STORE_DIR`` on a shared volume.
Single-node, multi-process is the premise: SQLite WAL + ``BEGIN IMMEDIATE``
atomic ``claim()`` makes concurrent consumers on one host safe. Horizontal
multi-host fan-out is out of scope (would need a different queue backend).

Lifecycle
---------
``main()`` recovers orphaned jobs once (idempotent), spawns N worker processes,
and blocks until SIGTERM/SIGINT, then asks the children to stop, joins them with
a bounded grace period, and terminates any straggler. Each child runs the shared
``async_worker.run_worker_loop`` with a cooperative stop ``Event`` -- the parsing
algorithm itself is unchanged.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import sys
import threading
from multiprocessing.synchronize import Event as EventType

from docforge.web import async_worker

logger = logging.getLogger(__name__)

#: Grace period (seconds) to wait for children to drain after a stop signal.
_SHUTDOWN_GRACE_SEC = float(os.environ.get("DOCFORGE_WORKER_SHUTDOWN_GRACE_SEC", "30"))


def _child_main(stop_event: EventType, worker_index: int) -> None:
    """Entry for a single worker process: run the shared loop until stopped."""
    logging.basicConfig(
        level=os.environ.get("DOCFORGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [worker-%(process)d] %(name)s: %(message)s",
    )
    # A child may be signalled directly (e.g. SIGTERM to the whole process group
    # in a container). React via a signal-safe local flag rather than touching
    # the shared multiprocessing.Event from inside the handler. The loop stops
    # when EITHER the supervisor sets the cross-process Event OR this local flag
    # is raised.
    local_stop = threading.Event()

    def _request_stop(_signum: int, _frame: object) -> None:
        local_stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # Import the parse callable lazily inside the child so a spawn-start child
    # does not import Flask/route machinery it does not need at module load.
    from docforge.web.v1_routes import _parse_by_mime

    logger.info(
        "parse worker %d starting (store=%s)",
        worker_index, async_worker.async_store_dir(),
    )
    job_store = _build_store()

    def _should_stop() -> bool:
        return local_stop.is_set() or stop_event.is_set()

    async_worker.run_worker_loop(
        job_store,
        _parse_by_mime,
        should_stop=_should_stop,
    )
    logger.info("parse worker %d stopped", worker_index)


def _build_store():
    """Construct a fresh ParseJobStore bound to the configured directory.

    Each process owns its own SQLite connection (the store uses thread-local
    connections); building it inside the child guarantees no connection is
    shared across the process boundary.
    """
    from docforge.web.job_store import ParseJobStore

    return ParseJobStore(async_worker.async_store_dir())


def main(argv: list[str] | None = None) -> int:
    """Launch the parse-worker process pool and supervise it until signalled."""
    logging.basicConfig(
        level=os.environ.get("DOCFORGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [supervisor] %(name)s: %(message)s",
    )

    n_workers = async_worker.parse_workers()
    store_dir = async_worker.async_store_dir()
    logger.info(
        "docforge-worker: launching %d parse worker(s); store=%s",
        n_workers, store_dir,
    )

    # Recover orphaned jobs ONCE in the supervisor before children start. This
    # is idempotent (claim is atomic; a clean shutdown leaves no 'processing'
    # rows), so even a duplicate recovery elsewhere is harmless.
    recovered = _build_store().recover_orphans()
    if recovered:
        logger.info("recovered %d orphaned job(s) on boot", recovered)

    ctx = mp.get_context("spawn")
    stop_event: EventType = ctx.Event()

    procs: list[mp.process.BaseProcess] = []
    for i in range(n_workers):
        p = ctx.Process(
            target=_child_main,
            args=(stop_event, i),
            name=f"docforge-parse-worker-{i}",
            daemon=False,
        )
        p.start()
        procs.append(p)

    # Signal handling note: a SIGTERM/SIGINT handler must NOT touch the shared
    # ``multiprocessing.Event`` directly. The supervisor's main thread blocks on
    # that Event's internal lock while polling children; mutating it from inside
    # the interrupting signal handler can deadlock against that very lock. So the
    # handler flips a plain, signal-safe ``threading.Event`` only, and the main
    # loop below propagates the request to the cross-process ``stop_event`` from
    # normal control flow.
    shutdown_requested = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Block until a stop is requested or a child dies unexpectedly. Poll on a
    # short interval; the threading.Event wait is interrupted promptly when the
    # signal handler fires.
    while not shutdown_requested.is_set():
        if shutdown_requested.wait(timeout=0.5):
            break
        dead = [p for p in procs if not p.is_alive()]
        if dead:
            logger.error(
                "%d parse worker(s) exited unexpectedly; shutting down",
                len(dead),
            )
            break

    logger.info("requesting graceful shutdown of %d worker(s)", len(procs))
    # Now -- from normal control flow, NOT a signal handler -- ask the children
    # to stop via the cross-process Event.
    stop_event.set()

    # Graceful drain: join within the grace period, then terminate stragglers.
    for p in procs:
        p.join(timeout=_SHUTDOWN_GRACE_SEC)
    stragglers = [p for p in procs if p.is_alive()]
    for p in stragglers:
        logger.warning("terminating straggler worker pid=%s", p.pid)
        p.terminate()
    for p in stragglers:
        p.join(timeout=5.0)

    exit_code = 0 if all(p.exitcode in (0, None) for p in procs) else 1
    logger.info("docforge-worker: all workers stopped (exit=%d)", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
