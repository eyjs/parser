"""Tests for the durable SQLite-backed parse job store (Step19, G21).

Covers the durability contract that fixes G21: enqueue persists, claims are
single-winner under concurrency, orphaned (crashed) jobs are recovered on
boot and reprocessed, results are recorded, and finished jobs are pruned by
TTL while in-flight jobs are preserved.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from docforge.web.job_store import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    ParseJobStore,
)


@pytest.fixture
def store(tmp_path):
    return ParseJobStore(tmp_path / "jobs")


def _make_payload(tmp_path: Path, job_id: str, name: str = "doc.pdf") -> str:
    job_dir = tmp_path / "payloads" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    p = job_dir / name
    p.write_bytes(b"%PDF-1.4 fake")
    return str(p)


# ---------------------------------------------------------------------------
# 1. enqueue -> INSERT
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_persists_queued_row(self, store, tmp_path):
        payload = _make_payload(tmp_path, "job-1")
        store.enqueue("job-1", "application/pdf", payload)

        row = store.get("job-1")
        assert row is not None
        assert row.status == STATUS_QUEUED
        assert row.mime == "application/pdf"
        assert row.payload_path == payload
        assert row.attempts == 0
        assert row.result is None

    def test_enqueue_rejects_empty_fields(self, store, tmp_path):
        payload = _make_payload(tmp_path, "job-x")
        with pytest.raises(ValueError):
            store.enqueue("", "application/pdf", payload)
        with pytest.raises(ValueError):
            store.enqueue("job-x", "", payload)
        with pytest.raises(ValueError):
            store.enqueue("job-x", "application/pdf", "")

    def test_get_unknown_job_returns_none(self, store):
        assert store.get("nope") is None


# ---------------------------------------------------------------------------
# 2. claim() is single-winner under concurrency
# ---------------------------------------------------------------------------


class TestClaim:
    def test_claim_marks_processing(self, store, tmp_path):
        store.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        claimed = store.claim()
        assert claimed is not None
        assert claimed.job_id == "job-1"
        assert claimed.status == STATUS_PROCESSING
        assert store.get("job-1").status == STATUS_PROCESSING

    def test_claim_empty_store_returns_none(self, store):
        assert store.claim() is None

    def test_claim_fifo_order(self, store, tmp_path):
        store.enqueue("job-a", "application/pdf", _make_payload(tmp_path, "job-a"))
        time.sleep(0.01)
        store.enqueue("job-b", "application/pdf", _make_payload(tmp_path, "job-b"))
        first = store.claim()
        assert first.job_id == "job-a"

    def test_concurrent_claim_single_winner(self, tmp_path):
        # One job, many threads racing to claim it -> exactly one winner.
        shared = ParseJobStore(tmp_path / "shared")
        shared.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))

        results: list = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            # Each thread uses its own store object (own connection) on the
            # same DB file -- the realistic multi-worker scenario.
            s = ParseJobStore(tmp_path / "shared")
            barrier.wait()
            claimed = s.claim()
            with lock:
                results.append(claimed)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [r for r in results if r is not None]
        assert len(winners) == 1, f"exactly one claim must win, got {len(winners)}"
        assert winners[0].job_id == "job-1"


# ---------------------------------------------------------------------------
# 3. orphan recovery after a (simulated) worker restart
# ---------------------------------------------------------------------------


class TestOrphanRecovery:
    def test_recover_orphans_requeues_processing(self, store, tmp_path):
        store.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        store.claim()  # -> processing, then the "worker crashes"
        assert store.get("job-1").status == STATUS_PROCESSING

        # New store object simulates a fresh process/worker boot on same DB.
        rebooted = ParseJobStore(store._dir)
        recovered = rebooted.recover_orphans()
        assert recovered == 1
        assert rebooted.get("job-1").status == STATUS_QUEUED

        # The recovered job can be claimed and finished by the new worker.
        again = rebooted.claim()
        assert again is not None and again.job_id == "job-1"
        rebooted.mark_done("job-1", {"markdown": "# ok"})
        assert rebooted.get("job-1").status == STATUS_DONE

    def test_recover_orphans_idempotent_noop(self, store, tmp_path):
        store.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        # No processing rows -> nothing to recover.
        assert store.recover_orphans() == 0
        assert store.get("job-1").status == STATUS_QUEUED


# ---------------------------------------------------------------------------
# 4. result UPDATE (done / failed)
# ---------------------------------------------------------------------------


class TestResults:
    def test_mark_done_records_result(self, store, tmp_path):
        store.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        store.claim()
        store.mark_done("job-1", {"markdown": "# hi", "metadata": {}, "stats": {}})

        row = store.get("job-1")
        assert row.status == STATUS_DONE
        assert row.result == {"markdown": "# hi", "metadata": {}, "stats": {}}
        assert row.error == ""

    def test_mark_failed_records_error_and_increments_attempts(self, store, tmp_path):
        store.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        store.claim()
        store.mark_failed("job-1", "boom")

        row = store.get("job-1")
        assert row.status == STATUS_FAILED
        assert row.error == "boom"
        assert row.attempts == 1


# ---------------------------------------------------------------------------
# 5. TTL cleanup removes finished jobs + payloads, preserves in-flight
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_removes_expired_done_and_payload(self, store, tmp_path):
        payload = _make_payload(tmp_path, "job-done")
        store.enqueue("job-done", "application/pdf", payload)
        store.claim()
        store.mark_done("job-done", {"markdown": "x"})

        # ttl=0 -> the just-finished job is past its TTL.
        removed = store.cleanup_expired(ttl_sec=0)
        assert removed == 1
        assert store.get("job-done") is None
        assert not Path(payload).parent.exists()

    def test_cleanup_preserves_queued_and_processing(self, store, tmp_path):
        store.enqueue("q", "application/pdf", _make_payload(tmp_path, "q"))
        store.enqueue("p", "application/pdf", _make_payload(tmp_path, "p"))
        # leave 'q' queued; claim 'p' twice path: claim oldest first ('q'),
        # so enqueue order matters -> claim 'q', leaving 'p' queued.
        store.claim()  # claims 'q' -> processing

        removed = store.cleanup_expired(ttl_sec=0)
        assert removed == 0  # nothing done/failed
        assert store.get("q").status == STATUS_PROCESSING
        assert store.get("p").status == STATUS_QUEUED

    def test_queued_count(self, store, tmp_path):
        assert store.queued_count() == 0
        store.enqueue("a", "application/pdf", _make_payload(tmp_path, "a"))
        store.enqueue("b", "application/pdf", _make_payload(tmp_path, "b"))
        assert store.queued_count() == 2
        store.claim()
        assert store.queued_count() == 1


# ---------------------------------------------------------------------------
# 6. Content idempotency (P1-1, defect C): dedup identical in-flight jobs
# ---------------------------------------------------------------------------


class TestContentIdempotency:
    def test_enqueue_idempotent_inserts_when_new(self, store, tmp_path):
        eff_id, dedup = store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        assert eff_id == "job-1"
        assert dedup is False
        row = store.get("job-1")
        assert row is not None
        assert row.content_hash == "hash-abc"
        assert row.status == STATUS_QUEUED

    def test_enqueue_idempotent_dedups_same_inflight_hash(self, store, tmp_path):
        # First submission inserts.
        store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        # Second submission of the same content+mime attaches to the existing job.
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "application/pdf", _make_payload(tmp_path, "job-2"), "hash-abc",
        )
        assert eff_id == "job-1"
        assert dedup is True
        assert store.get("job-2") is None  # no second row inserted
        assert store.queued_count() == 1

    def test_dedup_holds_while_processing(self, store, tmp_path):
        store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        store.claim()  # job-1 -> processing
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "application/pdf", _make_payload(tmp_path, "job-2"), "hash-abc",
        )
        assert dedup is True
        assert eff_id == "job-1"

    def test_done_job_does_not_dedup(self, store, tmp_path):
        store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        store.claim()
        store.mark_done("job-1", {"markdown": "x"})
        # Once finished, the same content may be re-submitted as a fresh job
        # (re-processing is allowed; only in-flight work dedups).
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "application/pdf", _make_payload(tmp_path, "job-2"), "hash-abc",
        )
        assert dedup is False
        assert eff_id == "job-2"

    def test_different_mime_same_hash_not_deduped(self, store, tmp_path):
        store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "text/csv", _make_payload(tmp_path, "job-2"), "hash-abc",
        )
        assert dedup is False
        assert eff_id == "job-2"

    def test_different_hash_not_deduped(self, store, tmp_path):
        store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "application/pdf", _make_payload(tmp_path, "job-2"), "hash-def",
        )
        assert dedup is False
        assert eff_id == "job-2"

    def test_empty_hash_falls_back_to_plain_enqueue(self, store, tmp_path):
        eff_id, dedup = store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "",
        )
        assert dedup is False
        assert eff_id == "job-1"
        assert store.get("job-1").content_hash is None

    def test_find_active_by_hash(self, store, tmp_path):
        assert store.find_active_by_hash("hash-abc", "application/pdf") is None
        store.enqueue_idempotent(
            "job-1", "application/pdf", _make_payload(tmp_path, "job-1"), "hash-abc",
        )
        found = store.find_active_by_hash("hash-abc", "application/pdf")
        assert found is not None and found.job_id == "job-1"

    def test_legacy_null_hash_never_matches(self, store, tmp_path):
        # A plain enqueue (no hash) leaves content_hash NULL.
        store.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        assert store.get("job-1").content_hash is None
        # A subsequent hashed submission must NOT attach to the legacy NULL row.
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "application/pdf", _make_payload(tmp_path, "job-2"), "hash-abc",
        )
        assert dedup is False
        assert eff_id == "job-2"


# ---------------------------------------------------------------------------
# 7. Backward-compatible, idempotent content_hash migration
# ---------------------------------------------------------------------------


class TestContentHashMigration:
    def test_migration_idempotent_double_boot(self, tmp_path):
        # First boot creates the schema (with content_hash + index).
        s1 = ParseJobStore(tmp_path / "jobs")
        s1.enqueue("job-1", "application/pdf", _make_payload(tmp_path, "job-1"))
        # Second boot on the SAME db must not error (ADD COLUMN already applied).
        s2 = ParseJobStore(tmp_path / "jobs")
        assert s2.get("job-1") is not None
        assert s2.get("job-1").content_hash is None

    def test_migration_adds_column_to_legacy_db(self, tmp_path):
        """A DB created WITHOUT content_hash boots cleanly and gains the column.

        Simulates an existing pre-P1-1 store: create the old table shape by
        hand, then open it with the current code and verify the migration adds
        the column, preserves the legacy row, and dedup works afterwards.
        """
        import sqlite3

        db_dir = tmp_path / "legacy"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "parse_jobs.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE parse_jobs (
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
            "INSERT INTO parse_jobs"
            " (job_id, status, mime, payload_path, created_at, updated_at)"
            " VALUES ('legacy-1', 'queued', 'application/pdf', '/x', 1.0, 1.0)"
        )
        conn.commit()
        conn.close()

        # Open with current code -> migration runs, legacy row survives.
        store = ParseJobStore(db_dir)
        legacy = store.get("legacy-1")
        assert legacy is not None
        assert legacy.content_hash is None  # legacy row keeps NULL
        # Dedup works on new submissions despite the legacy NULL row.
        eff_id, dedup = store.enqueue_idempotent(
            "job-2", "application/pdf", _make_payload(tmp_path, "job-2"), "hash-abc",
        )
        assert dedup is False and eff_id == "job-2"


# ---------------------------------------------------------------------------
# 8. Queue metrics (P2-2): per-status counts via a single GROUP BY
# ---------------------------------------------------------------------------


class TestCounts:
    def test_counts_empty_store(self, store):
        assert store.counts() == {
            STATUS_QUEUED: 0,
            STATUS_PROCESSING: 0,
            STATUS_DONE: 0,
            STATUS_FAILED: 0,
        }

    def test_counts_reflects_lifecycle(self, store, tmp_path):
        store.enqueue("q1", "application/pdf", _make_payload(tmp_path, "q1"))
        store.enqueue("q2", "application/pdf", _make_payload(tmp_path, "q2"))
        store.enqueue("p1", "application/pdf", _make_payload(tmp_path, "p1"))
        store.claim()  # oldest (q1) -> processing
        counts = store.counts()
        assert counts[STATUS_PROCESSING] == 1
        assert counts[STATUS_QUEUED] == 2

        store.mark_done("q1", {"markdown": "x"})
        counts = store.counts()
        assert counts[STATUS_DONE] == 1
        assert counts[STATUS_QUEUED] == 2
        assert counts[STATUS_PROCESSING] == 0
