"""DocForge v1 API 실서버 테스트.

실제 Flask 서버를 띄우고 HTTP 요청을 보내서 전체 파싱 파이프라인을 검증한다.
pytest -m live 으로 실행.
"""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest
import requests

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_PDF = FIXTURES_DIR / "test.pdf"
SERVER_PORT = 5051
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


def _run_flask(port: int) -> None:
    """Flask 서버를 별도 프로세스로 실행."""
    import os
    os.environ["DOCFORGE_INTERNAL_KEY"] = "test-live-key"
    os.environ["DOCFORGE_ALLOWED_ORIGINS"] = "http://localhost:3000"

    from docforge.web.app import create_app
    app = create_app()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


@pytest.fixture(scope="module")
def live_server():
    """모듈 스코프 Flask 서버 fixture."""
    proc = multiprocessing.Process(target=_run_flask, args=(SERVER_PORT,), daemon=True)
    proc.start()

    for _ in range(30):
        try:
            r = requests.get(
                f"{SERVER_URL}/v1/health",
                headers={"X-Internal-Key": "test-live-key"},
                timeout=2,
            )
            if r.status_code == 200:
                break
        except requests.ConnectionError:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("Flask 서버 시작 실패 (15초 타임아웃)")

    yield SERVER_URL

    proc.terminate()
    proc.join(timeout=5)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestHealthLive:
    def test_health_without_auth_returns_401(self, live_server):
        resp = requests.get(f"{live_server}/v1/health")
        assert resp.status_code == 401

    def test_health_returns_ok(self, live_server):
        resp = requests.get(
            f"{live_server}/v1/health",
            headers={"X-Internal-Key": "test-live-key"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"

    def test_health_with_auth(self, live_server):
        resp = requests.get(
            f"{live_server}/v1/health",
            headers={"X-Internal-Key": "test-live-key"},
        )
        assert resp.status_code == 200

    def test_health_rejects_wrong_key(self, live_server):
        resp = requests.get(
            f"{live_server}/v1/health",
            headers={"X-Internal-Key": "wrong-key"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestAuthLive:
    def test_v1_requires_key(self, live_server):
        """X-Internal-Key가 설정된 상태에서 키 없이 요청하면 401."""
        resp = requests.post(f"{live_server}/v1/parse/sync")
        assert resp.status_code == 401

    def test_v1_accepts_correct_key(self, live_server):
        """올바른 키로 요청하면 인증 통과 (파일 미포함이므로 400)."""
        resp = requests.post(
            f"{live_server}/v1/parse/sync",
            headers={"X-Internal-Key": "test-live-key"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "NO_FILE"

    def test_legacy_routes_skip_auth(self, live_server):
        """레거시 / 경로는 인증을 건너뛴다."""
        resp = requests.get(f"{live_server}/")
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Parse sync — validation
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestParseSyncValidation:
    HEADERS = {"X-Internal-Key": "test-live-key"}

    def test_no_file_returns_400(self, live_server):
        resp = requests.post(
            f"{live_server}/v1/parse/sync",
            headers=self.HEADERS,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "NO_FILE"

    def test_wrong_mime_returns_415(self, live_server):
        resp = requests.post(
            f"{live_server}/v1/parse/sync",
            headers=self.HEADERS,
            files={"file": ("test.txt", b"not a pdf", "text/plain")},
        )
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    def test_options_returns_204(self, live_server):
        resp = requests.options(
            f"{live_server}/v1/parse/sync",
            headers=self.HEADERS,
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Parse sync — 실제 파싱
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestParseSyncReal:
    HEADERS = {"X-Internal-Key": "test-live-key"}

    def test_parse_real_pdf(self, live_server):
        """실제 PDF를 보내서 마크다운이 반환되는지 검증."""
        assert TEST_PDF.exists(), f"테스트 PDF 없음: {TEST_PDF}"

        with open(TEST_PDF, "rb") as f:
            resp = requests.post(
                f"{live_server}/v1/parse/sync",
                headers=self.HEADERS,
                files={"file": ("test.pdf", f, "application/pdf")},
                timeout=120,
            )

        assert resp.status_code == 200, f"파싱 실패: {resp.text}"
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
        assert isinstance(body["data"]["markdown"], str)
        assert len(body["data"]["markdown"]) > 0
        assert isinstance(body["data"]["metadata"], dict)
        assert isinstance(body["data"]["stats"], dict)

    def test_parse_response_structure(self, live_server):
        """응답 구조가 DocForgeResponse 스키마와 일치하는지 검증."""
        with open(TEST_PDF, "rb") as f:
            resp = requests.post(
                f"{live_server}/v1/parse/sync",
                headers=self.HEADERS,
                files={"file": ("test.pdf", f, "application/pdf")},
                timeout=120,
            )

        body = resp.json()
        assert set(body.keys()) == {"success", "data"}
        assert set(body["data"].keys()) == {"markdown", "metadata", "stats"}

    def test_korean_filename(self, live_server):
        """한글 파일명이 정상 처리되는지 검증."""
        with open(TEST_PDF, "rb") as f:
            resp = requests.post(
                f"{live_server}/v1/parse/sync",
                headers=self.HEADERS,
                files={"file": ("테스트_문서.pdf", f, "application/pdf")},
                timeout=120,
            )

        assert resp.status_code == 200

    def test_path_traversal_filename(self, live_server):
        """경로 순회 파일명이 안전하게 처리되는지 검증."""
        with open(TEST_PDF, "rb") as f:
            resp = requests.post(
                f"{live_server}/v1/parse/sync",
                headers=self.HEADERS,
                files={"file": ("../../../etc/passwd.pdf", f, "application/pdf")},
                timeout=120,
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestCORSLive:
    def test_cors_with_allowed_origin(self, live_server):
        resp = requests.get(
            f"{live_server}/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "X-Internal-Key": "test-live-key",
            },
        )
        assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"

    def test_cors_without_origin(self, live_server):
        resp = requests.get(
            f"{live_server}/v1/health",
            headers={"X-Internal-Key": "test-live-key"},
        )
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_cors_disallowed_origin(self, live_server):
        resp = requests.get(
            f"{live_server}/v1/health",
            headers={
                "Origin": "http://evil.com",
                "X-Internal-Key": "test-live-key",
            },
        )
        assert "Access-Control-Allow-Origin" not in resp.headers
