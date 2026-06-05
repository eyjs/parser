"""V1 API routes for DocForge -- stable, versioned, authenticated."""

from __future__ import annotations

import atexit
import dataclasses
import logging
import os
import tempfile
import threading
import time as _time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from docforge.web.job_store import (
    STATUS_DONE,
    STATUS_FAILED,
    ParseJobStore,
)
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
    "text/markdown",
    "text/plain",
}

_CSV_MIME = {"text/csv"}
_EXCEL_MIME = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
# Markdown/plain text: 파싱 불필요, 텍스트를 그대로 markdown으로 pass-through.
_MARKDOWN_MIME = {"text/markdown", "text/plain"}


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

        if base_mime in _MARKDOWN_MIME:
            return _handle_markdown(file, base_mime)

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


def _decode_text(file_bytes: bytes) -> str:
    """텍스트 파일 바이트를 디코드한다. UTF-8 우선, 실패 시 cp949/latin-1 폴백."""
    for encoding in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 마지막 폴백: 손실 허용 디코드 (조용히 삼키지 않고 replace로 가시화)
    return file_bytes.decode("utf-8", errors="replace")


def _markdown_payload(file_bytes: bytes, filename: str) -> dict:
    """markdown/plain 텍스트 → data payload. 파싱 불필요, 섹션 헤더 보존 pass-through."""
    text = _decode_text(file_bytes)
    return {
        "markdown": text,
        "metadata": {"filename": filename, "format": "markdown"},
        "stats": {"char_count": len(text), "line_count": text.count("\n") + 1},
    }


def _handle_markdown(file, mime: str) -> tuple[Response, int]:
    """markdown/plain 텍스트 파일 처리.

    파싱이 불필요한 포맷이므로 텍스트를 그대로 markdown으로 반환한다.
    섹션 헤더(``#``)는 원문 그대로 보존되어 다운스트림 청크 분할에 활용된다.
    """
    file_bytes = file.read()
    filename = file.filename or "upload.md"

    return jsonify({
        "success": True,
        "data": _markdown_payload(file_bytes, filename),
    }), 200


# ---------------------------------------------------------------------------
# Asynchronous parse queue (durable, SQLite-backed)
# ---------------------------------------------------------------------------
#
# Large documents (e.g. a 1500-page insurance 약관) take many minutes to parse.
# Holding a synchronous HTTP connection open that long is fragile. Instead,
# callers POST to /v1/parse/async (returns a job_id immediately), the document
# is persisted to a durable job store, and a background worker claims and
# processes jobs. Callers poll GET /v1/parse/async/<job_id> for the result.
#
# The job store is SQLite-backed (see job_store.py), so jobs survive a worker
# or process restart: an interrupted document is recovered on boot and parsed
# again rather than lost. Job claiming uses an atomic UPDATE under a
# BEGIN IMMEDIATE transaction, so MULTIPLE workers are safe -- the old
# DOCFORGE_WORKERS=1 restriction no longer applies. Finished jobs (and their
# persisted payloads) are pruned DOCFORGE_ASYNC_JOB_TTL seconds after they
# complete so the poller can still fetch the result in the meantime.

_ASYNC_JOB_TTL = int(os.environ.get("DOCFORGE_ASYNC_JOB_TTL", "3600"))
#: Worker idle poll interval (seconds) when no job is available to claim.
_ASYNC_POLL_SEC = float(os.environ.get("DOCFORGE_ASYNC_POLL_SEC", "0.5"))
#: Run TTL cleanup roughly every N worker iterations to bound DB churn.
_ASYNC_CLEANUP_EVERY = 200

_job_store: ParseJobStore | None = None
_job_store_lock = threading.Lock()
_async_worker_started = False
_async_worker_lock = threading.Lock()


