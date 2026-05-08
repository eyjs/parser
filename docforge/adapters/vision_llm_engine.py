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


class Qwen2VLMLXEngine:
    """VisionLLMEngine implementation — Qwen2-VL-7B MLX."""

    def __init__(self, model_id: str = _MODEL_ID, max_new_tokens: int = 2048):
        self._model_id = model_id
        self._max_new_tokens = max_new_tokens
        self._model: object | None = None
        self._processor: object | None = None
        self._load_lock = threading.Lock()

    def is_available(self) -> bool:
        """Check mlx packages AND model cache existence."""
        try:
            import mlx.core  # noqa: F401
            from mlx_vlm import load  # noqa: F401
        except ImportError:
            return False
        # Verify model cache exists in HuggingFace hub
        return _check_model_cache(self._model_id)

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

    def describe_image(
        self,
        image_data: bytes,
        format: str = "png",
        prompt_hint: str = "",
        block_type: str = "",
        context_text: str = "",
        bbox_info: str = "",
    ) -> str:
        """Generate alt-text for an image using local Qwen2-VL.

        The ``block_type``, ``context_text``, and ``bbox_info`` parameters
        are accepted for Protocol compatibility but ignored by the local
        model (enriched input is only effective with cloud VLM providers).
        """
        if not image_data:
            return ""
        try:
            self._ensure_loaded()
            pil_image = _bytes_to_pil(image_data, format)
            prompt = _CAPTION_PROMPT.format(
                domain_hint=prompt_hint or "문서",
            )
            return self._run_inference(pil_image, prompt).strip()
        except Exception:
            logger.warning("Qwen2-VL describe_image failed", exc_info=True)
            return ""

    def _run_inference(self, pil_image: object, prompt: str) -> str:
        import tempfile
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        config = load_config(self._model_id)
        formatted = apply_chat_template(
            self._processor, config, prompt, num_images=1,
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            pil_image.save(tmp, format="PNG")
            tmp_path = tmp.name
        try:
            result = generate(
                self._model, self._processor, formatted,
                tmp_path, max_tokens=self._max_new_tokens, verbose=False,
            )
        finally:
            import os
            os.unlink(tmp_path)
        return result.text if hasattr(result, "text") else str(result)


def _raw_image_to_pil(image: RawImage) -> object:
    from PIL import Image
    data = image.data
    if image.channels == 1:
        return Image.fromarray(data.squeeze(), mode="L")
    return Image.fromarray(data.astype(np.uint8), mode="RGB")


def _check_model_cache(model_id: str) -> bool:
    """Check whether the HuggingFace model cache directory exists.

    This prevents ``is_available()`` from returning True when the model
    has never been downloaded, which would trigger a multi-GB download on
    first inference.
    """
    import os
    from pathlib import Path

    cache_dir = Path(
        os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"),
    ) / "hub"
    # HF stores models as "models--org--name"
    model_dir_name = "models--" + model_id.replace("/", "--")
    model_path = cache_dir / model_dir_name
    if model_path.is_dir():
        # Check that at least one snapshot exists
        snapshots = model_path / "snapshots"
        if snapshots.is_dir() and any(snapshots.iterdir()):
            return True
    return False


def _bytes_to_pil(data: bytes, fmt: str = "png") -> object:
    """Convert raw image bytes to a PIL Image."""
    import io
    from PIL import Image
    return Image.open(io.BytesIO(data))


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
