"""Flask route handlers for DocForge API."""

from __future__ import annotations

import dataclasses
import difflib
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    send_file,
    stream_with_context,
)
import re
import unicodedata

from docforge.web.sse import (
    EVT_DONE,
    EVT_ERROR,
    ProgressTracker,
)
from docforge.web.storage import TaskStore
from docforge.web.task_state import (
    registry as task_registry,
    state_full,
    state_summary,
)
from docforge.web.worker import (
    get_tracker as _get_tracker,
    set_tracker as _set_tracker,
    remove_tracker as _remove_tracker,
    submit_task,
    cancel_task,
    get_queue_status,
)

bp = Blueprint("docforge", __name__)

# ---------------------------------------------------------------------------
# Task store — lazily initialized per-app (stored on app config)
# ---------------------------------------------------------------------------

_STORE_KEY = "TASK_STORE"


def _get_store() -> TaskStore:
    """Return (or create) the singleton TaskStore for the current app."""
    if _STORE_KEY not in current_app.config:
        upload_dir = Path(current_app.config["UPLOAD_DIR"])
        current_app.config[_STORE_KEY] = TaskStore(upload_dir)
    return current_app.config[_STORE_KEY]


# ---------------------------------------------------------------------------
# Allowed extensions
# ---------------------------------------------------------------------------

_ALLOWED_EXT = {".pdf"}


def _is_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in _ALLOWED_EXT


def _safe_filename(filename: str) -> str:
    """Sanitize filename while preserving Korean and other Unicode characters."""
    filename = unicodedata.normalize("NFC", filename)
    filename = filename.replace("/", "_").replace("\\", "_")
    filename = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename)
    filename = filename.strip(". ")
    if not filename:
        return "upload.pdf"
    return filename


# ---------------------------------------------------------------------------
# API: upload + parse
# ---------------------------------------------------------------------------


@bp.route("/api/parse", methods=["POST"])
def api_parse() -> Response:
    """PDF 업로드 및 파싱 작업 시작 (단일/멀티파일 지원)."""
    # Collect files: support both 'file' (single) and 'files' (multi)
    files = request.files.getlist("files")
    if not files or (len(files) == 1 and not files[0].filename):
        # Fallback to single 'file' field for backward compatibility
        single = request.files.get("file")
        if single and single.filename:
            files = [single]
        else:
            return jsonify({"success": False, "error": {"code": "NO_FILE", "message": "파일이 없습니다."}}), 400

    store = _get_store()
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    app_ref = current_app._get_current_object()  # type: ignore[attr-defined]
    task_ids: list[str] = []

    for file in files:
        if not file.filename:
            continue

        filename: str = _safe_filename(file.filename)
        if not _is_allowed(filename):
            continue

        # Save uploaded file
        try:
            record = store.create(filename, "")
            task_id = record.task_id
            pdf_path = upload_dir / task_id / filename
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            file.save(str(pdf_path))
            store.update(task_id, pdf_path=str(pdf_path))
        except OSError as exc:
            continue

        # Register task state (for REST catch-up) and bind tracker to it.
        task_registry.create(task_id, filename)
        tracker = ProgressTracker(task_id=task_id)
        _set_tracker(task_id, tracker)

        # Submit to worker queue
        submit_task(task_id, _run_parse, app_ref, task_id, pdf_path)
        task_ids.append(task_id)

    if not task_ids:
        return jsonify({"success": False, "error": {"code": "NO_VALID_FILES", "message": "유효한 PDF 파일이 없습니다."}}), 400

    # Backward compatible: single file returns task_id, multi returns task_ids
    if len(task_ids) == 1:
        return jsonify({"success": True, "data": {"task_id": task_ids[0], "task_ids": task_ids}}), 202
    return jsonify({"success": True, "data": {"task_ids": task_ids}}), 202


# ---------------------------------------------------------------------------
# API: SSE progress stream
# ---------------------------------------------------------------------------


