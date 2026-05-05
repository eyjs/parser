"""Cloud Vision LLM adapters -- OpenAI GPT-4o and Anthropic Claude.

Implements the ``VisionLLMEngine`` protocol so the pipeline can fall back
to cloud providers when the local Qwen2-VL MLX engine is unavailable
(e.g. on Linux servers without Apple Silicon).

API keys are read from environment variables:
  - ``OPENAI_API_KEY`` for OpenAI GPT-4o vision
  - ``ANTHROPIC_API_KEY`` for Anthropic Claude vision
"""

from __future__ import annotations

import base64
import io
import logging
import os
from typing import TYPE_CHECKING

import numpy as np

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TEXT_PROMPT = """\
다음 문서 페이지 이미지를 읽고, 정확한 텍스트를 추출하세요.
도메인: {domain_hint}
요구사항:
- 원문 텍스트를 그대로 추출 (요약/변형 금지)
- 표는 행|열 구조로 표현
- 조항 번호(제1조, 제2항 등) 정확히 유지
- 출력: 추출된 텍스트만 (설명 없이)
"""

_CAPTION_PROMPT = """\
이 이미지의 내용을 간결하게 설명하세요.
도메인: {domain_hint}
요구사항:
- 문서 내 그림/차트/다이어그램이면 핵심 내용 요약
- 로고/아이콘이면 무엇인지 식별
- 한국어로 답변
- 1-2문장으로 간결하게
- 출력: 설명만 (접두어 없이)
"""


class CloudVisionEngine:
    """Cloud VLM adapter -- OpenAI GPT-4o vision or Anthropic Claude vision.

    Provider selection:
      - ``"auto"``: tries OpenAI first (if key present), then Anthropic.
      - ``"openai"``: OpenAI only.
      - ``"anthropic"``: Anthropic only.
    """

    def __init__(self, provider: str = "auto") -> None:
        self._provider = provider
        self._resolved_provider: str | None = None

    def is_available(self) -> bool:
        """True when at least one cloud provider has a valid API key."""
        return self._resolve_provider() is not None

    def correct_page(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str = "",
    ) -> list[TextBlock]:
        """Extract text from a page image using cloud VLM."""
        provider = self._resolve_provider()
        if provider is None:
            return list(ocr_blocks)

        image_bytes = _raw_image_to_bytes(image)
        prompt = _TEXT_PROMPT.format(domain_hint=prompt_hint or "문서")
        if ocr_blocks:
            ocr_preview = " ".join(b.text for b in ocr_blocks[:10])
            prompt += f"\n참고 OCR 텍스트(일부): {ocr_preview[:200]}"

        try:
            text = self._call_vision_api(provider, image_bytes, "png", prompt)
        except Exception:
            logger.warning("Cloud VLM correct_page failed", exc_info=True)
            return list(ocr_blocks)

        return _text_to_blocks(text, image)

    def describe_image(
        self,
        image_data: bytes,
        format: str = "png",
        prompt_hint: str = "",
    ) -> str:
        """Generate alt-text for an image using cloud VLM."""
        if not image_data:
            return ""
        provider = self._resolve_provider()
        if provider is None:
            return ""

        prompt = _CAPTION_PROMPT.format(domain_hint=prompt_hint or "문서")
        try:
            return self._call_vision_api(provider, image_data, format, prompt).strip()
        except Exception:
            logger.warning("Cloud VLM describe_image failed", exc_info=True)
            return ""

    # -- internal -------------------------------------------------------------

    def _resolve_provider(self) -> str | None:
        """Determine which cloud provider to use based on available API keys."""
        if self._resolved_provider is not None:
            return self._resolved_provider

        if self._provider in ("auto", "openai"):
            if os.environ.get("OPENAI_API_KEY"):
                self._resolved_provider = "openai"
                return "openai"

        if self._provider in ("auto", "anthropic"):
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._resolved_provider = "anthropic"
                return "anthropic"

        return None

    def _call_vision_api(
        self,
        provider: str,
        image_data: bytes,
        fmt: str,
        prompt: str,
    ) -> str:
        """Dispatch to the appropriate cloud provider."""
        if provider == "openai":
            return self._call_openai(image_data, fmt, prompt)
        if provider == "anthropic":
            return self._call_anthropic(image_data, fmt, prompt)
        raise ValueError(f"Unknown provider: {provider}")

    def _call_openai(self, image_data: bytes, fmt: str, prompt: str) -> str:
        """Call OpenAI GPT-4o vision API."""
        from openai import OpenAI

        client = OpenAI()
        media_type = "image/jpeg" if fmt in ("jpg", "jpeg") else "image/png"
        b64 = base64.b64encode(image_data).decode("utf-8")
        data_uri = f"data:{media_type};base64,{b64}"

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri, "detail": "high"},
                        },
                    ],
                },
            ],
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""

    def _call_anthropic(self, image_data: bytes, fmt: str, prompt: str) -> str:
        """Call Anthropic Claude vision API."""
        from anthropic import Anthropic

        client = Anthropic()
        media_type = "image/jpeg" if fmt in ("jpg", "jpeg") else "image/png"
        b64 = base64.b64encode(image_data).decode("utf-8")

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        )
        if response.content and len(response.content) > 0:
            return response.content[0].text
        return ""


# -- helpers ------------------------------------------------------------------


def _raw_image_to_bytes(image: RawImage) -> bytes:
    """Convert a RawImage to PNG bytes for API upload."""
    from PIL import Image as PILImage

    data = image.data
    if image.channels == 1:
        pil_img = PILImage.fromarray(data.squeeze(), mode="L")
    else:
        pil_img = PILImage.fromarray(data.astype(np.uint8), mode="RGB")

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return buf.getvalue()


def _text_to_blocks(text: str, image: RawImage) -> list[TextBlock]:
    """Convert extracted text to TextBlock list."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    full_bbox = BBox(x0=0.0, y0=0.0, x1=float(image.width), y1=float(image.height))
    default_font = FontInfo(size=10.0, is_bold=False, name="unknown")

    return [
        TextBlock(
            text=line,
            bbox=full_bbox,
            font=default_font,
            block_type=BlockType.TEXT,
            heading_level=0,
            confidence=0.9,
        )
        for line in lines
    ]
