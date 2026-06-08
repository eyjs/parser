"""V1 API routes for DocForge -- stable, versioned, authenticated."""

from __future__ import annotations

import atexit
import dataclasses
import hashlib
import logging
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from docforge.web import async_worker
from docforge.web.async_worker import async_store_dir as _async_store_dir
from docforge.web.job_store import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
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
    """Health check endpoint for ProviderFactory startup verification.

    Includes lightweight queue observability (P2-2): ``queue_depth`` (queued),
    ``in_flight`` (processing) and the per-status ``queue`` breakdown so an
    operator (or the upstream) can see saturation without a separate call. The
    counts come from a single ``GROUP BY`` and never make ``/health`` fail --
    any store error degrades to omitting the metrics, not a non-200.
    """
    data: dict = {"status": "ok", "version": "1.0.0"}
    try:
        counts = _get_job_store().counts()
        data["queue_depth"] = counts[STATUS_QUEUED]
        data["in_flight"] = counts[STATUS_PROCESSING]
        data["queue"] = counts
    except Exception:  # noqa: BLE001 -- health must stay 200 even if metrics fail
        logger.exception("queue metrics unavailable for /v1/health")
    return jsonify({"success": True, "data": data}), 200


@v1_bp.route("/metrics", methods=["GET"])
def metrics() -> tuple[Response, int]:
    """Queue metrics for observability (P2-2): depth, in-flight, per-status.

    Separate from ``/health`` so monitoring can scrape it directly. ``queue`` is
    the per-status breakdown (queued/processing/done/failed); ``queue_depth`` and
    ``in_flight`` surface the two operationally important numbers up top.
    """
    counts = _get_job_store().counts()
    return jsonify({
        "success": True,
        "data": {
            "queue_depth": counts[STATUS_QUEUED],
            "in_flight": counts[STATUS_PROCESSING],
            "queue": counts,
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

_job_store: ParseJobStore | None = None
_job_store_lock = threading.Lock()
_async_worker_started = False
_async_worker_lock = threading.Lock()


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
    """Run the shared consumer loop in this (web) process forever.

    Retained for the in-proc dev fallback (``DOCFORGE_INPROC_WORKER=1``). The
    canonical consumer is now a SEPARATE process (``docforge-worker``); see
    ``async_worker.run_worker_loop`` and ``worker_main``. Parsing behaviour is
    unchanged -- only *where* the loop runs differs.
    """
    async_worker.run_worker_loop(_get_job_store(), _parse_by_mime)


def _ensure_async_worker() -> None:
    """Start the in-proc worker thread ONLY when the dev fallback is enabled.

    By default (``DOCFORGE_INPROC_WORKER=0``) the web process does NOT spawn a
    parse worker: parsing is owned by the separate ``docforge-worker`` process
    so a CPU-bound parse cannot starve gunicorn's HTTP threads (defect A). The
    web process then only enqueues and polls. Setting ``DOCFORGE_INPROC_WORKER=1``
    restores the legacy single-thread behaviour for local development.
    """
    global _async_worker_started
    if _async_worker_started:
        return
    if not async_worker.inproc_worker_enabled():
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

    # Backpressure (defect B): when the queue is saturated, reject new work with
    # 503 + Retry-After instead of growing the queue without bound. The upstream
    # (ai-platform docforge_client) treats this exact 503/QUEUE_FULL/Retry-After
    # contract as a clear back-off signal -- removing the old ambiguity where a
    # dropped connection looked like a hard failure and triggered a thundering
    # herd of retries. We check the *queued* (not-yet-claimed) depth only.
    if store.queued_count() >= async_worker.queue_max():
        retry_after = async_worker.retry_after_sec()
        resp = jsonify({
            "success": False,
            "error": {
                "code": "QUEUE_FULL",
                "message": (
                    "파싱 큐가 가득 찼습니다. 잠시 후 다시 시도하세요 "
                    f"(Retry-After: {retry_after}s)."
                ),
            },
        })
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 503

    # Content idempotency (defect C): hash the uploaded bytes so a duplicate
    # submission of the same file (upstream retry, orphan recovery, thundering
    # herd) attaches to the existing in-flight job instead of spawning a
    # redundant parse that could overwrite a good result with an empty one. We
    # read the bytes once for both the hash and the durable write.
    file_bytes = file.read()
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Short-circuit before allocating a job dir / writing the payload when an
    # identical job is already queued or processing.
    existing = store.find_active_by_hash(content_hash, mime)
    if existing is not None:
        return jsonify({
            "success": True,
            "data": {
                "job_id": existing.job_id,
                "status": existing.status,
                "queue_size": store.queued_count(),
                "deduplicated": True,
            },
        }), 202

    job_id = uuid.uuid4().hex

    # Persist the payload in a durable per-job directory so it survives a
    # restart and can be re-parsed after orphan recovery. It is removed by the
    # TTL cleanup once the job finishes -- NOT eagerly after parsing.
    job_dir = _async_store_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    saved = job_dir / _safe_filename(file.filename or "upload.pdf")
    saved.write_bytes(file_bytes)

    # Atomic dedup-or-insert: if a concurrent request inserted the same content
    # between our pre-check and here, we attach to that job (and drop the dir we
    # just created) instead of inserting a duplicate.
    effective_id, deduplicated = store.enqueue_idempotent(
        job_id, mime, str(saved), content_hash,
    )
    if deduplicated and effective_id != job_id:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

    return jsonify({
        "success": True,
        "data": {
            "job_id": effective_id,
            "status": "queued",
            "queue_size": store.queued_count(),
            "deduplicated": deduplicated,
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