@bp.route("/api/parse/<task_id>/status")
def api_parse_status(task_id: str) -> Response:
    """SSE 스트림 — 파싱 진행률 실시간 전달.

    재연결 시나리오: 클라이언트가 새로고침 후 다시 구독하면 먼저
    ``catchup`` 이벤트로 누적 스냅샷을 한 번 보내고, 살아있는
    tracker에 합류한다. 이미 완료된 경우 ``done``/``error``만 보낸다.
    """
    import json as _json

    tracker = _get_tracker(task_id)
    # Lock-safe snapshot — see TaskRegistry.snapshot_full. Markdown is
    # excluded from catch-up; clients fetch it via REST to avoid replaying
    # hundreds of KB over SSE on reconnect.
    catchup_payload = task_registry.snapshot_full(task_id)
    state = task_registry.get(task_id)

    if tracker is None:
        # No live tracker — fall back to stored status (legacy + finished tasks).
        store = _get_store()
        record = store.get(task_id)
        if record is None and state is None:
            return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

        def _static_stream():
            if catchup_payload is not None:
                yield f"event: catchup\ndata: {_json.dumps(catchup_payload)}\n\n"
            if record is not None and record.status == "done":
                yield f"event: {EVT_DONE}\ndata: {_json.dumps({'message': '완료', 'pct': 100})}\n\n"
            elif record is not None:
                yield f"event: {EVT_ERROR}\ndata: {_json.dumps({'message': record.error or '오류 발생', 'pct': 0})}\n\n"
            elif state is not None and state.status == "done":
                yield f"event: {EVT_DONE}\ndata: {_json.dumps({'pct': 100})}\n\n"
            elif state is not None and state.status == "error":
                yield f"event: {EVT_ERROR}\ndata: {_json.dumps({'message': state.error_message or '', 'pct': 0})}\n\n"

        return Response(stream_with_context(_static_stream()), mimetype="text/event-stream")

    # Tracker is live: emit catchup first, then merge into the live stream.
    def _live_stream():
        if catchup_payload is not None:
            yield f"event: catchup\ndata: {_json.dumps(catchup_payload)}\n\n"
        for chunk in tracker.stream():
            yield chunk

    return Response(
        stream_with_context(_live_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# API: live state catch-up (REST)
# ---------------------------------------------------------------------------


@bp.route("/api/parse/active")
def api_parse_active() -> Response:
    """현재 큐/실행 중인 작업 목록."""
    return jsonify({"success": True, "data": task_registry.snapshot_active()})


@bp.route("/api/parse/<task_id>/state")
def api_parse_state(task_id: str) -> Response:
    """단일 작업의 누적 스냅샷 (markdown 제외)."""
    snapshot = task_registry.snapshot_full(task_id)
    if snapshot is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404
    return jsonify({"success": True, "data": snapshot})


@bp.route("/api/parse/<task_id>/pages")
def api_parse_pages(task_id: str) -> Response:
    """완료된 페이지 번호 목록."""
    snapshot = task_registry.snapshot_completed_pages(task_id)
    if snapshot is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404
    return jsonify({"success": True, "data": snapshot})


@bp.route("/api/parse/<task_id>/page/<int:page_num>")
def api_parse_page(task_id: str, page_num: int) -> Response:
    """특정 페이지 markdown."""
    md = task_registry.get_page_markdown(task_id, page_num)
    if md is None:
        # Could be missing task or missing page — disambiguate.
        if task_registry.get(task_id) is None:
            return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404
        return jsonify({"success": False, "error": {"code": "PAGE_NOT_READY", "message": "해당 페이지는 아직 처리되지 않았습니다."}}), 404
    return jsonify({"success": True, "data": {"page": page_num, "markdown": md}})


# ---------------------------------------------------------------------------
# API: result
# ---------------------------------------------------------------------------


@bp.route("/api/parse/<task_id>/result")
def api_parse_result(task_id: str) -> Response:
    """파싱 결과(마크다운, 메타데이터, 통계) 반환."""
    store = _get_store()
    record = store.get(task_id)
    if record is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    if record.status != "done":
        return jsonify({
            "success": False,
            "error": {"code": "NOT_READY", "message": f"아직 완료되지 않았습니다. 상태: {record.status}"},
        }), 409

    # Try loading from result.json first (persisted), then fall back to in-memory record
    result_data = store.load_result(task_id)
    if result_data:
        return jsonify({
            "success": True,
            "data": {
                "task_id": task_id,
                "filename": record.filename,
                "markdown": record.markdown or result_data.get("markdown", ""),
                "metadata": record.metadata or result_data.get("metadata", {}),
                "stats": record.stats or result_data.get("stats", {}),
                "completed_at": record.completed_at or result_data.get("completed_at", ""),
                "pdf_path": record.pdf_path,
            },
        })

    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "filename": record.filename,
            "markdown": record.markdown,
            "metadata": record.metadata,
            "stats": record.stats,
            "completed_at": record.completed_at,
            "pdf_path": record.pdf_path,
        },
    })


# ---------------------------------------------------------------------------
# API: history
# ---------------------------------------------------------------------------


@bp.route("/api/history")
def api_history() -> Response:
    """변환 이력 목록 반환."""
    store = _get_store()
    records = store.list_all()
    items = [
        {
            "task_id": r.task_id,
            "filename": r.filename,
            "status": r.status,
            "progress": r.progress,
            "progress_pct": r.progress_pct,
            "created_at": r.created_at,
            "completed_at": r.completed_at,
            "error": r.error,
        }
        for r in records
    ]
    return jsonify({"success": True, "data": items})


