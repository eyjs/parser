"""Tests for the cloud VLM engine adapter (OpenAI / Anthropic)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from docforge.adapters.cloud_vlm_engine import CloudVisionEngine, _raw_image_to_bytes
from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo, RawImage


def _make_raw_image() -> RawImage:
    return RawImage(
        data=np.zeros((10, 10, 3), dtype=np.uint8),
        width=10,
        height=10,
        channels=3,
    )


def _make_block(text: str = "test") -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0=0, y0=0, x1=100, y1=20),
        font=FontInfo(size=10.0, is_bold=False, name="test"),
        block_type=BlockType.TEXT,
        heading_level=0,
    )


class TestProviderResolution:
    """Test _resolve_provider logic."""

    def test_no_api_keys_returns_not_available(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            # Remove specific keys if present
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
            with patch.dict(os.environ, env, clear=True):
                engine = CloudVisionEngine(provider="auto")
                assert engine.is_available() is False

    def test_openai_key_resolves_openai(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            engine = CloudVisionEngine(provider="auto")
            assert engine.is_available() is True
            assert engine._resolve_provider() == "openai"

    def test_anthropic_key_resolves_anthropic(self) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env, clear=True):
            engine = CloudVisionEngine(provider="auto")
            assert engine.is_available() is True
            assert engine._resolve_provider() == "anthropic"

    def test_provider_openai_only(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            engine = CloudVisionEngine(provider="openai")
            # Only checks openai key, which is absent
            assert engine.is_available() is False

    def test_provider_anthropic_only(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            engine = CloudVisionEngine(provider="anthropic")
            # Only checks anthropic key, which is absent
            assert engine.is_available() is False

    def test_auto_prefers_openai_over_anthropic(self) -> None:
        env = {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env, clear=True):
            engine = CloudVisionEngine(provider="auto")
            assert engine._resolve_provider() == "openai"


class TestDescribeImage:
    """Test describe_image method."""

    def test_empty_data_returns_empty(self) -> None:
        engine = CloudVisionEngine(provider="auto")
        result = engine.describe_image(image_data=b"", format="png")
        assert result == ""

    def test_no_provider_returns_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
            with patch.dict(os.environ, env, clear=True):
                engine = CloudVisionEngine(provider="auto")
                result = engine.describe_image(
                    image_data=b"fake-png-data",
                    format="png",
                )
                assert result == ""

    def test_openai_describe_image(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "보험 약관 도표"

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            engine = CloudVisionEngine(provider="openai")
            with patch("docforge.adapters.cloud_vlm_engine.CloudVisionEngine._call_openai",
                       return_value="보험 약관 도표"):
                result = engine.describe_image(
                    image_data=b"fake-png-data",
                    format="png",
                    prompt_hint="보험약관",
                )
                assert result == "보험 약관 도표"

    def test_anthropic_describe_image(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            engine = CloudVisionEngine(provider="anthropic")
            with patch("docforge.adapters.cloud_vlm_engine.CloudVisionEngine._call_anthropic",
                       return_value="차트 설명"):
                result = engine.describe_image(
                    image_data=b"fake-png-data",
                    format="png",
                )
                assert result == "차트 설명"

    def test_api_failure_returns_empty(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            engine = CloudVisionEngine(provider="openai")
            with patch("docforge.adapters.cloud_vlm_engine.CloudVisionEngine._call_openai",
                       side_effect=RuntimeError("API error")):
                result = engine.describe_image(
                    image_data=b"fake-png-data",
                    format="png",
                )
                assert result == ""


class TestCorrectPage:
    """Test correct_page method."""

    def test_no_provider_returns_original_blocks(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
            with patch.dict(os.environ, env, clear=True):
                engine = CloudVisionEngine(provider="auto")
                blocks = [_make_block("original")]
                result = engine.correct_page(
                    image=_make_raw_image(),
                    ocr_blocks=blocks,
                )
                assert len(result) == 1
                assert result[0].text == "original"

    def test_correct_page_calls_vision_api(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            engine = CloudVisionEngine(provider="openai")
            with patch("docforge.adapters.cloud_vlm_engine.CloudVisionEngine._call_openai",
                       return_value="교정된 텍스트\n두번째 줄"):
                result = engine.correct_page(
                    image=_make_raw_image(),
                    ocr_blocks=[_make_block("원본")],
                )
                assert len(result) == 2
                assert result[0].text == "교정된 텍스트"
                assert result[1].text == "두번째 줄"

    def test_correct_page_api_failure(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            engine = CloudVisionEngine(provider="openai")
            with patch("docforge.adapters.cloud_vlm_engine.CloudVisionEngine._call_openai",
                       side_effect=RuntimeError("fail")):
                blocks = [_make_block("keep")]
                result = engine.correct_page(
                    image=_make_raw_image(),
                    ocr_blocks=blocks,
                )
                assert len(result) == 1
                assert result[0].text == "keep"


class TestHelpers:
    """Test module-level helpers."""

    def test_raw_image_to_bytes_rgb(self) -> None:
        img = _make_raw_image()
        result = _raw_image_to_bytes(img)
        assert isinstance(result, bytes)
        assert len(result) > 0
        # PNG starts with magic bytes
        assert result[:4] == b"\x89PNG"

    def test_raw_image_to_bytes_grayscale(self) -> None:
        img = RawImage(
            data=np.zeros((10, 10, 1), dtype=np.uint8),
            width=10,
            height=10,
            channels=1,
        )
        result = _raw_image_to_bytes(img)
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG"
