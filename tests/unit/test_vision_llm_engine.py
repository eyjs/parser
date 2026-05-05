"""Tests for the Qwen2-VL MLX vision LLM engine adapter."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from docforge.adapters.vision_llm_engine import (
    Qwen2VLMLXEngine,
    _check_model_cache,
)


class TestIsAvailable:
    """Test is_available() with model cache verification."""

    def test_no_mlx_returns_false(self) -> None:
        """When mlx is not installed, is_available should return False."""
        engine = Qwen2VLMLXEngine()
        with patch.dict("sys.modules", {"mlx": None, "mlx.core": None}):
            # Force ImportError on mlx.core
            with patch("builtins.__import__", side_effect=ImportError("no mlx")):
                assert engine.is_available() is False

    def test_available_with_mlx_and_cache(self, tmp_path: Path) -> None:
        """When mlx is installed and model cache exists, should return True."""
        # Create a fake model cache directory
        model_dir = tmp_path / "hub" / "models--mlx-community--Qwen2-VL-7B-Instruct-4bit"
        snapshots = model_dir / "snapshots" / "abc123"
        snapshots.mkdir(parents=True)
        (snapshots / "config.json").write_text("{}")

        engine = Qwen2VLMLXEngine()
        with patch("docforge.adapters.vision_llm_engine._check_model_cache", return_value=True):
            with patch.dict("sys.modules", {
                "mlx": MagicMock(),
                "mlx.core": MagicMock(),
                "mlx_vlm": MagicMock(),
            }):
                # Patch the import inside is_available
                assert engine.is_available() is True


class TestCheckModelCache:
    """Test the _check_model_cache helper."""

    def test_empty_cache_returns_false(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            assert _check_model_cache("mlx-community/Qwen2-VL-7B-Instruct-4bit") is False

    def test_model_dir_without_snapshots_returns_false(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "hub" / "models--mlx-community--Qwen2-VL-7B-Instruct-4bit"
        model_dir.mkdir(parents=True)
        with patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            assert _check_model_cache("mlx-community/Qwen2-VL-7B-Instruct-4bit") is False

    def test_empty_snapshots_returns_false(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "hub" / "models--mlx-community--Qwen2-VL-7B-Instruct-4bit"
        snapshots = model_dir / "snapshots"
        snapshots.mkdir(parents=True)
        with patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            assert _check_model_cache("mlx-community/Qwen2-VL-7B-Instruct-4bit") is False

    def test_valid_cache_returns_true(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "hub" / "models--mlx-community--Qwen2-VL-7B-Instruct-4bit"
        snapshots = model_dir / "snapshots" / "abc123"
        snapshots.mkdir(parents=True)
        (snapshots / "config.json").write_text("{}")
        with patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            assert _check_model_cache("mlx-community/Qwen2-VL-7B-Instruct-4bit") is True


class TestDescribeImage:
    """Test describe_image method existence and basic contract."""

    def test_method_exists(self) -> None:
        engine = Qwen2VLMLXEngine()
        assert hasattr(engine, "describe_image")
        assert callable(engine.describe_image)

    def test_empty_data_returns_empty_string(self) -> None:
        engine = Qwen2VLMLXEngine()
        result = engine.describe_image(image_data=b"", format="png")
        assert result == ""