@bp.route("/api/history/<task_id>", methods=["DELETE"])
def api_history_delete(task_id: str) -> Response:
    """변환 이력 항목 삭제."""
    store = _get_store()
    deleted = store.delete(task_id)
    if not deleted:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    # Remove tracker if still running
    _remove_tracker(task_id)

    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API: save edited markdown
# ---------------------------------------------------------------------------


@bp.route("/api/save/<task_id>", methods=["POST"])
def api_save(task_id: str) -> Response:
    """편집된 마크다운 저장."""
    store = _get_store()
    record = store.get(task_id)
    if record is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    body = request.get_json(silent=True)
    if not body or "markdown" not in body:
        return jsonify({"success": False, "error": {"code": "NO_MARKDOWN", "message": "마크다운 내용이 없습니다."}}), 400

    markdown: str = body["markdown"]
    try:
        if record.md_path:
            md_path = Path(record.md_path)
        else:
            upload_dir = Path(current_app.config["UPLOAD_DIR"])
            md_path = upload_dir / task_id / (Path(record.filename).stem + ".md")
            md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(markdown, encoding="utf-8")
        store.update(task_id, markdown=markdown, md_path=str(md_path))

        # Save as new version
        store.save_version(task_id, markdown)

        # Update result.json
        store.save_result(task_id, {
            "markdown": markdown,
            "metadata": record.metadata,
            "stats": record.stats,
            "completed_at": record.completed_at,
        })
    except OSError as exc:
        return jsonify({"success": False, "error": {"code": "SAVE_FAILED", "message": f"저장 실패: {exc}"}}), 500

    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API: export / download
# ---------------------------------------------------------------------------


@bp.route("/api/export/<task_id>")
def api_export(task_id: str) -> Response:
    """변환된 마크다운 파일 다운로드."""
    store = _get_store()
    record = store.get(task_id)
    if record is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    if not record.md_path or not Path(record.md_path).exists():
        return jsonify({"success": False, "error": {"code": "NO_FILE", "message": "다운로드할 파일이 없습니다."}}), 404

    return send_file(
        record.md_path,
        as_attachment=True,
        download_name=Path(record.filename).stem + ".md",
        mimetype="text/markdown",
    )


# ---------------------------------------------------------------------------
# API: queue status + cancel
# ---------------------------------------------------------------------------


@bp.route("/api/queue/status")
def api_queue_status() -> Response:
    """현재 큐 상태 반환."""
    status = get_queue_status()
    return jsonify({"success": True, "data": status})


@bp.route("/api/parse/<task_id>/cancel", methods=["POST"])
def api_cancel(task_id: str) -> Response:
    """대기 중인 작업 취소."""
    store = _get_store()
    record = store.get(task_id)
    if record is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    if record.status == "running":
        return jsonify({"success": False, "error": {"code": "RUNNING", "message": "실행 중인 작업은 취소할 수 없습니다."}}), 409

    if record.status != "queued":
        return jsonify({"success": False, "error": {"code": "INVALID_STATE", "message": f"현재 상태에서 취소할 수 없습니다: {record.status}"}}), 409

    cancelled = cancel_task(task_id)
    if cancelled:
        store.update(task_id, status="cancelled")
        _remove_tracker(task_id)
        return jsonify({"success": True})

    # Future.cancel() returned False — task may have started running
    return jsonify({"success": False, "error": {"code": "CANCEL_FAILED", "message": "취소에 실패했습니다. 이미 실행 중일 수 있습니다."}}), 409


# ---------------------------------------------------------------------------
# API: versions + diff
# ---------------------------------------------------------------------------


@bp.route("/api/versions/<task_id>")
def api_versions(task_id: str) -> Response:
    """태스크의 저장된 버전 목록 반환."""
    store = _get_store()
    record = store.get(task_id)
    if record is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    versions = store.list_versions(task_id)
    return jsonify({"success": True, "data": versions})


@bp.route("/api/diff/<task_id>")
def api_diff(task_id: str) -> Response:
    """두 버전 간의 diff 반환."""
    store = _get_store()
    record = store.get(task_id)
    if record is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "작업을 찾을 수 없습니다."}}), 404

    v1 = request.args.get("v1", "")
    v2 = request.args.get("v2", "")

    if not v1 or not v2:
        return jsonify({"success": False, "error": {"code": "MISSING_PARAMS", "message": "v1, v2 파라미터가 필요합니다."}}), 400

    content1 = store.get_version_content(task_id, v1)
    content2 = store.get_version_content(task_id, v2)

    if content1 is None or content2 is None:
        return jsonify({"success": False, "error": {"code": "VERSION_NOT_FOUND", "message": "지정된 버전을 찾을 수 없습니다."}}), 404

    diff_lines = list(difflib.unified_diff(
        content1.splitlines(keepends=True),
        content2.splitlines(keepends=True),
        fromfile=v1,
        tofile=v2,
    ))

    return jsonify({
        "success": True,
        "data": {
            "v1": v1,
            "v2": v2,
            "diff": "".join(diff_lines),
            "has_changes": len(diff_lines) > 0,
        },
    })


