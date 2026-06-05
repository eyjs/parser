"""Tests for DocForge /v1 API routes."""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from docforge.web.app import create_app


@pytest.fixture
def app(tmp_path):
    """Create a test app with a temporary upload dir."""
    app = create_app(upload_dir=tmp_path / "uploads")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["status"] == "ok"

    def test_health_has_version(self, client):
        resp = client.get("/v1/health")
        data = resp.get_json()
        assert "version" in data["data"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    """Auth tests — CF-Connecting-IP based internal/external routing.

    Without CF-Connecting-IP header the request is treated as internal
    (Docker network) and auth is always skipped.
    """

    def test_internal_request_skips_auth(self, client):
        """Internal request (no CF-Connecting-IP): auth skipped regardless of key config."""
        resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_internal_request_skips_auth_even_with_key_configured(self, app):
        """Internal request with DOCFORGE_INTERNAL_KEY set still skips auth."""
        with patch.dict(os.environ, {"DOCFORGE_INTERNAL_KEY": "test-secret"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads2",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                # No CF-Connecting-IP => internal => auth skipped
                resp = c.get("/v1/health")
                assert resp.status_code == 200

    def test_external_request_allowed_when_auth_disabled(self, app):
        """External request (CF-Connecting-IP present) with auth disabled: allowed."""
        with patch.dict(os.environ, {"DOCFORGE_EXTERNAL_AUTH": "false"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads3",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={"CF-Connecting-IP": "203.0.113.1"},
                )
                assert resp.status_code == 200

    def test_external_request_missing_key_returns_401_when_auth_enabled(self, app):
        """External request with auth enabled but no key header: 401."""
        with patch.dict(os.environ, {
            "DOCFORGE_EXTERNAL_AUTH": "true",
            "DOCFORGE_INTERNAL_KEY": "test-secret",
        }):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads4",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={"CF-Connecting-IP": "203.0.113.1"},
                )
                assert resp.status_code == 401
                data = resp.get_json()
                assert data["success"] is False
                assert data["error"]["code"] == "UNAUTHORIZED"

    def test_external_request_wrong_key_returns_401_when_auth_enabled(self, app):
        """External request with auth enabled and wrong key: 401."""
        with patch.dict(os.environ, {
            "DOCFORGE_EXTERNAL_AUTH": "true",
            "DOCFORGE_INTERNAL_KEY": "test-secret",
        }):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads5",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={
                        "CF-Connecting-IP": "203.0.113.1",
                        "X-Internal-Key": "wrong-key",
                    },
                )
                assert resp.status_code == 401

    def test_external_request_correct_key_passes_when_auth_enabled(self, app):
        """External request with auth enabled and correct key: 200."""
        with patch.dict(os.environ, {
            "DOCFORGE_EXTERNAL_AUTH": "true",
            "DOCFORGE_INTERNAL_KEY": "test-secret",
        }):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads6",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={
                        "CF-Connecting-IP": "203.0.113.1",
                        "X-Internal-Key": "test-secret",
                    },
                )
                assert resp.status_code == 200

    def test_auth_skipped_for_legacy_routes(self, app):
        """Legacy /api/ routes are not affected by auth."""
        with patch.dict(os.environ, {
            "DOCFORGE_EXTERNAL_AUTH": "true",
            "DOCFORGE_INTERNAL_KEY": "test-secret",
        }):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads7",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                # Legacy route — auth middleware only applies to /v1/
                resp = c.get("/api/history")
                assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Parse sync
# ---------------------------------------------------------------------------


