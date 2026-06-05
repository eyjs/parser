"""SQLite-backed durable job store for the /v1 async parse queue.

Why this exists
---------------
The previous async parse queue (``v1_routes.py``) kept jobs in an in-process
``dict`` + ``queue.Queue`` serviced by a single daemon thread. A worker or
process restart wiped every queued/in-flight job: pollers then received a 404
and gave up permanently (no re-queue). A large document (e.g. a 1500-page
insurance 약관) caught mid-parse was simply lost.

This module persists jobs to SQLite so they survive restarts, can be claimed
safely by multiple workers, and are recovered after a crash. It deliberately
mirrors the existing persistence pattern already used by ``storage.py``
(per-thread connection, WAL journal, ``busy_timeout``, stale-row recovery),
keeping docforge on a single, familiar storage technology. docforge is a
standalone tool with no PostgreSQL access; introducing a PG dependency here
would add DSN/network/migration coupling a single-container tool does not need.

Concurrency contract
--------------------
``claim()`` runs an ``UPDATE ... WHERE status='queued'`` inside a
``BEGIN IMMEDIATE`` transaction and checks ``rowcount``. SQLite serialises
writers, so when two workers race for the same job exactly one sees
``rowcount == 1``; the other sees ``0`` and retries. This is the standalone
equivalent of PostgreSQL ``SELECT ... FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import local

logger = logging.getLogger(__name__)

# Job lifecycle states.
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_VALID_STATUSES = frozenset(
    {STATUS_QUEUED, STATUS_PROCESSING, STATUS_DONE, STATUS_FAILED},
)


@dataclass(frozen=True)
class JobRow:
    """Immutable snapshot of a single ``parse_jobs`` row.

    ``result`` is the decoded JSON ``data`` payload (``None`` until done).
    """

    job_id: str
    status: str
    mime: str
    payload_path: str
    result: dict | None
    error: str
    attempts: int
    created_at: float
    updated_at: float


class ParseJobStore:
    """SQLite-backed durable store for async parse jobs.

    Thread-safe across workers via WAL mode + ``BEGIN IMMEDIATE`` claims.
    One DB file (``parse_jobs.db``) lives under ``db_dir``; job payloads
    (the original uploaded files) are persisted by the caller and referenced
    by ``payload_path`` so they survive until the job is cleaned up.
    """

    def __init__(self, db_dir: Path) -> None:
        self._dir = Path(db_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "parse_jobs.db"
        self._local = local()
        self._init_db()

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parse_jobs (
                job_id       TEXT PRIMARY KEY,
                status       TEXT NOT NULL DEFAULT 'queued',
                mime         TEXT NOT NULL,
                payload_path TEXT NOT NULL,
                result       TEXT DEFAULT '',
                error        TEXT DEFAULT '',
                attempts     INTEGER NOT NULL DEFAULT 0,
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parse_jobs_status"
            " ON parse_jobs(status, created_at)"
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(row: sqlite3.Row) -> JobRow:
        raw_result = row["result"]
        result: dict | None = None
        if raw_result:
            try:
                result = json.loads(raw_result)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "parse_jobs result JSON decode failed for job %s",
                    row["job_id"],
                )
                result = None
        return JobRow(
            job_id=row["job_id"],
            status=row["status"],
            mime=row["mime"],
            payload_path=row["payload_path"],
            result=result,
            error=row["error"] or "",
            attempts=row["attempts"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Enqueue / claim / complete
    # ------------------------------------------------------------------

    def enqueue(self, job_id: str, mime: str, payload_path: str) -> None:
        """Persist a new queued job. Validates required fields (fail fast)."""
        if not job_id:
            raise ValueError("job_id is required")
        if not mime:
            raise ValueError("mime is required")
        if not payload_path:
            raise ValueError("payload_path is required")
        now = time.time()
        conn = self._conn()
        conn.execute(
            "INSERT INTO parse_jobs"
            " (job_id, status, mime, payload_path, result, error,"
            "  attempts, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '', '', 0, ?, ?)",
            (job_id, STATUS_QUEUED, mime, payload_path, now, now),
        )
        conn.commit()

    def claim(self) -> JobRow | None:
        """Atomically claim the oldest queued job, marking it ``processing``.

        Returns the claimed ``JobRow`` or ``None`` if no job is available.
        Safe under concurrent workers: ``BEGIN IMMEDIATE`` serialises writers,
        and the claimed row is identified by its own ``job_id`` (captured in
        the same transaction) rather than by a timestamp, so two workers can
        never win or mis-identify the same job.
        """
        now = time.time()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute(
                "SELECT job_id FROM parse_jobs WHERE status = ?"
                " ORDER BY created_at LIMIT 1",
                (STATUS_QUEUED,),
            ).fetchone()
            if target is None:
                conn.rollback()
                return None
            job_id = target["job_id"]
            conn.execute(
                "UPDATE parse_jobs SET status = ?, updated_at = ?"
                " WHERE job_id = ?",
                (STATUS_PROCESSING, now, job_id),
            )
            row = conn.execute(
                "SELECT * FROM parse_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            logger.exception("parse_jobs claim failed")
            raise
        return self._to_row(row) if row else None

    def mark_done(self, job_id: str, result: dict) -> None:
        """Record a successful parse result."""
        now = time.time()
        conn = self._conn()
        conn.execute(
            "UPDATE parse_jobs SET status = ?, result = ?, error = '',"
            " updated_at = ? WHERE job_id = ?",
            (STATUS_DONE, json.dumps(result, ensure_ascii=False), now, job_id),
        )
        conn.commit()

    def mark_failed(self, job_id: str, error: str) -> None:
        """Record a parse failure, incrementing the attempt counter."""
        now = time.time()
        conn = self._conn()
        conn.execute(
            "UPDATE parse_jobs SET status = ?, error = ?,"
            " attempts = attempts + 1, updated_at = ? WHERE job_id = ?",
            (STATUS_FAILED, error or "parse failed", now, job_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Lookup / recovery / cleanup
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> JobRow | None:
        """Fetch a job by id, or ``None`` if it does not exist."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM parse_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._to_row(row) if row else None

    def recover_orphans(self) -> int:
        """Requeue jobs left ``processing`` by a crashed worker.

        Called once at worker boot. Idempotent: a clean shutdown leaves no
        ``processing`` rows, so this is a no-op in the common case.
        Returns the number of jobs recovered.
        """
        now = time.time()
        conn = self._conn()
        cur = conn.execute(
            "UPDATE parse_jobs SET status = ?, updated_at = ?"
            " WHERE status = ?",
            (STATUS_QUEUED, now, STATUS_PROCESSING),
        )
        conn.commit()
        recovered = cur.rowcount
        if recovered:
            logger.info("recovered %d orphaned parse job(s) on boot", recovered)
        return recovered

    def cleanup_expired(self, ttl_sec: float) -> int:
        """Delete finished jobs older than ``ttl_sec`` and their payloads.

        Only ``done``/``failed`` jobs past the TTL are removed; queued and
        in-flight jobs are always preserved. Returns the number of rows
        deleted.
        """
        cutoff = time.time() - ttl_sec
        conn = self._conn()
        stale = conn.execute(
            "SELECT job_id, payload_path FROM parse_jobs"
            " WHERE status IN (?, ?) AND updated_at < ?",
            (STATUS_DONE, STATUS_FAILED, cutoff),
        ).fetchall()
        for row in stale:
            payload_path = row["payload_path"]
            if payload_path:
                # Remove the per-job payload directory (parent of the file).
                parent = Path(payload_path).parent
                shutil.rmtree(parent, ignore_errors=True)
            conn.execute(
                "DELETE FROM parse_jobs WHERE job_id = ?", (row["job_id"],)
            )
        conn.commit()
        return len(stale)

    def queued_count(self) -> int:
        """Number of jobs currently waiting to be processed."""
        conn = self._conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM parse_jobs WHERE status = ?",
            (STATUS_QUEUED,),
        ).fetchone()
        return int(row["n"]) if row else 0
