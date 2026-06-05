"""Remote Apple Vision OCR adapter — calls host OCR service via HTTP.

Used when the parser runs inside a Docker container and the macOS host
exposes Apple Vision OCR as an HTTP service (ocr_service.py).
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.request
import urllib.error
from typing import Any

from docforge.adapters.host_health import TTLAvailability, probe_health
from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo

logger = logging.getLogger(__name__)

_DEFAULT_OCR_SERVICE_URL = "http://host.docker.internal:5052"


def _get_service_url() -> str:
    return os.environ.get("DOCFORGE_OCR_SERVICE_URL", _DEFAULT_OCR_SERVICE_URL)


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
        from PIL import Image
        import numpy as np
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

        with urllib.request.urlopen(req, timeout=60) as resp:
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