def _async_store_dir() -> Path:
    """Resolve the durable store directory (DB + persisted payloads)."""
    configured = os.environ.get("DOCFORGE_ASYNC_STORE_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "docforge_async_jobs"


def _get_job_store() -> ParseJobStore:
    """Return (or lazily create) the durable parse-job store."""
    global _job_store
    if _job_store is not None:
        return _job_store
    with _job_store_lock:
        if _job_store is not None:
            return _job_store
        _job_store = ParseJobStore(_async_store_dir())
        return _job_store


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
    if mime in _MARKDOWN_MIME:
        return _markdown_payload(path.read_bytes(), path.name)
    from docforge.usecases.parse_pdf import parse_pdf
    result = parse_pdf(path)
    return {
        "markdown": result.markdown,
        "metadata": _serialize(result.metadata),
        "stats": _serialize(result.stats),
    }


def _async_worker_loop() -> None:
    """Claim and process durable parse jobs until the process exits.

    Jobs are claimed atomically from the SQLite store (multi-worker safe).
    A job that crashes the process mid-parse stays ``processing`` on disk and
    is requeued by ``recover_orphans`` on the next boot, so no work is lost.
    The payload file is kept until the TTL cleanup removes the finished job.
    """
    store = _get_job_store()
    iterations = 0
    while True:
        iterations += 1
        try:
            row = store.claim()
        except Exception:  # noqa: BLE001 -- a transient DB error must not kill the worker
            logger.exception("async worker claim failed; backing off")
            _time.sleep(_ASYNC_POLL_SEC)
            continue

        if row is None:
            if iterations % _ASYNC_CLEANUP_EVERY == 0:
                _safe_cleanup(store)
            _time.sleep(_ASYNC_POLL_SEC)
            continue

        try:
            data = _parse_by_mime(Path(row.payload_path), row.mime)
            store.mark_done(row.job_id, data)
        except Exception as exc:  # noqa: BLE001 -- surface any parse failure to poller
            logger.exception("async parse failed for job %s", row.job_id)
            store.mark_failed(row.job_id, str(exc) or type(exc).__name__)

        if iterations % _ASYNC_CLEANUP_EVERY == 0:
            _safe_cleanup(store)


def _safe_cleanup(store: ParseJobStore) -> None:
    """Best-effort TTL cleanup; never lets a cleanup error stop the worker."""
    try:
        store.cleanup_expired(_ASYNC_JOB_TTL)
    except Exception:  # noqa: BLE001
        logger.exception("async job TTL cleanup failed")


def _ensure_async_worker() -> None:
    global _async_worker_started
    if _async_worker_started:
        return
    with _async_worker_lock:
        if _async_worker_started:
            return
        # Recover jobs left 'processing' by a previous crash before workers run.
        _get_job_store().recover_orphans()
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
    store = _get_job_store()
    job_id = uuid.uuid4().hex

    # Persist the payload in a durable per-job directory so it survives a
    # restart and can be re-parsed after orphan recovery. It is removed by the
    # TTL cleanup once the job finishes -- NOT eagerly after parsing.
    job_dir = _async_store_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    saved = job_dir / _safe_filename(file.filename or "upload.pdf")
    file.save(str(saved))

    store.enqueue(job_id, mime, str(saved))

    return jsonify({
        "success": True,
        "data": {
            "job_id": job_id,
            "status": "queued",
            "queue_size": store.queued_count(),
        },
    }), 202


@v1_bp.route("/parse/async/<job_id>", methods=["GET"])
def parse_async_status(job_id: str) -> tuple[Response, int]:
    """Poll an async parse job. Returns status and, when done, the result.

    Because jobs are durable, a worker restart does NOT make a live job vanish:
    a 404 here means the id was never seen (typo) or was pruned long after
    completion. queued/processing/done/failed all return 200.
    """
    job = _get_job_store().get(job_id)
    if job is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다 (만료되었거나 잘못된 ID)."},
        }), 404
    if job.status == STATUS_DONE:
        data = job.result or {}
        return jsonify({"success": True, "data": {"status": "done", **data}}), 200
    if job.status == STATUS_FAILED:
        return jsonify({
            "success": False,
            "data": {"status": "failed"},
            "error": {"code": "PARSE_ERROR", "message": job.error or "파싱 실패"},
        }), 200
    return jsonify({"success": True, "data": {"status": job.status}}), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(obj: object) -> dict:
    """Convert a frozen dataclass to a JSON-serializable dict."""
    try:
        return dataclasses.asdict(obj)  # type: ignore[arg-type]
    except TypeError:
        return {}
