"""VLM-based image captioning for ParsedImage alt-text generation.

Walks a list of ``ParsedImage`` objects and, for each one that has actual
image bytes, calls ``VisionLLMEngine.describe_image()`` to generate a
concise alt-text. The result is a new list of ``ParsedImage`` with
``alt_text`` populated — inputs are never mutated (frozen dataclasses).

Graceful degradation: when the VLM call fails or the image has no bytes,
the original ``ParsedImage`` is kept as-is.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from docforge.domain.models import ParsedImage

if TYPE_CHECKING:
    from docforge.domain.ports import VisionLLMEngine

logger = logging.getLogger(__name__)


def caption_images(
    images: list[ParsedImage],
    vlm_engine: "VisionLLMEngine",
    prompt_hint: str = "",
) -> list[ParsedImage]:
    """Generate VLM alt-text for images that have actual bytes.

    Args:
        images: Images extracted for the current page.
        vlm_engine: A ``VisionLLMEngine`` that supports ``describe_image()``.
        prompt_hint: Optional domain hint (e.g. ``"보험약관"``).

    Returns:
        New list of ``ParsedImage`` with ``alt_text`` populated where
        the VLM produced a non-empty description. Images without bytes
        or where the VLM fails are returned unchanged.
    """
    if not images:
        return []

    out: list[ParsedImage] = []
    for image in images:
        if not image.data:
            out.append(image)
            continue
        try:
            alt_text = vlm_engine.describe_image(
                image_data=image.data,
                format=image.format,
                prompt_hint=prompt_hint,
            )
        except Exception:
            logger.warning(
                "VLM captioning failed for image %s on page %d",
                image.block_id,
                image.page_num,
                exc_info=True,
            )
            out.append(image)
            continue

        if alt_text:
            out.append(replace(image, alt_text=alt_text))
        else:
            out.append(image)

    return out
