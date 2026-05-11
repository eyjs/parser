"""Tests for the export API endpoint (task-003).

Verifies:
- inline format: base64 image replacement, no /uploads/ leakage
- zip format: valid zip, relative paths, no /uploads/ leakage
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from docforge.web.routes import _process_export_markdown


# ---------------------------------------------------------------------------
# Unit tests for _process_export_markdown helper
# ---------------------------------------------------------------------------

class TestProcessExportMarkdown:
    """Test the markdown processing helper directly."""

    def _create_image(self, tmp_path: Path, task_id: str, name: str, content: bytes = b"\x89PNG") -> Path:
        img_dir = tmp_path / task_id / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        img_path = img_dir / name
        img_path.write_bytes(content)
        return img_path

    def test_inline_replaces_image_with_base64(self, tmp_path: Path) -> None:
        """inline format: images become data URIs."""
        task_id = "test-task-001"
        img_bytes = b"\x89PNG\r\n\x1a\n fake png data"
        self._create_image(tmp_path, task_id, "page-1-img-0.png", img_bytes)

        md = f"# Title\n\n![figure](/uploads/{task_id}/images/page-1-img-0.png)\n\nSome text."
        processed, files = _process_export_markdown(md, task_id, "inline", tmp_path)

        assert "/uploads/" not in processed
        expected_b64 = base64.b64encode(img_bytes).decode("ascii")
        assert f"data:image/png;base64,{expected_b64}" in processed
        assert len(files) == 0

    def test_inline_no_uploads_path_leak(self, tmp_path: Path) -> None:
        """inline format: /uploads/ paths must not survive."""
        task_id = "test-task-002"
        self._create_image(tmp_path, task_id, "chart.png")

        md = (
            f"![chart](/uploads/{task_id}/images/chart.png)\n"
            f"See also ![fig2](/uploads/{task_id}/images/chart.png)"
        )
        processed, _ = _process_export_markdown(md, task_id, "inline", tmp_path)

        assert "/uploads/" not in processed

    def test_inline_missing_image_preserves_original(self, tmp_path: Path) -> None:
        """inline format: missing image files keep original reference."""
        task_id = "test-task-003"
        md = f"![missing](/uploads/{task_id}/images/nonexistent.png)"
        processed, _ = _process_export_markdown(md, task_id, "inline", tmp_path)

        # Original reference preserved when file doesn't exist
        assert f"/uploads/{task_id}/images/nonexistent.png" in processed

    def test_zip_uses_relative_paths(self, tmp_path: Path) -> None:
        """zip format: image references use ./images/ relative paths."""
        task_id = "test-task-004"
        self._create_image(tmp_path, task_id, "page-1-img-0.png")

        md = f"![alt](/uploads/{task_id}/images/page-1-img-0.png)"
        processed, files = _process_export_markdown(md, task_id, "zip", tmp_path)

        assert "./images/page-1-img-0.png" in processed
        assert "/uploads/" not in processed
        assert len(files) == 1
        assert files[0][0] == "page-1-img-0.png"

    def test_zip_no_uploads_path_leak(self, tmp_path: Path) -> None:
        """zip format: /uploads/ paths must not survive."""
        task_id = "test-task-005"
        self._create_image(tmp_path, task_id, "img-a.png")
        self._create_image(tmp_path, task_id, "img-b.jpg")

        md = (
            f"![a](/uploads/{task_id}/images/img-a.png)\n"
            f"![b](/uploads/{task_id}/images/img-b.jpg)"
        )
        processed, files = _process_export_markdown(md, task_id, "zip", tmp_path)

        assert "/uploads/" not in processed
        assert len(files) == 2


# ---------------------------------------------------------------------------
# Integration tests using Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path: Path):
    """Create a minimal Flask app with the docforge blueprint."""
    from flask import Flask
    from docforge.web.routes import bp

    app = Flask(__name__)
    app.config["UPLOAD_DIR"] = str(tmp_path)
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


class TestExportEndpoint:
    """Integration tests for GET /api/tasks/<task_id>/export."""

    def _setup_task(self, tmp_path: Path, task_id: str, filename: str = "doc.pdf") -> None:
        """Create a done task in the store with markdown and images."""
        from docforge.web.storage import TaskStore

        store = TaskStore(tmp_path)
        record = store.create(filename, str(tmp_path / task_id / filename))
        # We need to use the generated task_id, but for testing we'll
        # directly insert with a known ID
        store._conn().execute(
            "INSERT OR REPLACE INTO tasks"
            " (task_id, filename, status, created_at, markdown, md_path)"
            " VALUES (?, ?, 'done', '2026-01-01', ?, ?)",
            (
                task_id,
                filename,
                f"# Title\n\n![fig](/uploads/{task_id}/images/page-1-img-0.png)\n\nText.",
                str(tmp_path / task_id / f"{Path(filename).stem}.md"),
            ),
        )
        store._conn().commit()

        # Create image file
        img_dir = tmp_path / task_id / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / "page-1-img-0.png").write_bytes(b"\x89PNG\r\n\x1a\n test")

    def test_export_inline_returns_markdown(self, client, tmp_path: Path) -> None:
        """GET /api/tasks/{id}/export?format=inline returns markdown with base64."""
        task_id = "integ-inline-001"
        self._setup_task(tmp_path, task_id)

        resp = client.get(f"/api/tasks/{task_id}/export?format=inline")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/markdown")

        content = resp.data.decode("utf-8")
        assert "/uploads/" not in content
        assert "data:image/png;base64," in content

    def test_export_zip_returns_valid_zip(self, client, tmp_path: Path) -> None:
        """GET /api/tasks/{id}/export?format=zip returns a valid zip."""
        task_id = "integ-zip-001"
        self._setup_task(tmp_path, task_id)

        resp = client.get(f"/api/tasks/{task_id}/export?format=zip")
        assert resp.status_code == 200
        assert "application/zip" in resp.content_type

        buf = io.BytesIO(resp.data)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            # Should contain markdown + images
            md_files = [n for n in names if n.endswith(".md")]
            img_files = [n for n in names if n.startswith("images/")]
            assert len(md_files) == 1
            assert len(img_files) >= 1

            # Verify markdown uses relative paths
            md_content = zf.read(md_files[0]).decode("utf-8")
            assert "./images/" in md_content
            assert "/uploads/" not in md_content

    def test_export_not_found(self, client) -> None:
        """GET /api/tasks/{nonexistent}/export returns 404."""
        resp = client.get("/api/tasks/nonexistent-id/export?format=inline")
        assert resp.status_code == 404

    def test_existing_export_endpoint_still_works(self, client, tmp_path: Path) -> None:
        """Legacy /api/export/<task_id> remains functional."""
        task_id = "integ-legacy-001"
        # Set up task with md_path pointing to a real file
        from docforge.web.storage import TaskStore
        store = TaskStore(tmp_path)
        store._conn().execute(
            "INSERT OR REPLACE INTO tasks"
            " (task_id, filename, status, created_at, md_path)"
            " VALUES (?, ?, 'done', '2026-01-01', ?)",
            (task_id, "report.pdf", str(tmp_path / task_id / "report.md")),
        )
        store._conn().commit()

        md_path = tmp_path / task_id / "report.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Legacy export test", encoding="utf-8")

        resp = client.get(f"/api/export/{task_id}")
        assert resp.status_code == 200
