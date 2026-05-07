"""V1 API routes for DocForge -- stable, versioned, authenticated."""

from __future__ import annotations

import atexit
import dataclasses
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from docforge.web.routes import _safe_filename

logger = logging.getLogger(__name__)

v1_bp = Blueprint("v1", __name__, url_prefix="/v1")

# ---------------------------------------------------------------------------
# Shared executor for synchronous parse requests
# ---------------------------------------------------------------------------

_SYNC_TIMEOUT = 280  # seconds -- must be < Gunicorn's --timeout (300)

_sync_executor: ThreadPoolExecutor | None = None
_sync_executor_lock = threading.Lock()


def _get_sync_executor() -> ThreadPoolExecutor:
    """Return (or lazily create) the /v1/parse/sync thread pool."""
    global _sync_executor
    if _sync_executor is not None:
        return _sync_executor
    with _sync_executor_lock:
        if _sync_executor is not None:
            return _sync_executor
        max_w = min(4, os.cpu_count() or 2)
        _sync_executor = ThreadPoolExecutor(
            max_workers=max_w,
            thread_name_prefix="v1-sync",
        )
        atexit.register(_sync_executor.shutdown, wait=False)
        return _sync_executor


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@v1_bp.route("/health", methods=["GET"])
def health() -> tuple[Response, int]:
    """Health check endpoint for ProviderFactory startup verification."""
    return jsonify({
        "success": True,
        "data": {
            "status": "ok",
            "version": "1.0.0",
        },
    }), 200


# ---------------------------------------------------------------------------
# Synchronous parse
# ---------------------------------------------------------------------------

_ALLOWED_MIME = {
    "application/pdf",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}

_CSV_MIME = {"text/csv"}
_EXCEL_MIME = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}


@v1_bp.route("/parse/sync", methods=["POST", "OPTIONS"])
def parse_sync() -> tuple[Response, int]:
    """Synchronous document parsing -- file bytes in, markdown out.

    Supports PDF, CSV, and Excel files. Routes internally by MIME type.
    Designed for ai-platform ``DocForgeClient`` and KMS ai-worker
    ``DocForgeParsingProvider``.
    """
    if request.method == "OPTIONS":
        return Response("", status=204)
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({
            "success": False,
            "error": {
                "code": "NO_FILE",
                "message": "파일이 필요합니다. 'file' 필드로 파일을 전송하세요.",
            },
        }), 400

    # MIME type validation
    mime = file.content_type or ""
    base_mime = mime.split(";")[0].strip().lower()
    if base_mime not in _ALLOWED_MIME:
        allowed = ", ".join(sorted(_ALLOWED_MIME))
        return jsonify({
            "success": False,
            "error": {
                "code": "UNSUPPORTED_MEDIA_TYPE",
                "message": f"지원하지 않는 파일 형식입니다: {mime}. 허용: {allowed}",
            },
        }), 415

    # Route by MIME type
    try:
        if base_mime in _CSV_MIME:
            return _handle_csv(file, base_mime)

        if base_mime in _EXCEL_MIME:
            return _handle_excel(file, base_mime)

        # PDF (default)
        return _handle_pdf(file)

    except Exception:
        logger.exception("parse_sync failed for file %s (mime=%s)", file.filename, base_mime)
        return jsonify({
            "success": False,
            "error": {
                "code": "PARSE_ERROR",
                "message": "파싱 중 오류가 발생했습니다. 관리자에게 문의하세요.",
            },
        }), 500


def _handle_pdf(file) -> tuple[Response, int]:
    """PDF 파일 파싱 처리.

    The heavy ``parse_pdf`` call is offloaded to a shared thread pool and
    awaited with a timeout so that a single slow document cannot block the
    request thread indefinitely.
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="docforge_sync_")
        filename = _safe_filename(file.filename or "upload.pdf")
        pdf_path = Path(tmp_dir) / filename
        file.save(str(pdf_path))

        from docforge.usecases.parse_pdf import parse_pdf

        future = _get_sync_executor().submit(parse_pdf, pdf_path)
        try:
            result = future.result(timeout=_SYNC_TIMEOUT)
        except FutureTimeout:
            future.cancel()
            logger.warning(
                "parse_sync timed out after %ds for file %s",
                _SYNC_TIMEOUT, file.filename,
            )
            return jsonify({
                "success": False,
                "error": {
                    "code": "REQUEST_TIMEOUT",
                    "message": f"파싱 시간이 {_SYNC_TIMEOUT}초를 초과했습니다. 파일이 너무 크거나 복잡할 수 있습니다.",
                },
            }), 408

        metadata_dict = _serialize(result.metadata)
        stats_dict = _serialize(result.stats)

        return jsonify({
            "success": True,
            "data": {
                "markdown": result.markdown,
                "metadata": metadata_dict,
                "stats": stats_dict,
            },
        }), 200
    finally:
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _handle_csv(file, mime: str) -> tuple[Response, int]:
    """CSV 파일 파싱 처리."""
    from docforge.adapters.csv_reader import parse_csv_bytes

    file_bytes = file.read()
    filename = file.filename or "upload.csv"

    result = parse_csv_bytes(file_bytes, filename=filename)

    return jsonify({
        "success": True,
        "data": {
            "markdown": result.markdown,
            "metadata": result.metadata,
            "stats": result.stats,
        },
    }), 200


def _handle_excel(file, mime: str) -> tuple[Response, int]:
    """Excel 파일 파싱 처리."""
    from docforge.adapters.excel_reader import parse_excel_bytes

    file_bytes = file.read()
    filename = file.filename or "upload.xlsx"

    result = parse_excel_bytes(file_bytes, filename=filename)

    return jsonify({
        "success": True,
        "data": {
            "markdown": result.markdown,
            "metadata": result.metadata,
            "stats": result.stats,
        },
    }), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(obj: object) -> dict:
    """Convert a frozen dataclass to a JSON-serializable dict."""
    try:
        return dataclasses.asdict(obj)  # type: ignore[arg-type]
    except TypeError:
        return {}
