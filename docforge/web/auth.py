"""X-Internal-Key authentication middleware for /v1/ API routes."""

from __future__ import annotations

import os

from flask import jsonify, request


def register_auth(app):
    """Register before_request hook that enforces X-Internal-Key on /v1/ routes."""

    @app.before_request
    def _check_internal_key():
        if not request.path.startswith("/v1/"):
            return None

        internal_key = os.environ.get("DOCFORGE_INTERNAL_KEY", "")
        if not internal_key:
            return None

        provided = request.headers.get("X-Internal-Key", "")
        if not provided:
            return jsonify({
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "X-Internal-Key 헤더가 필요합니다.",
                },
            }), 401

        if provided != internal_key:
            return jsonify({
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "유효하지 않은 인증키입니다.",
                },
            }), 401

        return None
