"""Flask application factory for DocForge web GUI."""

from __future__ import annotations

import atexit
from pathlib import Path

from flask import Flask


def create_app(upload_dir: Path | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        static_folder=str(Path(__file__).parent / "static"),
        template_folder=str(Path(__file__).parent / "templates"),
    )

    if upload_dir is None:
        upload_dir = Path.cwd() / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    app.config["UPLOAD_DIR"] = str(upload_dir)
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB

    from docforge.web.routes import bp
    app.register_blueprint(bp)

    # Initialize worker queue
    from docforge.web.worker import init_worker_queue, shutdown_worker_queue

    max_workers = app.config.get("MAX_WORKERS")
    init_worker_queue(max_workers=max_workers)
    atexit.register(shutdown_worker_queue)

    return app
