"""Flask application factory for DocForge web GUI."""

from __future__ import annotations

import atexit
import os
from pathlib import Path

from flask import Flask, request


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

    # --- CORS ---
    _register_cors(app)

    # --- X-Internal-Key auth for /v1/ routes ---
    from docforge.web.auth import register_auth
    register_auth(app)

    # --- Legacy GUI + API routes ---
    from docforge.web.routes import bp
    app.register_blueprint(bp)

    # --- V1 API routes ---
    from docforge.web.v1_routes import v1_bp
    app.register_blueprint(v1_bp)

    # Initialize worker queue
    from docforge.web.worker import init_worker_queue, shutdown_worker_queue

    max_workers = app.config.get("MAX_WORKERS")
    init_worker_queue(max_workers=max_workers)
    atexit.register(shutdown_worker_queue)

    return app


def _register_cors(app: Flask) -> None:
    """Register CORS headers based on DOCFORGE_ALLOWED_ORIGINS env var."""
    allowed_raw = os.environ.get("DOCFORGE_ALLOWED_ORIGINS", "")
    allowed_origins: list[str] = (
        [o.strip() for o in allowed_raw.split(",") if o.strip()]
        if allowed_raw
        else []
    )

    @app.after_request
    def _add_cors_headers(response):
        origin = request.headers.get("Origin", "")
        if origin and (not allowed_origins or origin in allowed_origins):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, X-Internal-Key, Authorization"
            )
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, DELETE, OPTIONS"
            )
        return response
