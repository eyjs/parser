"""Tests for the image VLM captioner module."""

from __future__ import annotations

from dataclasses import replace

import pytest

from docforge.domain.models import ParsedImage
from docforge.domain.value_objects import BBox
from docforge.processing.image_vlm_captioner import caption_images


def _make_image(
    page_num: int = 1,
    block_id: str = "abc",
    data: bytes = b"fake-png",
    alt_text: str | None = None,
) -> ParsedImage:
    return ParsedImage(
        bbox=BBox(x0=0, y0=0, x1=100, y1=100),
        data=data,
        format="png",
        caption=None,
        page_num=page_num,
        block_id=block_id,
        alt_text=alt_text,
    )


class FakeVLMEngine:
    """Fake VisionLLMEngine for testing caption_images."""

    def __init__(
        self,
        description: str = "test caption",
        should_fail: bool = False,
    ) -> None:
        self._description = description
        self._should_fail = should_fail
        self.call_count = 0

    def describe_image(
        self,
        image_data: bytes,
        format: str = "png",
        prompt_hint: str = "",
        block_type: str = "",
        context_text: str = "",
        bbox_info: str = "",
    ) -> str:
        self.call_count += 1
        if self._should_fail:
            raise RuntimeError("VLM failure")
        return self._description

    def correct_page(self, image, ocr_blocks, prompt_hint=""):
        return list(ocr_blocks)

    def is_available(self) -> bool:
        return True


class TestCaptionImages:
    """Test caption_images function."""

    def test_empty_list(self) -> None:
        result = caption_images([], FakeVLMEngine())
        assert result == []

    def test_images_with_data_get_captioned(self) -> None:
        engine = FakeVLMEngine(description="보험 약관 도표")
        images = [_make_image(block_id="img1")]
        result = caption_images(images, engine)

        assert len(result) == 1
        assert result[0].alt_text == "보험 약관 도표"
        assert result[0].block_id == "img1"
        # Original should not be mutated (frozen dataclass)
        assert images[0].alt_text is None
        assert engine.call_count == 1

    def test_images_without_data_skipped(self) -> None:
        engine = FakeVLMEngine()
        images = [_make_image(data=b"")]
        result = caption_images(images, engine)

        assert len(result) == 1
        assert result[0].alt_text is None
        assert engine.call_count == 0

    def test_vlm_failure_keeps_original(self) -> None:
        engine = FakeVLMEngine(should_fail=True)
        images = [_make_image()]
        result = caption_images(images, engine)

        assert len(result) == 1
        assert result[0].alt_text is None
        assert engine.call_count == 1

    def test_mixed_data_and_no_data(self) -> None:
        engine = FakeVLMEngine(description="captioned")
        images = [
            _make_image(block_id="with-data", data=b"real-png"),
            _make_image(block_id="no-data", data=b""),
            _make_image(block_id="also-data", data=b"more-png"),
        ]
        result = caption_images(images, engine)

        assert len(result) == 3
        assert result[0].alt_text == "captioned"
        assert result[1].alt_text is None  # no data, skipped
        assert result[2].alt_text == "captioned"
        assert engine.call_count == 2

    def test_empty_vlm_response_keeps_original(self) -> None:
        engine = FakeVLMEngine(description="")
        images = [_make_image()]
        result = caption_images(images, engine)

        assert len(result) == 1
        assert result[0].alt_text is None

    def test_prompt_hint_forwarded(self) -> None:
        """Verify the prompt_hint parameter is passed through."""
        received_hints: list[str] = []

        class HintCapture:
            def describe_image(
                self, image_data, format="png", prompt_hint="",
                block_type="", context_text="", bbox_info="",
            ):
                received_hints.append(prompt_hint)
                return "ok"

            def correct_page(self, image, ocr_blocks, prompt_hint=""):
                return list(ocr_blocks)

            def is_available(self):
                return True

        images = [_make_image()]
        caption_images(images, HintCapture(), prompt_hint="보험약관")
        assert received_hints == ["보험약관"]
