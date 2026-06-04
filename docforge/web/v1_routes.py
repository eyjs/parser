"""V1 API routes for DocForge -- stable, versioned, authenticated."""

from __future__ import annotations

import atexit
import dataclasses
import logging
import os
import queue as _queue
import shutil
import tempfile
import threading
import time as _time
import uuid
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

# Per-request parse budget. Must stay < Gunicorn's --timeout
# (DOCFORGE_GUNICORN_TIMEOUT, default 1800) so the worker is not killed
# mid-request. Large born-digital documents (e.g. a 1500-page insurance 약관)
# legitimately take many minutes, so the old 280s ceiling was far too low.
# Tunable via DOCFORGE_SYNC_TIMEOUT.
_SYNC_TIMEOUT = int(os.environ.get("DOCFORGE_SYNC_TIMEOUT", "1740"))  # seconds

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
# Asynchronous parse queue
# ---------------------------------------------------------------------------
#
# Large documents (e.g. a 1500-page insurance 약관) take many minutes to parse.
# Holding a synchronous HTTP connection open that long is fragile. Instead,
# callers POST to /v1/parse/async (returns a job_id immediately), the document
# is queued, and a SINGLE background worker processes the queue one document at
# a time. Callers poll GET /v1/parse/async/<job_id> for the result.
#
# The job store is in-process, so this assumes a single Gunicorn worker
# (DOCFORGE_WORKERS=1, the default). Finished jobs are kept for
# DOCFORGE_ASYNC_JOB_TTL seconds so the poller can fetch the result.

_async_jobs: dict[str, dict] = {}
_async_jobs_lock = threading.Lock()
_async_queue: "_queue.Queue[tuple[str, str, str]]" = _queue.Queue()
_async_worker_started = False
_async_worker_lock = threading.Lock()
_ASYNC_JOB_TTL = int(os.environ.get("DOCFORGE_ASYNC_JOB_TTL", "3600"))


def _cleanup_async_jobs() -> None:
    now = _time.time()
    with _async_jobs_lock:
        stale = [
            k for k, v in _async_jobs.items()
            if v.get("ts") and v["status"] in ("done", "failed")
            and now - v["ts"] > _ASYNC_JOB_TTL
        ]
        for k in stale:
            _async_jobs.pop(k, None)


def _parse_by_mime(path: Path, mime: str) -> dict:
    """Parse a saved file and return the JSON ``data`` payload."""
    if mime in _CSV_MIME:
        from docforge.adapters.csv_reader import parse_csv_bytes
        result = parse_csv_bytes(path.read_bytes(), filename=path.name)
        return {"markdown": result.markdown, "metadata": result.metadata, "stats": result.stats}
    if mime in _EXCEL_MIME:
        from docforge.adapters.excel_reader import parse_excel_bytes
        result = parse_excel_bytes(path.read_bytes(), filename=path.name)
        return {"markdown": result.markdown, "metadata": result.metadata, "stats": result.stats}
    from docforge.usecases.parse_pdf import parse_pdf
    result = parse_pdf(path)
    return {
        "markdown": result.markdown,
        "metadata": _serialize(result.metadata),
        "stats": _serialize(result.stats),
    }


def _async_worker_loop() -> None:
    """Process queued parse jobs one at a time."""
    while True:
        job_id, pdf_path, mime = _async_queue.get()
        path = Path(pdf_path)
        try:
            with _async_jobs_lock:
                if job_id in _async_jobs:
                    _async_jobs[job_id]["status"] = "processing"
            data = _parse_by_mime(path, mime)
            with _async_jobs_lock:
                _async_jobs[job_id] = {"status": "done", "data": data, "ts": _time.time()}
        except Exception as exc:  # noqa: BLE001 -- surface any parse failure to poller
            logger.exception("async parse failed for job %s", job_id)
            with _async_jobs_lock:
                _async_jobs[job_id] = {
                    "status": "failed",
                    "error": str(exc) or type(exc).__name__,
                    "ts": _time.time(),
                }
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
            _async_queue.task_done()
            _cleanup_async_jobs()


def _ensure_async_worker() -> None:
    global _async_worker_started
    if _async_worker_started:
        return
    with _async_worker_lock:
        if _async_worker_started:
            return
        threading.Thread(
            target=_async_worker_loop, name="v1-async-worker", daemon=True,
        ).start()
        _async_worker_started = True


@v1_bp.route("/parse/async", methods=["POST", "OPTIONS"])
def parse_async() -> tuple[Response, int]:
    """Enqueue a document for asynchronous parsing; returns a job_id at once."""
    if request.method == "OPTIONS":
        return Response("", status=204)
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({
            "success": False,
            "error": {"code": "NO_FILE", "message": "파일이 필요합니다. 'file' 필드로 전송하세요."},
        }), 400

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in _ALLOWED_MIME:
        allowed = ", ".join(sorted(_ALLOWED_MIME))
        return jsonify({
            "success": False,
            "error": {"code": "UNSUPPORTED_MEDIA_TYPE", "message": f"지원하지 않는 형식: {mime}. 허용: {allowed}"},
        }), 415

    _ensure_async_worker()
    job_id = uuid.uuid4().hex
    tmp_dir = tempfile.mkdtemp(prefix="docforge_async_")
    saved = Path(tmp_dir) / _safe_filename(file.filename or "upload.pdf")
    file.save(str(saved))

    with _async_jobs_lock:
        _async_jobs[job_id] = {"status": "queued", "ts": _time.time()}
    _async_queue.put((job_id, str(saved), mime))

    return jsonify({
        "success": True,
        "data": {"job_id": job_id, "status": "queued", "queue_size": _async_queue.qsize()},
    }), 202


@v1_bp.route("/parse/async/<job_id>", methods=["GET"])
def parse_async_status(job_id: str) -> tuple[Response, int]:
    """Poll an async parse job. Returns status and, when done, the result."""
    with _async_jobs_lock:
        job = _async_jobs.get(job_id)
        job = dict(job) if job else None
    if job is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다 (만료되었거나 잘못된 ID)."},
        }), 404
    status = job["status"]
    if status == "done":
        return jsonify({"success": True, "data": {"status": "done", **job["data"]}}), 200
    if status == "failed":
        return jsonify({
            "success": False,
            "data": {"status": "failed"},
            "error": {"code": "PARSE_ERROR", "message": job.get("error", "파싱 실패")},
        }), 200
    return jsonify({"success": True, "data": {"status": status}}), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(obj: object) -> dict:
    """Convert a frozen dataclass to a JSON-serializable dict."""
    try:
        return dataclasses.asdict(obj)  # type: ignore[arg-type]
    except TypeError:
        return {}
