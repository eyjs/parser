"""Tests for build_llm_engine fallback chain in _parse_pdf_helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from docforge.infrastructure.config import ParserConfig
from docforge.usecases._parse_pdf_helpers import build_llm_engine


class TestBuildLlmEngine:
    """Test VLM engine construction with fallback chain."""

    def test_disabled_returns_none(self) -> None:
        config = ParserConfig(
            llm_fallback_enabled=False,
            region_vlm_enabled=False,
        )
        result = build_llm_engine(config)
        assert result is None

    def test_local_available_returns_local(self) -> None:
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True

        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
            return_value=mock_engine,
        ):
            config = ParserConfig(vlm_provider="auto")
            result = build_llm_engine(config)
            assert result is mock_engine

    def test_local_unavailable_falls_back_to_cloud(self) -> None:
        mock_cloud = MagicMock()
        mock_cloud.is_available.return_value = True

        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
            return_value=None,
        ), patch(
            "docforge.usecases._parse_pdf_helpers._try_cloud_vlm",
            return_value=mock_cloud,
        ):
            config = ParserConfig(vlm_provider="auto")
            result = build_llm_engine(config)
            assert result is mock_cloud

    def test_both_unavailable_returns_none(self) -> None:
        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
            return_value=None,
        ), patch(
            "docforge.usecases._parse_pdf_helpers._try_cloud_vlm",
            return_value=None,
        ):
            config = ParserConfig(vlm_provider="auto")
            result = build_llm_engine(config)
            assert result is None

    def test_provider_local_no_cloud_fallback(self) -> None:
        """When provider=local and local is unavailable, should NOT try cloud."""
        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
            return_value=None,
        ) as mock_local, patch(
            "docforge.usecases._parse_pdf_helpers._try_cloud_vlm",
        ) as mock_cloud:
            config = ParserConfig(vlm_provider="local")
            result = build_llm_engine(config)
            assert result is None
            mock_local.assert_called_once()
            mock_cloud.assert_not_called()

    def test_provider_openai_skips_local(self) -> None:
        """When provider=openai, should skip local and go straight to cloud."""
        mock_cloud = MagicMock()
        mock_cloud.is_available.return_value = True

        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
        ) as mock_local, patch(
            "docforge.usecases._parse_pdf_helpers._try_cloud_vlm",
            return_value=mock_cloud,
        ) as mock_cloud_fn:
            config = ParserConfig(vlm_provider="openai")
            result = build_llm_engine(config)
            assert result is mock_cloud
            mock_local.assert_not_called()
            mock_cloud_fn.assert_called_once_with("openai")

    def test_provider_anthropic_skips_local(self) -> None:
        """When provider=anthropic, should skip local and go straight to cloud."""
        mock_cloud = MagicMock()

        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
        ) as mock_local, patch(
            "docforge.usecases._parse_pdf_helpers._try_cloud_vlm",
            return_value=mock_cloud,
        ) as mock_cloud_fn:
            config = ParserConfig(vlm_provider="anthropic")
            result = build_llm_engine(config)
            assert result is mock_cloud
            mock_local.assert_not_called()
            mock_cloud_fn.assert_called_once_with("anthropic")

    def test_region_vlm_enabled_alone_triggers_build(self) -> None:
        """Even if llm_fallback_enabled=False, region_vlm_enabled=True should
        still attempt to build an engine."""
        mock_engine = MagicMock()

        with patch(
            "docforge.usecases._parse_pdf_helpers._try_local_vlm",
            return_value=mock_engine,
        ):
            config = ParserConfig(
                llm_fallback_enabled=False,
                region_vlm_enabled=True,
                vlm_provider="auto",
            )
            result = build_llm_engine(config)
            assert result is mock_engine
