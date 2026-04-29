"""Local file-based storage for parsing tasks and results."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

KST = timezone(timedelta(hours=9))

_TASKS_FILE = "tasks.json"


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
    """Thread-safe in-memory task store with JSON persistence."""

    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = Lock()
        self._load()

    def create(self, filename: str, pdf_path: str) -> TaskRecord:
        """Create a new task record and persist it."""
        task_id = str(uuid.uuid4())
        now = datetime.now(KST).isoformat()
        record = TaskRecord(
            task_id=task_id,
            filename=filename,
            status="queued",
            created_at=now,
            queued_at=now,
            pdf_path=pdf_path,
        )
        with self._lock:
            self._tasks[task_id] = record
            self._save()
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        """Return the task record for task_id, or None if not found."""
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **kwargs: object) -> None:
        """Update fields on an existing task record and persist."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            for key, value in kwargs.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            self._save()

    def list_all(self) -> list[TaskRecord]:
        """Return all task records sorted by creation time descending."""
        with self._lock:
            records = list(self._tasks.values())
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def delete(self, task_id: str) -> bool:
        """Delete a task record. Returns True if deleted, False if not found."""
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
            self._save()
        return True

    # ------------------------------------------------------------------
    # Result JSON persistence
    # ------------------------------------------------------------------

    def save_result(self, task_id: str, result_data: dict) -> Path | None:
        """Save parse result as JSON to uploads/<task_id>/result.json."""
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
        """Load parse result from uploads/<task_id>/result.json."""
        result_path = self._dir / task_id / "result.json"
        if not result_path.exists():
            return None
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load result.json for %s", task_id, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Version management
    # ------------------------------------------------------------------

    def save_version(self, task_id: str, markdown: str, label: str = "") -> str | None:
        """Save a markdown version file. Returns the version filename or None."""
        task_dir = self._dir / task_id / "versions"
        task_dir.mkdir(parents=True, exist_ok=True)

        existing = sorted(task_dir.glob("v*_*.md"))
        next_num = len(existing)

        if not label:
            ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
            label = ts

        version_name = f"v{next_num}_{label}.md"
        version_path = task_dir / version_name
        try:
            version_path.write_text(markdown, encoding="utf-8")
            return version_name
        except OSError:
            logger.warning("Failed to save version for %s", task_id, exc_info=True)
            return None

    def list_versions(self, task_id: str) -> list[dict]:
        """List all saved versions for a task."""
        task_dir = self._dir / task_id / "versions"
        if not task_dir.exists():
            return []

        versions = []
        for f in sorted(task_dir.glob("v*_*.md")):
            versions.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
            })
        return versions

    def get_version_content(self, task_id: str, version_name: str) -> str | None:
        """Read a specific version file."""
        version_path = self._dir / task_id / "versions" / version_name
        # Prevent path traversal
        try:
            version_path.resolve().relative_to((self._dir / task_id / "versions").resolve())
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

    def _save(self) -> None:
        """Persist all tasks to disk as JSON. Must be called under lock."""
        path = self._dir / _TASKS_FILE
        data = {tid: asdict(record) for tid, record in self._tasks.items()}
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except OSError:
            logger.warning("Failed to persist tasks to disk", exc_info=True)

    def _load(self) -> None:
        """Load persisted tasks from disk. Called once during __init__."""
        path = self._dir / _TASKS_FILE
        if not path.exists():
            return
        try:
            data: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))
            for tid, raw in data.items():
                # Backward compat: add queued_at if missing
                if "queued_at" not in raw:
                    raw["queued_at"] = raw.get("created_at", "")
                # Backward compat: migrate old 'pending' status to 'queued'
                if raw.get("status") == "pending":
                    raw["status"] = "queued"
                self._tasks[tid] = TaskRecord(**raw)
        except (json.JSONDecodeError, TypeError, KeyError):
            # Corrupted persistence — start fresh
            self._tasks = {}