# ---------------------------------------------------------------------------
# Uploaded / result file serving
# ---------------------------------------------------------------------------


@bp.route("/uploads/<path:filename>")
def serve_upload(filename: str) -> Response:
    """업로드 및 결과 파일 제공."""
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    file_path = upload_dir / filename

    # Prevent path traversal
    try:
        file_path.resolve().relative_to(upload_dir.resolve())
    except ValueError:
        return jsonify({"success": False, "error": {"code": "FORBIDDEN", "message": "접근 거부"}}), 403

    if not file_path.exists():
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "파일 없음"}}), 404

    return send_file(str(file_path))


# ---------------------------------------------------------------------------
# Background parsing worker
# ---------------------------------------------------------------------------


def _run_parse(app, task_id: str, pdf_path: Path) -> None:
    """Background thread: run parse_pdf and stream progress via ProgressTracker."""
    with app.app_context():
        store: TaskStore = _get_store()
        tracker = _get_tracker(task_id)
        if tracker is None:
            return

        store.update(task_id, status="running")

        try:
            from datetime import datetime, timezone, timedelta

            from docforge.usecases.parse_pdf import parse_pdf
            from docforge.web.sse import EVT_DONE, EVT_PROFILING, progress_line_to_sse

            tracker.push_stage(EVT_PROFILING, "문서 분석 시작...")

            # 페이지 총 수를 progress 로그에서 추출
            total_pages_ref: list[int] = [0]

            def _on_progress(msg: str) -> None:
                progress_line_to_sse(tracker, msg)
                # [page] N/M 형식에서 총 페이지 수 추출
                if msg.strip().startswith("[page]"):
                    parts = msg.strip()[len("[page]"):].strip().split("/")
                    if len(parts) == 2:
                        try:
                            total_pages_ref[0] = int(parts[1])
                        except ValueError:
                            pass

            def _on_page_done(page_num: int, page_md: str) -> None:
                tracker.push_page_result(page_num, total_pages_ref[0], page_md)

            result = parse_pdf(
                pdf_path,
                on_progress=_on_progress,
                on_page_done=_on_page_done,
            )

            KST = timezone(timedelta(hours=9))
            completed_at = datetime.now(KST).isoformat()

            # Persist markdown
            md_path = pdf_path.parent / (pdf_path.stem + ".md")
            md_path.write_text(result.markdown, encoding="utf-8")

            # Serialize metadata and stats (frozen dataclasses -> dict)
            metadata_dict = _serialize_metadata(result.metadata)
            stats_dict = _serialize_stats(result.stats)

            store.update(
                task_id,
                status="done",
                progress="완료",
                progress_pct=100,
                completed_at=completed_at,
                markdown=result.markdown,
                metadata=metadata_dict,
                stats=stats_dict,
                md_path=str(md_path),
            )

            # Persist result.json
            store.save_result(task_id, {
                "markdown": result.markdown,
                "metadata": metadata_dict,
                "stats": stats_dict,
                "completed_at": completed_at,
            })

            # Save original version (v0)
            store.save_version(task_id, result.markdown, "original")

            tracker.push_stage(EVT_DONE, "변환 완료!")
            tracker.mark_done()

        except Exception as exc:
            error_msg = str(exc)
            store.update(task_id, status="error", error=error_msg, progress_pct=0)
            tracker.push_error(f"파싱 오류: {error_msg}")

        finally:
            _remove_tracker(task_id)


def _estimate_pages(pdf_path: Path) -> int:
    """Quick page count estimate without full parsing — returns 1 on failure."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return 1


def _serialize_metadata(metadata: object) -> dict:
    """Convert a frozen Metadata dataclass to a JSON-serializable dict."""
    try:
        d = dataclasses.asdict(metadata)  # type: ignore[arg-type]
        return d
    except TypeError:
        return {}


def _serialize_stats(stats: object) -> dict:
    """Convert a frozen ParseStats dataclass to a JSON-serializable dict."""
    try:
        d = dataclasses.asdict(stats)  # type: ignore[arg-type]
        return d
    except TypeError:
        return {}
