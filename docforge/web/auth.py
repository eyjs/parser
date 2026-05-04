"""Authentication middleware for /v1/ API routes.

Distinguishes internal (Docker network) vs external (Cloudflare Tunnel) requests
using the CF-Connecting-IP header that Cloudflare automatically adds.

- Internal request (no CF-Connecting-IP): auth fully skipped
- External request (CF-Connecting-IP present):
  - DOCFORGE_EXTERNAL_AUTH=false (default): allow all (no auth)
  - DOCFORGE_EXTERNAL_AUTH=true: require X-Internal-Key header
"""

from __future__ import annotations

import logging
import os

from flask import jsonify, request

logger = logging.getLogger(__name__)


def register_auth(app):
    """Register before_request hook that enforces auth on /v1/ routes."""

    @app.before_request
    def _check_auth():
        if not request.path.startswith("/v1/"):
            return None

        # --- Determine request origin ---
        cf_connecting_ip = request.headers.get("CF-Connecting-IP")

        if not cf_connecting_ip:
            # Internal request (Docker container-to-container, no Cloudflare proxy)
            # Auth completely skipped for internal services (KMS, ai-platform, etc.)
            logger.debug(
                "Internal request to %s: auth skipped (no CF-Connecting-IP)",
                request.path,
            )
            return None

        # --- External request (Cloudflare Tunnel) ---
        external_auth_enabled = (
            os.environ.get("DOCFORGE_EXTERNAL_AUTH", "false").lower() == "true"
        )

        if not external_auth_enabled:
            # External auth disabled — allow all external requests without auth.
            # This is the default for the initial rollout. Enable
            # DOCFORGE_EXTERNAL_AUTH=true when ready to enforce authentication
            # on Tunnel-originated requests.
            logger.debug(
                "External request from %s to %s: auth disabled "
                "(DOCFORGE_EXTERNAL_AUTH=false)",
                cf_connecting_ip,
                request.path,
            )
            return None

        # --- External auth enabled: require X-Internal-Key ---
        internal_key = os.environ.get("DOCFORGE_INTERNAL_KEY", "")
        if not internal_key:
            # No key configured even though auth is enabled — allow through
            # to avoid lockout. Log a warning so operators notice.
            logger.warning(
                "DOCFORGE_EXTERNAL_AUTH=true but DOCFORGE_INTERNAL_KEY is empty; "
                "allowing request from %s",
                cf_connecting_ip,
            )
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

        logger.debug(
            "External request from %s to %s: authenticated via X-Internal-Key",
            cf_connecting_ip,
            request.path,
        )
        return None
