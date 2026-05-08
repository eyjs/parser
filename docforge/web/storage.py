"""SQLite-backed storage for parsing tasks and results."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import local

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_VALID_COLUMNS = frozenset({
    "filename", "status", "progress", "progress_pct",
    "created_at", "completed_at", "queued_at", "markdown",
    "metadata", "stats", "error", "pdf_path", "md_path",
})

_JSON_COLUMNS = frozenset({"metadata", "stats"})


@dataclass
class TaskRecord:
    task_id: str
    filename: str
    status: str  # queued, running, done, error, cancelled
    progress: str = ""
    progress_pct: int = 0
    created_at: str = ""
    completed_at: str = ""
    queued_at: str = ""
    markdown: str = ""
    metadata: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    error: str = ""
    pdf_path: str = ""
    md_path: str = ""


class TaskStore:
    """SQLite-backed task store. Thread-safe across Gunicorn workers via WAL mode."""

    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "tasks.db"
        self._local = local()
        self._init_db()
        self._migrate_from_json()
        self._recover_stale_tasks()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id     TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'queued',
                progress    TEXT DEFAULT '',
                progress_pct INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                completed_at TEXT DEFAULT '',
                queued_at   TEXT DEFAULT '',
                markdown    TEXT DEFAULT '',
                metadata    TEXT DEFAULT '{}',
                stats       TEXT DEFAULT '{}',
                error       TEXT DEFAULT '',
                pdf_path    TEXT DEFAULT '',
                md_path     TEXT DEFAULT ''
            )
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, filename: str, pdf_path: str) -> TaskRecord:
        task_id = str(uuid.uuid4())
        now = datetime.now(KST).isoformat()
        self._conn().execute(
            "INSERT INTO tasks"
            " (task_id, filename, status, created_at, queued_at, pdf_path)"
            " VALUES (?, ?, 'queued', ?, ?, ?)",
            (task_id, filename, now, now, pdf_path),
        )
        self._conn().commit()
        return TaskRecord(
            task_id=task_id, filename=filename, status="queued",
            created_at=now, queued_at=now, pdf_path=pdf_path,
        )

    def get(self, task_id: str) -> TaskRecord | None:
        row = self._conn().execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,),
        ).fetchone()
        return self._to_record(row) if row else None

    def update(self, task_id: str, **kwargs: object) -> None:
        safe = {k: v for k, v in kwargs.items() if k in _VALID_COLUMNS}
        if not safe:
            return
        for col in _JSON_COLUMNS & safe.keys():
            if isinstance(safe[col], dict):
                safe[col] = json.dumps(safe[col], ensure_ascii=False)
        cols = list(safe.keys())
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        values = [safe[c] for c in cols] + [task_id]
        conn = self._conn()
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE task_id = ?", values)
        conn.commit()

    def list_all(self) -> list[TaskRecord]:
        rows = self._conn().execute(
            "SELECT * FROM tasks ORDER BY created_at DESC",
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def delete(self, task_id: str) -> bool:
        conn = self._conn()
        cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Result JSON persistence (file-based)
    # ------------------------------------------------------------------

    def save_result(self, task_id: str, result_data: dict) -> Path | None:
        record = self.get(task_id)
        if record is None:
            return None
        task_dir = self._dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        result_path = task_dir / "result.json"
        try:
            tmp = result_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(result_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(result_path)
            return result_path
        except OSError:
            logger.warning("Failed to save result.json for %s", task_id, exc_info=True)
            return None

    def load_result(self, task_id: str) -> dict | None:
        result_path = self._dir / task_id / "result.json"
        if not result_path.exists():
            return None
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load result.json for %s", task_id, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Version management (file-based)
    # ------------------------------------------------------------------

    def save_version(self, task_id: str, markdown: str, label: str = "") -> str | None:
        task_dir = self._dir / task_id / "versions"
        task_dir.mkdir(parents=True, exist_ok=True)
        version_id = uuid.uuid4().hex[:8]
        if not label:
            label = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
        version_name = f"v_{version_id}_{label}.md"
        try:
            (task_dir / version_name).write_text(markdown, encoding="utf-8")
            return version_name
        except OSError:
            logger.warning("Failed to save version for %s", task_id, exc_info=True)
            return None

    def list_versions(self, task_id: str) -> list[dict]:
        task_dir = self._dir / task_id / "versions"
        if not task_dir.exists():
            return []
        files = sorted(task_dir.glob("v*_*.md"), key=lambda f: f.stat().st_mtime)
        return [{"name": f.name, "path": str(f), "size": f.stat().st_size} for f in files]

    def get_version_content(self, task_id: str, version_name: str) -> str | None:
        version_path = self._dir / task_id / "versions" / version_name
        try:
            version_path.resolve().relative_to(
                (self._dir / task_id / "versions").resolve(),
            )
        except ValueError:
            return None
        if not version_path.exists():
            return None
        try:
            return version_path.read_text(encoding="utf-8")
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_record(self, row: sqlite3.Row) -> TaskRecord:
        metadata: dict = {}
        stats: dict = {}
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            stats = json.loads(row["stats"]) if row["stats"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        return TaskRecord(
            task_id=row["task_id"],
            filename=row["filename"],
            status=row["status"],
            progress=row["progress"] or "",
            progress_pct=row["progress_pct"] or 0,
            created_at=row["created_at"] or "",
            completed_at=row["completed_at"] or "",
            queued_at=row["queued_at"] or "",
            markdown=row["markdown"] or "",
            metadata=metadata,
            stats=stats,
            error=row["error"] or "",
            pdf_path=row["pdf_path"] or "",
            md_path=row["md_path"] or "",
        )

    def _recover_stale_tasks(self) -> None:
        now = datetime.now(KST).isoformat()
        conn = self._conn()
        cur = conn.execute(
            "UPDATE tasks SET status = 'error',"
            " error = '서버 재시작으로 중단됨', completed_at = ?"
            " WHERE status IN ('running', 'queued')",
            (now,),
        )
        if cur.rowcount > 0:
            logger.info("Recovered %d stale tasks after restart", cur.rowcount)
        conn.commit()

    def _migrate_from_json(self) -> None:
        json_path = self._dir / "tasks.json"
        if not json_path.exists():
            return
        try:
            data: dict[str, dict] = json.loads(
                json_path.read_text(encoding="utf-8"),
            )
            conn = self._conn()
            migrated = 0
            for tid, raw in data.items():
                if conn.execute(
                    "SELECT 1 FROM tasks WHERE task_id = ?", (tid,),
                ).fetchone():
                    continue
                if raw.get("status") == "pending":
                    raw["status"] = "queued"
                if "queued_at" not in raw:
                    raw["queued_at"] = raw.get("created_at", "")
                conn.execute(
                    "INSERT INTO tasks"
                    " (task_id, filename, status, progress, progress_pct,"
                    "  created_at, completed_at, queued_at, markdown,"
                    "  metadata, stats, error, pdf_path, md_path)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tid,
                        raw.get("filename", ""),
                        raw.get("status", "queued"),
                        raw.get("progress", ""),
                        raw.get("progress_pct", 0),
                        raw.get("created_at", ""),
                        raw.get("completed_at", ""),
                        raw.get("queued_at", ""),
                        raw.get("markdown", ""),
                        json.dumps(raw.get("metadata", {}), ensure_ascii=False),
                        json.dumps(raw.get("stats", {}), ensure_ascii=False),
                        raw.get("error", ""),
                        raw.get("pdf_path", ""),
                        raw.get("md_path", ""),
                    ),
                )
                migrated += 1
            conn.commit()
            if migrated > 0:
                logger.info("Migrated %d tasks from tasks.json to SQLite", migrated)
            json_path.rename(json_path.with_suffix(".json.bak"))
            logger.info("Renamed tasks.json → tasks.json.bak")
        except Exception:
            logger.warning("Failed to migrate from tasks.json", exc_info=True)
