"""Remote VLM adapter — calls host Qwen2-VL service via HTTP.

Used when the parser runs inside a Docker container and the macOS host
exposes Qwen2-VL as an HTTP service (vlm_service.py).
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import numpy as np

from docforge.adapters.host_health import TTLAvailability, probe_health
from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DEFAULT_VLM_SERVICE_URL = "http://host.docker.internal:5053"

# Shortened VLM call timeout (P2-1, defect F): the old 120s ceiling let a
# dead/slow host block a parse thread for two minutes per page. 45s bounds the
# worst case while leaving headroom for a real VLM response; override via
# DOCFORGE_VLM_CALL_TIMEOUT_SEC.
_DEFAULT_VLM_CALL_TIMEOUT_SEC = 45.0


def _get_service_url() -> str:
    return os.environ.get("DOCFORGE_VLM_SERVICE_URL", _DEFAULT_VLM_SERVICE_URL)


def _get_call_timeout_sec() -> float:
    raw = os.environ.get("DOCFORGE_VLM_CALL_TIMEOUT_SEC")
    if not raw:
        return _DEFAULT_VLM_CALL_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid DOCFORGE_VLM_CALL_TIMEOUT_SEC=%r, using default %.0fs",
            raw,
            _DEFAULT_VLM_CALL_TIMEOUT_SEC,
        )
        return _DEFAULT_VLM_CALL_TIMEOUT_SEC
    return value if value > 0 else _DEFAULT_VLM_CALL_TIMEOUT_SEC


class HostVLMEngine:
    """VisionLLMEngine implementation that delegates to a remote Qwen2-VL host service."""

    def __init__(self) -> None:
        self._url = _get_service_url().rstrip("/")
        # G23: TTL re-probe instead of a permanent cache — a restarted host VLM
        # service is re-detected automatically (see host_health.TTLAvailability).
        self._availability = TTLAvailability()

    def _probe(self) -> bool:
        return probe_health(self._url, timeout=5.0)

    def is_available(self) -> bool:
        return self._availability.is_available(self._probe)

    def correct_page(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str = "",
    ) -> list[TextBlock]:
        if not self.is_available():
            return list(ocr_blocks)

        try:
            return self._call_correct(image, ocr_blocks, prompt_hint)
        except Exception as exc:
            # Host likely went down mid-use — re-probe on the next call.
            self._availability.invalidate()
            logger.warning("Remote VLM correct_page failed: %s", exc, exc_info=True)
            return list(ocr_blocks)

    def describe_image(
        self,
        image_data: bytes,
        format: str = "png",
        prompt_hint: str = "",
        block_type: str = "",
        context_text: str = "",
        bbox_info: str = "",
    ) -> str:
        if not image_data or not self.is_available():
            return ""

        try:
            return self._call_describe(image_data, format, prompt_hint, block_type, context_text, bbox_info)
        except Exception as exc:
            self._availability.invalidate()
            logger.warning("Remote VLM describe_image failed: %s", exc, exc_info=True)
            return ""

    def _call_describe(
        self,
        image_data: bytes,
        fmt: str,
        prompt_hint: str,
        block_type: str,
        context_text: str,
        bbox_info: str,
    ) -> str:
        boundary = "----DocForgeVLMBoundary"

        parts = []
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="image.{fmt}"\r\n'
            f"Content-Type: image/{fmt}\r\n\r\n"
        )
        parts.append(image_data)
        for field_name, field_value in [
            ("format", fmt),
            ("prompt_hint", prompt_hint),
            ("block_type", block_type),
            ("context_text", context_text),
            ("bbox_info", bbox_info),
        ]:
            if field_value:
                parts.append(
                    f"\r\n--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'
                    f"{field_value}"
                )
        parts.append(f"\r\n--{boundary}--\r\n")

        body = b""
        for part in parts:
            if isinstance(part, bytes):
                body += part
            else:
                body += part.encode("utf-8")

        req = urllib.request.Request(
            f"{self._url}/describe",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=_get_call_timeout_sec()) as resp:
            data = json.loads(resp.read())

        return data.get("alt_text", "")

    def _call_correct(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str,
    ) -> list[TextBlock]:
        from PIL import Image as PILImage

        data = image.data
        if image.channels == 1:
            pil_img = PILImage.fromarray(data.squeeze(), mode="L")
        else:
            pil_img = PILImage.fromarray(data.astype(np.uint8), mode="RGB")

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        blocks_json = json.dumps([
            {
                "text": b.text,
                "bbox": {"x0": b.bbox.x0, "y0": b.bbox.y0, "x1": b.bbox.x1, "y1": b.bbox.y1},
                "confidence": b.confidence,
            }
            for b in ocr_blocks
        ])

        boundary = "----DocForgeVLMBoundary"
        parts = []
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="page.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        )
        parts.append(img_bytes)
        parts.append(
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="prompt_hint"\r\n\r\n'
            f"{prompt_hint}"
        )
        parts.append(
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="ocr_blocks"\r\n\r\n'
            f"{blocks_json}"
        )
        parts.append(f"\r\n--{boundary}--\r\n")

        body = b""
        for part in parts:
            if isinstance(part, bytes):
                body += part
            else:
                body += part.encode("utf-8")

        req = urllib.request.Request(
            f"{self._url}/correct",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=_get_call_timeout_sec()) as resp:
            resp_data = json.loads(resp.read())

        result_blocks: list[TextBlock] = []
        for item in resp_data.get("blocks", []):
            result_blocks.append(TextBlock(
                text=item["text"],
                bbox=BBox(
                    x0=item["bbox"]["x0"],
                    y0=item["bbox"]["y0"],
                    x1=item["bbox"]["x1"],
                    y1=item["bbox"]["y1"],
                ),
                font=FontInfo(name="host_vlm_remote", size=0.0, is_bold=False),
                block_type=BlockType.TEXT,
                confidence=item.get("confidence", 0.9),
            ))

        return result_blocks
