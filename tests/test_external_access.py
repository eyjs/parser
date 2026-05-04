"""Tests for external access via Cloudflare Tunnel.

Validates the CF-Connecting-IP based internal/external request routing
and CORS behavior for external clients.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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
# Internal / External request routing
# ---------------------------------------------------------------------------


class TestInternalExternalRouting:
    """CF-Connecting-IP header determines internal vs external request."""

    def test_internal_no_cf_header_passes(self, client):
        """No CF-Connecting-IP => internal request => auth skipped."""
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_internal_with_key_still_works(self, client):
        """Internal request with an X-Internal-Key header still succeeds (key ignored)."""
        resp = client.get(
            "/v1/health",
            headers={"X-Internal-Key": "any-value"},
        )
        assert resp.status_code == 200

    def test_external_with_cf_header_allowed_by_default(self, client):
        """CF-Connecting-IP present => external, but DOCFORGE_EXTERNAL_AUTH defaults to false."""
        resp = client.get(
            "/v1/health",
            headers={"CF-Connecting-IP": "198.51.100.42"},
        )
        assert resp.status_code == 200

    def test_external_without_key_allowed_when_auth_disabled(self, app):
        """External request without any key is allowed when auth is explicitly disabled."""
        with patch.dict(os.environ, {"DOCFORGE_EXTERNAL_AUTH": "false"}):
            test_app = create_app(
                upload_dir=Path(app.config["UPLOAD_DIR"]).parent / "ext1",
            )
            test_app.config["TESTING"] = True
            with test_app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={"CF-Connecting-IP": "198.51.100.42"},
                )
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# External auth enabled scenarios
# ---------------------------------------------------------------------------


class TestExternalAuthEnabled:
    """When DOCFORGE_EXTERNAL_AUTH=true, external requests require X-Internal-Key."""

    @pytest.fixture
    def auth_app(self, tmp_path):
        with patch.dict(os.environ, {
            "DOCFORGE_EXTERNAL_AUTH": "true",
            "DOCFORGE_INTERNAL_KEY": "ext-secret-key",
        }):
            app = create_app(upload_dir=tmp_path / "ext_auth_uploads")
            app.config["TESTING"] = True
            yield app

    @pytest.fixture
    def auth_client(self, auth_app):
        return auth_app.test_client()

    def test_external_no_key_returns_401(self, auth_client):
        """External request without key => 401."""
        resp = auth_client.get(
            "/v1/health",
            headers={"CF-Connecting-IP": "198.51.100.42"},
        )
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_external_wrong_key_returns_401(self, auth_client):
        """External request with wrong key => 401."""
        resp = auth_client.get(
            "/v1/health",
            headers={
                "CF-Connecting-IP": "198.51.100.42",
                "X-Internal-Key": "wrong-key",
            },
        )
        assert resp.status_code == 401

    def test_external_correct_key_returns_200(self, auth_client):
        """External request with correct key => 200."""
        resp = auth_client.get(
            "/v1/health",
            headers={
                "CF-Connecting-IP": "198.51.100.42",
                "X-Internal-Key": "ext-secret-key",
            },
        )
        assert resp.status_code == 200

    def test_internal_still_skips_auth(self, auth_client):
        """Even with EXTERNAL_AUTH=true, internal requests (no CF header) skip auth."""
        resp = auth_client.get("/v1/health")
        assert resp.status_code == 200

    def test_external_auth_enabled_no_key_configured_allows(self, tmp_path):
        """EXTERNAL_AUTH=true but INTERNAL_KEY empty => allows (safety valve)."""
        with patch.dict(os.environ, {
            "DOCFORGE_EXTERNAL_AUTH": "true",
            "DOCFORGE_INTERNAL_KEY": "",
        }):
            app = create_app(upload_dir=tmp_path / "ext_nokey")
            app.config["TESTING"] = True
            with app.test_client() as c:
                resp = c.get(
                    "/v1/health",
                    headers={"CF-Connecting-IP": "198.51.100.42"},
                )
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCORS:
    """CORS behavior for external requests."""

    def test_options_preflight_returns_204(self, client):
        """OPTIONS request to /v1/parse/sync returns 204."""
        resp = client.options("/v1/parse/sync")
        assert resp.status_code == 204

    def test_cors_headers_present_with_origin(self, client):
        """When Origin header is present, CORS response headers are set."""
        resp = client.get(
            "/v1/health",
            headers={"Origin": "https://app.example.com"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"
        assert "X-Internal-Key" in resp.headers.get("Access-Control-Allow-Headers", "")

    def test_cors_headers_absent_without_origin(self, client):
        """When no Origin header (e.g., exe client), no CORS headers are set."""
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_cors_restricted_origins(self, tmp_path):
        """When DOCFORGE_ALLOWED_ORIGINS is set, only listed origins get CORS headers."""
        with patch.dict(os.environ, {
            "DOCFORGE_ALLOWED_ORIGINS": "https://allowed.example.com",
        }):
            app = create_app(upload_dir=tmp_path / "cors_restricted")
            app.config["TESTING"] = True
            with app.test_client() as c:
                # Allowed origin
                resp = c.get(
                    "/v1/health",
                    headers={"Origin": "https://allowed.example.com"},
                )
                assert resp.headers.get("Access-Control-Allow-Origin") == "https://allowed.example.com"

                # Disallowed origin
                resp = c.get(
                    "/v1/health",
                    headers={"Origin": "https://evil.example.com"},
                )
                assert "Access-Control-Allow-Origin" not in resp.headers


# ---------------------------------------------------------------------------
# Regression: existing internal clients
# ---------------------------------------------------------------------------


class TestInternalClientRegression:
    """Ensure existing internal clients (KMS, ai-platform) are not broken."""

    def test_internal_client_with_key_still_works(self, client):
        """Internal client that still sends X-Internal-Key should not get errors."""
        resp = client.get(
            "/v1/health",
            headers={"X-Internal-Key": "some-old-key"},
        )
        assert resp.status_code == 200

    def test_internal_client_without_key_now_works(self, client):
        """Internal client that omits X-Internal-Key now succeeds (previously would fail if key was configured)."""
        resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_legacy_api_routes_unaffected(self, client):
        """Legacy /api/ routes remain accessible."""
        resp = client.get("/api/history")
        assert resp.status_code == 200
