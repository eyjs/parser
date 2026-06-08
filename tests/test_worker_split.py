"""Tests for the parse-worker process split + backpressure (defects A & B).

Covers the execution-model changes only -- the job_store queue schema and
``claim()`` logic are unchanged and exercised by ``test_job_store.py``.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import pytest

from docforge.web import async_worker, v1_routes
from docforge.web.app import create_app
from docforge.web.job_store import ParseJobStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    app = create_app(upload_dir=tmp_path / "uploads")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def async_store_dir(tmp_path, monkeypatch):
    """Fresh durable store per test; reset the module singletons.

    Default leaves DOCFORGE_INPROC_WORKER unset (=> 0), matching the new
    production default where the web process does NOT spawn a parse worker.
    """
    store_dir = tmp_path / "async_store"
    monkeypatch.setenv("DOCFORGE_ASYNC_STORE_DIR", str(store_dir))
    monkeypatch.delenv("DOCFORGE_INPROC_WORKER", raising=False)
    monkeypatch.setattr(v1_routes, "_job_store", None, raising=False)
    monkeypatch.setattr(v1_routes, "_async_worker_started", False, raising=False)
    yield store_dir
    monkeypatch.setattr(v1_routes, "_job_store", None, raising=False)
    monkeypatch.setattr(v1_routes, "_async_worker_started", False, raising=False)


def _enqueue(client, name="bp.md", mime="text/markdown", body=b"# bp\n\nhello"):
    data = {"file": (io.BytesIO(body), name, mime)}
    return client.post(
        "/v1/parse/async", data=data, content_type="multipart/form-data",
    )


# ---------------------------------------------------------------------------
# Config resolution / validation (defect D guard)
# ---------------------------------------------------------------------------


class TestConfigResolution:
    def test_resolve_positive_int_falls_back_on_zero(self):
        assert async_worker.resolve_positive_int("0", 4) == 4

    def test_resolve_positive_int_falls_back_on_negative(self):
        assert async_worker.resolve_positive_int("-3", 4) == 4

    def test_resolve_positive_int_falls_back_on_garbage(self):
        assert async_worker.resolve_positive_int("nope", 7) == 7

    def test_resolve_positive_int_falls_back_on_none(self):
        assert async_worker.resolve_positive_int(None, 2) == 2

    def test_resolve_positive_int_accepts_valid(self):
        assert async_worker.resolve_positive_int("9", 4) == 9

    def test_default_must_be_positive(self):
        with pytest.raises(ValueError):
            async_worker.resolve_positive_int("5", 0)

    def test_parse_workers_zero_coerced(self, monkeypatch):
        monkeypatch.setenv("DOCFORGE_PARSE_WORKERS", "0")
        assert async_worker.parse_workers() >= 1

    def test_queue_max_scales_with_workers(self, monkeypatch):
        monkeypatch.setenv("DOCFORGE_PARSE_WORKERS", "10")
        monkeypatch.delenv("DOCFORGE_QUEUE_MAX", raising=False)
        # default = max(8, 2*workers) = 20
        assert async_worker.queue_max() == 20

    def test_inproc_worker_default_off(self, monkeypatch):
        monkeypatch.delenv("DOCFORGE_INPROC_WORKER", raising=False)
        assert async_worker.inproc_worker_enabled() is False

    def test_inproc_worker_on(self, monkeypatch):
        monkeypatch.setenv("DOCFORGE_INPROC_WORKER", "1")
        assert async_worker.inproc_worker_enabled() is True


# ---------------------------------------------------------------------------
# Backpressure: 503 + Retry-After + QUEUE_FULL (defect B)
# ---------------------------------------------------------------------------


class TestBackpressure:
    def test_room_in_queue_returns_202(self, client, async_store_dir, monkeypatch):
        monkeypatch.setenv("DOCFORGE_QUEUE_MAX", "5")
        resp = _enqueue(client)
        assert resp.status_code == 202
        assert resp.get_json()["data"]["status"] == "queued"

    def test_queue_full_returns_503_with_retry_after(
        self, client, async_store_dir, monkeypatch,
    ):
        monkeypatch.setenv("DOCFORGE_QUEUE_MAX", "2")
        monkeypatch.setenv("DOCFORGE_RETRY_AFTER_SEC", "7")

        # Fill the queue to the ceiling (2 queued jobs). Distinct bodies so each
        # is a separate document -- content idempotency (P1-1) dedups identical
        # bytes, which is orthogonal to backpressure (distinct work filling up).
        assert _enqueue(client, name="a.md", body=b"# a\n\nalpha").status_code == 202
        assert _enqueue(client, name="b.md", body=b"# b\n\nbravo").status_code == 202

        # The next submit must be rejected with backpressure.
        resp = _enqueue(client, name="c.md", body=b"# c\n\ncharlie")
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "7"
        body = resp.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "QUEUE_FULL"

    def test_queue_full_boundary_is_at_ceiling(
        self, client, async_store_dir, monkeypatch,
    ):
        """At exactly QUEUE_MAX queued jobs the next submit is rejected;
        below the ceiling it is accepted."""
        monkeypatch.setenv("DOCFORGE_QUEUE_MAX", "3")
        for i in range(3):
            # Distinct bodies per slot (idempotency dedups identical bytes).
            body = f"# doc {i}\n\nbody {i}".encode()
            assert _enqueue(client, name=f"{i}.md", body=body).status_code == 202
        assert _enqueue(client, name="over.md", body=b"# over\n\nover").status_code == 503


# ---------------------------------------------------------------------------
# Web no-spawn (defect A): the web process must not start a parse thread
# ---------------------------------------------------------------------------


class TestWebNoSpawn:
    def test_enqueue_does_not_spawn_inproc_worker(
        self, client, async_store_dir, monkeypatch,
    ):
        monkeypatch.delenv("DOCFORGE_INPROC_WORKER", raising=False)
        resp = _enqueue(client)
        assert resp.status_code == 202
        # The legacy in-proc worker thread must NOT have been started.
        assert v1_routes._async_worker_started is False
        # And the job stays queued (no in-proc consumer to process it).
        job_id = resp.get_json()["data"]["job_id"]
        poll = client.get(f"/v1/parse/async/{job_id}")
        assert poll.status_code == 200
        assert poll.get_json()["data"]["status"] == "queued"

    def test_inproc_fallback_spawns_when_enabled(
        self, client, async_store_dir, monkeypatch,
    ):
        monkeypatch.setenv("DOCFORGE_INPROC_WORKER", "1")
        resp = _enqueue(client)
        assert resp.status_code == 202
        assert v1_routes._async_worker_started is True


# ---------------------------------------------------------------------------
# Concurrent claim singularity (multiprocess-safe contract)
# ---------------------------------------------------------------------------


class TestConcurrentClaim:
    def test_concurrent_claims_process_each_job_once(self, tmp_path):
        """N concurrent claimers against a seeded store: every job is claimed
        exactly once and no job is claimed twice."""
        store_dir = tmp_path / "claim_store"
        store = ParseJobStore(store_dir)

        n_jobs = 40
        for i in range(n_jobs):
            store.enqueue(f"job-{i}", "text/markdown", str(store_dir / f"{i}.md"))

        claimed: list[str] = []

        def claim_all() -> list[str]:
            # Each thread uses its own thread-local connection (store pattern).
            got: list[str] = []
            while True:
                row = store.claim()
                if row is None:
                    break
                got.append(row.job_id)
            return got

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(claim_all) for _ in range(8)]
            for f in futures:
                claimed.extend(f.result())

        # Every job claimed exactly once: no duplicates, full coverage.
        assert len(claimed) == n_jobs
        assert len(set(claimed)) == n_jobs
        assert set(claimed) == {f"job-{i}" for i in range(n_jobs)}


# ---------------------------------------------------------------------------
# recover_orphans idempotency
# ---------------------------------------------------------------------------


class TestRecoverOrphansIdempotent:
    def test_recover_then_recover_again_is_noop(self, tmp_path):
        store_dir = tmp_path / "orphan_store"
        store = ParseJobStore(store_dir)

        store.enqueue("orphan-1", "text/markdown", str(store_dir / "1.md"))
        # Simulate a worker that claimed then crashed (row left 'processing').
        claimed = store.claim()
        assert claimed is not None and claimed.job_id == "orphan-1"

        first = store.recover_orphans()
        assert first == 1  # the orphaned job is requeued

        second = store.recover_orphans()
        assert second == 0  # nothing left processing => no-op

    def test_recover_orphans_clean_store_is_zero(self, tmp_path):
        store = ParseJobStore(tmp_path / "clean_store")
        assert store.recover_orphans() == 0
