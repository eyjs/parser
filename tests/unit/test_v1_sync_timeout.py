"""Tests for /v1/parse/sync timeout behaviour (task-004)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app(tmp_path: Path):
    from docforge.web.app import create_app

    app = create_app(upload_dir=tmp_path / "uploads")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestParseSyncTimeout:
    """Verify that /v1/parse/sync returns 408 on timeout."""

    def test_timeout_returns_408(self, client, tmp_path: Path) -> None:
        """When parse_pdf exceeds _SYNC_TIMEOUT, the route returns 408."""
        # Create a dummy PDF file
        pdf_bytes = b"%PDF-1.4 dummy content"

        def _slow_parse(pdf_path):
            time.sleep(5)  # Simulate slow parsing

        import docforge.web.v1_routes as v1_mod
        original_timeout = v1_mod._SYNC_TIMEOUT

        try:
            # Set a very short timeout for testing
            v1_mod._SYNC_TIMEOUT = 0.1

            with patch("docforge.usecases.parse_pdf.parse_pdf", side_effect=_slow_parse):
                from io import BytesIO
                data = {
                    "file": (BytesIO(pdf_bytes), "test.pdf", "application/pdf"),
                }
                resp = client.post(
                    "/v1/parse/sync",
                    data=data,
                    content_type="multipart/form-data",
                )

            body = json.loads(resp.data)
            assert resp.status_code == 408
            assert body["success"] is False
            assert body["error"]["code"] == "REQUEST_TIMEOUT"
        finally:
            v1_mod._SYNC_TIMEOUT = original_timeout

    def test_normal_parse_returns_200(self, client) -> None:
        """When parse_pdf completes normally, the route returns 200."""
        fake_result = MagicMock()
        fake_result.markdown = "# Hello"
        fake_result.metadata = MagicMock()
        fake_result.stats = MagicMock()

        with patch("docforge.usecases.parse_pdf.parse_pdf", return_value=fake_result), \
             patch("docforge.web.v1_routes._serialize", return_value={}):
            from io import BytesIO
            data = {
                "file": (BytesIO(b"%PDF-1.4 dummy"), "test.pdf", "application/pdf"),
            }
            resp = client.post(
                "/v1/parse/sync",
                data=data,
                content_type="multipart/form-data",
            )

        body = json.loads(resp.data)
        assert resp.status_code == 200
        assert body["success"] is True
        assert body["data"]["markdown"] == "# Hello"

    def test_health_check_unaffected(self, client) -> None:
        """GET /v1/health should still work regardless of executor state."""
        resp = client.get("/v1/health")
        body = json.loads(resp.data)
        assert resp.status_code == 200
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
