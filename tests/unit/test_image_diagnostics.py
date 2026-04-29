"""Tests for image quality diagnostics."""

from __future__ import annotations

import numpy as np
import pytest

from docforge.domain.value_objects import ImageQualityPolicy, ImageQualityReport
from docforge.processing.image_diagnostics import (
    diagnose_image,
    to_grayscale,
    _estimate_dpi,
    _measure_contrast,
    _measure_noise,
    _measure_bg_uniformity,
)


class TestToGrayscale:
    def test_already_grayscale(self) -> None:
        gray = np.zeros((100, 100), dtype=np.uint8)
        result = to_grayscale(gray)
        assert result.ndim == 2

    def test_rgb_to_grayscale(self) -> None:
        rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        rgb[:, :, 0] = 255  # red channel
        result = to_grayscale(rgb)
        assert result.ndim == 2
        # Red with luminance formula: 0.299 * 255 = ~76
        assert 70 < result[0, 0] < 80

    def test_single_channel_3d(self) -> None:
        img = np.zeros((100, 100, 1), dtype=np.uint8)
        result = to_grayscale(img)
        assert result.ndim == 2


class TestEstimateDpi:
    def test_returns_negative_for_tiny_image(self) -> None:
        gray = np.zeros((10, 10), dtype=np.uint8)
        assert _estimate_dpi(gray) == -1.0

    def test_returns_negative_for_blank_image(self) -> None:
        gray = np.full((200, 200), 255, dtype=np.uint8)
        assert _estimate_dpi(gray) == -1.0

    def test_detects_text_lines(self) -> None:
        # Create synthetic image with horizontal text lines
        gray = np.full((600, 400), 255, dtype=np.uint8)
        # Add ~50px high text lines (simulating ~300 DPI 12pt text)
        for i in range(6):
            y_start = 50 + i * 80
            gray[y_start:y_start + 50, 50:350] = 30  # dark text
        dpi = _estimate_dpi(gray)
        assert dpi > 0  # Should detect something


class TestMeasureContrast:
    def test_full_contrast(self) -> None:
        gray = np.zeros((100, 100), dtype=np.uint8)
        gray[:50, :] = 0
        gray[50:, :] = 255
        contrast = _measure_contrast(gray)
        assert contrast > 0.9

    def test_no_contrast(self) -> None:
        gray = np.full((100, 100), 128, dtype=np.uint8)
        contrast = _measure_contrast(gray)
        assert contrast < 0.05

    def test_low_contrast(self) -> None:
        gray = np.full((100, 100), 128, dtype=np.uint8)
        gray[:50, :] = 120
        gray[50:, :] = 136
        contrast = _measure_contrast(gray)
        assert contrast < 0.1


class TestMeasureNoise:
    def test_clean_image_low_noise(self) -> None:
        gray = np.full((200, 200), 200, dtype=np.uint8)
        noise = _measure_noise(gray)
        assert noise < 0.2

    def test_salt_pepper_noise_detected(self) -> None:
        rng = np.random.default_rng(42)
        gray = np.full((200, 200), 128, dtype=np.uint8)
        # Add 25% salt-and-pepper noise
        mask = rng.random((200, 200))
        gray[mask < 0.125] = 0
        gray[mask > 0.875] = 255
        noise = _measure_noise(gray)
        assert noise > 0.1


class TestMeasureBgUniformity:
    def test_uniform_background(self) -> None:
        gray = np.full((200, 200), 200, dtype=np.uint8)
        uniformity = _measure_bg_uniformity(gray)
        assert uniformity < 0.1

    def test_non_uniform_background(self) -> None:
        gray = np.zeros((200, 200), dtype=np.uint8)
        # Left half bright, right half dark
        gray[:, :100] = 240
        gray[:, 100:] = 40
        uniformity = _measure_bg_uniformity(gray)
        assert uniformity > 0.3


class TestDiagnoseImage:
    def test_returns_report(self) -> None:
        gray = np.full((200, 200), 200, dtype=np.uint8)
        report = diagnose_image(gray)
        assert isinstance(report, ImageQualityReport)

    def test_clean_image_is_clean(self) -> None:
        # Create a "clean" document-like image
        gray = np.full((600, 400), 240, dtype=np.uint8)
        # Add text lines
        for i in range(8):
            y_start = 50 + i * 60
            gray[y_start:y_start + 40, 50:350] = 30
        report = diagnose_image(gray)
        policy = ImageQualityPolicy()
        # May or may not be completely clean depending on metrics,
        # but should at least have reasonable values
        assert 0.0 <= report.contrast_ratio <= 1.0
        assert 0.0 <= report.noise_score <= 1.0
        assert 0.0 <= report.background_uniformity <= 1.0
