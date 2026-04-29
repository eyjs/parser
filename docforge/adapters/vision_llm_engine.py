"""Qwen2-VL-7B MLX Vision LLM adapter.

Implements VisionLLMEngine Protocol. Apple Silicon only.
Lazy-loads the model on first use to avoid startup cost.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage

logger = logging.getLogger(__name__)

_MODEL_ID = "mlx-community/Qwen2-VL-7B-Instruct-4bit"

_PROMPT_TEMPLATE = """\
다음 문서 페이지 이미지를 읽고, 정확한 텍스트를 추출하세요.
도메인: {domain_hint}
요구사항:
- 원문 텍스트를 그대로 추출 (요약/변형 금지)
- 표는 행|열 구조로 표현
- 조항 번호(제1조, 제2항 등) 정확히 유지
- 출력: 추출된 텍스트만 (설명 없이)
"""


class Qwen2VLMLXEngine:
    """VisionLLMEngine implementation — Qwen2-VL-7B MLX."""

    def __init__(self, model_id: str = _MODEL_ID, max_new_tokens: int = 2048):
        self._model_id = model_id
        self._max_new_tokens = max_new_tokens
        self._model: object | None = None
        self._processor: object | None = None
        self._load_lock = threading.Lock()

    def is_available(self) -> bool:
        try:
            import mlx.core  # noqa: F401
            from mlx_vlm import load  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            from mlx_vlm import load
            self._model, self._processor = load(self._model_id)

    def correct_page(
        self,
        image: RawImage,
        ocr_blocks: list[TextBlock],
        prompt_hint: str = "보험약관",
    ) -> list[TextBlock]:
        self._ensure_loaded()

        prompt = _PROMPT_TEMPLATE.format(domain_hint=prompt_hint or "문서")
        if ocr_blocks:
            ocr_preview = " ".join(b.text for b in ocr_blocks[:10])
            prompt += f"\n참고 OCR 텍스트(일부): {ocr_preview[:200]}"

        pil_image = _raw_image_to_pil(image)
        corrected_text = self._run_inference(pil_image, prompt)
        return _text_to_blocks(corrected_text, image)

    def _run_inference(self, pil_image: object, prompt: str) -> str:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        config = load_config(self._model_id)
        formatted = apply_chat_template(
            self._processor, config, prompt, num_images=1,
        )
        result = generate(
            self._model, self._processor, pil_image,
            formatted, max_tokens=self._max_new_tokens, verbose=False,
        )
        return result


def _raw_image_to_pil(image: RawImage) -> object:
    from PIL import Image
    data = image.data
    if image.channels == 1:
        return Image.fromarray(data.squeeze(), mode="L")
    return Image.fromarray(data.astype(np.uint8), mode="RGB")


def _text_to_blocks(text: str, image: RawImage) -> list[TextBlock]:
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