class TestParseSync:
    def test_no_file_returns_400(self, client):
        resp = client.post("/v1/parse/sync")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]["code"] == "NO_FILE"

    def test_wrong_mime_returns_415(self, client):
        # application/zip은 지원 대상이 아님 (text/plain은 Step24에서 허용됨)
        data = {"file": (io.BytesIO(b"PK\x03\x04binary"), "test.zip", "application/zip")}
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 415
        body = resp.get_json()
        assert body["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    @patch("docforge.web.v1_routes._serialize")
    @patch("docforge.usecases.parse_pdf.parse_pdf")
    def test_parse_sync_success(self, mock_parse, mock_serialize, client):
        """Successful sync parse returns markdown and metadata."""
        mock_result = MagicMock()
        mock_result.markdown = "# Test Document\n\nHello world"
        mock_serialize.side_effect = [
            {"pages": 1},
            {"elapsed_ms": 100},
        ]
        mock_parse.return_value = mock_result

        pdf_content = b"%PDF-1.4 fake pdf content"
        data = {
            "file": (io.BytesIO(pdf_content), "test.pdf", "application/pdf"),
        }
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["markdown"] == "# Test Document\n\nHello world"
        assert "metadata" in body["data"]
        assert "stats" in body["data"]
        mock_parse.assert_called_once()

    @patch("docforge.usecases.parse_pdf.parse_pdf")
    def test_parse_sync_error_returns_500(self, mock_parse, client):
        """Parse failure returns 500 with generic error message."""
        mock_parse.side_effect = RuntimeError("OCR engine not available")

        pdf_content = b"%PDF-1.4 fake pdf content"
        data = {
            "file": (io.BytesIO(pdf_content), "test.pdf", "application/pdf"),
        }
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 500
        body = resp.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "PARSE_ERROR"
        assert "관리자에게 문의" in body["error"]["message"]


# ---------------------------------------------------------------------------
# CSV / Excel parse sync
# ---------------------------------------------------------------------------


class TestParseSyncCsv:
    """CSV 파일 파싱 API 테스트."""

    def test_csv_parse_success(self, client):
        csv_content = b"name,age,city\nAlice,30,Seoul\nBob,25,Busan\n"
        data = {
            "file": (io.BytesIO(csv_content), "data.csv", "text/csv"),
        }
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "Alice" in body["data"]["markdown"]
        assert "metadata" in body["data"]
        assert "stats" in body["data"]

    def test_csv_empty_file(self, client):
        data = {
            "file": (io.BytesIO(b""), "empty.csv", "text/csv"),
        }
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["markdown"] == ""


class TestParseSyncMarkdown:
    """Markdown/plain 텍스트 파싱 API 테스트 (Step24)."""

    def test_markdown_parse_success(self, client):
        md = b"# Title\n\n## Section\n\nbody text with section headers preserved.\n"
        data = {"file": (io.BytesIO(md), "doc.md", "text/markdown")}
        resp = client.post(
            "/v1/parse/sync", data=data, content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # pass-through: 섹션 헤더(#) 원문 보존
        assert "# Title" in body["data"]["markdown"]
        assert "## Section" in body["data"]["markdown"]
        assert body["data"]["metadata"]["format"] == "markdown"
        assert body["data"]["stats"]["char_count"] == len(md.decode("utf-8"))

    def test_plain_text_parse_success(self, client):
        txt = "안녕하세요\n보험 약관 텍스트\n".encode("utf-8")
        data = {"file": (io.BytesIO(txt), "약관.txt", "text/plain")}
        resp = client.post(
            "/v1/parse/sync", data=data, content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "보험 약관 텍스트" in body["data"]["markdown"]

    def test_markdown_cp949_fallback(self, client):
        # UTF-8 디코드 실패 시 cp949 폴백 (한글 레거시 인코딩)
        txt = "한글 약관".encode("cp949")
        data = {"file": (io.BytesIO(txt), "legacy.txt", "text/plain")}
        resp = client.post(
            "/v1/parse/sync", data=data, content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "한글 약관" in body["data"]["markdown"]

    def test_markdown_empty_file(self, client):
        data = {"file": (io.BytesIO(b""), "empty.md", "text/markdown")}
        resp = client.post(
            "/v1/parse/sync", data=data, content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["markdown"] == ""


class TestParseSyncExcel:
    """Excel 파일 파싱 API 테스트."""

    def _make_xlsx(self) -> bytes:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["name", "age"])
        ws.append(["Alice", 30])
        ws.append(["Bob", 25])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    def test_xlsx_parse_success(self, client):
        xlsx_content = self._make_xlsx()
        data = {
            "file": (
                io.BytesIO(xlsx_content),
                "data.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "Alice" in body["data"]["markdown"]
        assert "metadata" in body["data"]
        assert "stats" in body["data"]

    def test_xls_mime_accepted(self, client):
        """application/vnd.ms-excel MIME type is accepted."""
        xlsx_content = self._make_xlsx()
        data = {
            "file": (
                io.BytesIO(xlsx_content),
                "data.xls",
                "application/vnd.ms-excel",
            ),
        }
        resp = client.post(
            "/v1/parse/sync",
            data=data,
            content_type="multipart/form-data",
        )

        # Note: openpyxl may fail on .xls but MIME acceptance should work
        # For this test we just check it's not rejected at MIME level
        assert resp.status_code in (200, 500)  # 200 or parse error, not 415


# ---------------------------------------------------------------------------
# Durable async queue (Step19, G21) -- route level
# ---------------------------------------------------------------------------


@pytest.fixture
def async_store_dir(tmp_path, monkeypatch):
    """Point the durable async store at a temp dir and reset the singleton.

    The job store is a module-level singleton; reset it before and after each
    test so cases don't share a DB file.
    """
    from docforge.web import v1_routes

    store_dir = tmp_path / "async_store"
    monkeypatch.setenv("DOCFORGE_ASYNC_STORE_DIR", str(store_dir))
    # Reset the module singletons so each test gets a fresh store + worker
    # bound to its own on-disk DB (the worker thread is a module-global).
    monkeypatch.setattr(v1_routes, "_job_store", None, raising=False)
    monkeypatch.setattr(v1_routes, "_async_worker_started", False, raising=False)
    yield store_dir
    monkeypatch.setattr(v1_routes, "_job_store", None, raising=False)
    monkeypatch.setattr(v1_routes, "_async_worker_started", False, raising=False)


class TestAsyncDurableQueue:
    """G21: jobs survive a worker restart -- poll stays 200, never 404."""

    def _enqueue(self, client, name="durable.md", mime="text/markdown",
                 body=b"# durable\n\nhello"):
        data = {"file": (io.BytesIO(body), name, mime)}
        return client.post(
            "/v1/parse/async", data=data, content_type="multipart/form-data",
        )

    def test_enqueue_returns_202_with_job_id(self, client, async_store_dir):
        resp = self._enqueue(client)
        assert resp.status_code == 202
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["status"] == "queued"
        assert body["data"]["job_id"]

    def test_enqueue_persists_payload_to_durable_dir(self, client, async_store_dir):
        resp = self._enqueue(client)
        job_id = resp.get_json()["data"]["job_id"]
        # Payload is persisted under <store_dir>/<job_id>/ (NOT eagerly deleted).
        job_dir = async_store_dir / job_id
        assert job_dir.exists()
        assert any(job_dir.iterdir())

    def test_poll_unknown_job_returns_404(self, client, async_store_dir):
        resp = client.get("/v1/parse/async/does-not-exist")
        assert resp.status_code == 404
        assert resp.get_json()["error"]["code"] == "NOT_FOUND"

    def test_job_survives_restart_poll_stays_200(self, client, async_store_dir):
        """Core G21 contract: enqueue, simulate a worker restart, the job is
        still there (200, not 404) and is recovered to 'queued'."""
        from docforge.web import v1_routes
        from docforge.web.job_store import STATUS_PROCESSING, STATUS_QUEUED

        resp = self._enqueue(client)
        job_id = resp.get_json()["data"]["job_id"]

        # Simulate a worker that claimed the job and then crashed.
        store = v1_routes._get_job_store()
        claimed = store.claim()
        assert claimed is not None and claimed.job_id == job_id
        assert store.get(job_id).status == STATUS_PROCESSING

        # "Restart": drop the singleton so the next access rebuilds it from the
        # same on-disk DB and recovers orphans (as _ensure_async_worker does).
        v1_routes._job_store = None
        rebooted = v1_routes._get_job_store()
        rebooted.recover_orphans()

        # Poll must NOT 404 -- the durable job is still tracked.
        poll = client.get(f"/v1/parse/async/{job_id}")
        assert poll.status_code == 200
        assert poll.get_json()["data"]["status"] == STATUS_QUEUED

    def test_done_job_poll_returns_result(self, client, async_store_dir):
        from docforge.web import v1_routes

        resp = self._enqueue(client)
        job_id = resp.get_json()["data"]["job_id"]

        store = v1_routes._get_job_store()
        store.claim()
        store.mark_done(job_id, {"markdown": "# parsed", "metadata": {}, "stats": {}})

        poll = client.get(f"/v1/parse/async/{job_id}")
        assert poll.status_code == 200
        body = poll.get_json()
        assert body["success"] is True
        assert body["data"]["status"] == "done"
        assert body["data"]["markdown"] == "# parsed"

    def test_failed_job_poll_returns_error(self, client, async_store_dir):
        from docforge.web import v1_routes

        resp = self._enqueue(client)
        job_id = resp.get_json()["data"]["job_id"]

        store = v1_routes._get_job_store()
        store.claim()
        store.mark_failed(job_id, "boom")

        poll = client.get(f"/v1/parse/async/{job_id}")
        assert poll.status_code == 200
        body = poll.get_json()
        assert body["data"]["status"] == "failed"
        assert body["error"]["code"] == "PARSE_ERROR"

    def test_worker_processes_markdown_job_end_to_end(self, client, async_store_dir):
        """Real worker loop: enqueue a markdown doc, the background worker
        claims and parses it, poll eventually returns done with markdown."""
        import time as _t

        from docforge.web import v1_routes

        resp = self._enqueue(client, body=b"# Title\n\nbody text")
        job_id = resp.get_json()["data"]["job_id"]

        # _ensure_async_worker was started by the enqueue route; wait for done.
        deadline = _t.time() + 5.0
        status = None
        while _t.time() < deadline:
            poll = client.get(f"/v1/parse/async/{job_id}")
            assert poll.status_code == 200  # never 404 while job is live
            status = poll.get_json()["data"]["status"]
            if status == "done":
                break
            _t.sleep(0.05)

        assert status == "done", f"worker did not finish job (last status={status})"
        body = client.get(f"/v1/parse/async/{job_id}").get_json()
        assert "Title" in body["data"]["markdown"]
