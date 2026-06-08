"""Remote Apple Vision OCR adapter — calls host OCR service via HTTP.

Used when the parser runs inside a Docker container and the macOS host
exposes Apple Vision OCR as an HTTP service (ocr_service.py).
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from docforge.adapters.host_health import TTLAvailability, probe_health
from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo

logger = logging.getLogger(__name__)

_DEFAULT_OCR_SERVICE_URL = "http://host.docker.internal:5052"

# Shortened OCR call timeout (P2-1, defect F): a dead/slow host previously blocked
# a single parse thread for up to 60s per page. 30s is generous for a real OCR
# response yet bounds the worst case; override via DOCFORGE_OCR_CALL_TIMEOUT_SEC.
_DEFAULT_OCR_CALL_TIMEOUT_SEC = 30.0


def _get_service_url() -> str:
    return os.environ.get("DOCFORGE_OCR_SERVICE_URL", _DEFAULT_OCR_SERVICE_URL)


def _get_call_timeout_sec() -> float:
    raw = os.environ.get("DOCFORGE_OCR_CALL_TIMEOUT_SEC")
    if not raw:
        return _DEFAULT_OCR_CALL_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid DOCFORGE_OCR_CALL_TIMEOUT_SEC=%r, using default %.0fs",
            raw,
            _DEFAULT_OCR_CALL_TIMEOUT_SEC,
        )
        return _DEFAULT_OCR_CALL_TIMEOUT_SEC
    return value if value > 0 else _DEFAULT_OCR_CALL_TIMEOUT_SEC


class AppleVisionRemoteEngine:
    """OCR engine that delegates to a remote Apple Vision service on the host."""

    def __init__(self) -> None:
        self._url = _get_service_url().rstrip("/")
        # G23: TTL re-probe instead of a permanent cache — a restarted host OCR
        # service is re-detected on the next call after the TTL elapses, so the
        # pipeline self-recovers instead of staying False forever.
        self._availability = TTLAvailability()

    def _probe(self) -> bool:
        return probe_health(self._url, timeout=3.0)

    def is_available(self) -> bool:
        return self._availability.is_available(self._probe)

    def recognize(self, image: Any) -> list[TextBlock]:
        if not self.is_available():
            return []

        try:
            return self._call_remote(image)
        except Exception as exc:
            # A failed call likely means the host went down mid-use — invalidate
            # so the next is_available() re-probes immediately (faster recovery).
            self._availability.invalidate()
            logger.warning("Remote OCR call failed: %s", exc, exc_info=True)
            return []

    def _call_remote(self, image: Any) -> list[TextBlock]:
        import numpy as np
        from PIL import Image

        from docforge.domain.value_objects import RawImage

        if isinstance(image, RawImage):
            pil_img = Image.fromarray(image.data)
        elif isinstance(image, np.ndarray):
            pil_img = Image.fromarray(image)
        elif isinstance(image, Image.Image):
            pil_img = image
        else:
            raise TypeError(f"Unsupported image type: {type(image)}")

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        boundary = "----DocForgeOCRBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="page.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + img_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{self._url}/recognize",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=_get_call_timeout_sec()) as resp:
            data = json.loads(resp.read())

        blocks: list[TextBlock] = []
        for item in data.get("blocks", []):
            blocks.append(TextBlock(
                text=item["text"],
                bbox=BBox(
                    x0=item["bbox"]["x0"],
                    y0=item["bbox"]["y0"],
                    x1=item["bbox"]["x1"],
                    y1=item["bbox"]["y1"],
                ),
                font=FontInfo(name="apple_vision_remote", size=0.0, is_bold=False),
                block_type=BlockType.TEXT,
                confidence=item.get("confidence", 1.0),
            ))

        return blocks
