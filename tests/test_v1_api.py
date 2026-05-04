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
    def test_no_auth_when_key_not_configured(self, client):
        """When DOCFORGE_INTERNAL_KEY is not set, auth is skipped."""
        resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_auth_missing_key_returns_401(self, app):
        """When key is configured but request has no header, return 401."""
        with patch.dict(os.environ, {"DOCFORGE_INTERNAL_KEY": "test-secret"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads2",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get("/v1/health")
                assert resp.status_code == 401
                data = resp.get_json()
                assert data["success"] is False
                assert data["error"]["code"] == "UNAUTHORIZED"

    def test_auth_wrong_key_returns_401(self, app):
        """When key is configured but request has wrong key, return 401."""
        with patch.dict(os.environ, {"DOCFORGE_INTERNAL_KEY": "test-secret"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads3",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={"X-Internal-Key": "wrong-key"},
                )
                assert resp.status_code == 401

    def test_auth_correct_key_passes(self, app):
        """When correct key is provided, request proceeds."""
        with patch.dict(os.environ, {"DOCFORGE_INTERNAL_KEY": "test-secret"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads4",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={"X-Internal-Key": "test-secret"},
                )
                assert resp.status_code == 200

    def test_auth_skipped_for_legacy_routes(self, app):
        """Legacy /api/ routes are not affected by auth."""
        with patch.dict(os.environ, {"DOCFORGE_INTERNAL_KEY": "test-secret"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "uploads5",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                # Legacy route (no auth required)
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
        data = {"file": (io.BytesIO(b"not a pdf"), "test.txt", "text/plain")}
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
