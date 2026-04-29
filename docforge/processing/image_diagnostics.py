"""Image quality diagnostics. Measures only, no decisions.

Takes numpy grayscale arrays as input to keep the processing layer pure
(no PIL dependency). PIL-to-ndarray conversion is adapter responsibility.
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray

from docforge.domain.value_objects import ImageQualityReport

logger = logging.getLogger(__name__)


def diagnose_image(gray: NDArray[np.uint8]) -> ImageQualityReport:
    """Measure 5 quality metrics independently.

    Args:
        gray: 2D grayscale numpy array (H x W, dtype uint8).

    Returns:
        ImageQualityReport with all measurements.
    """
    return ImageQualityReport(
        dpi_estimated=_estimate_dpi(gray),
        skew_angle=_detect_skew(gray),
        contrast_ratio=_measure_contrast(gray),
        noise_score=_measure_noise(gray),
        background_uniformity=_measure_bg_uniformity(gray),
    )


def to_grayscale(data: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Convert image data to grayscale if needed."""
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[2] == 3:
        # Simple luminance formula: 0.299R + 0.587G + 0.114B
        r = data[:, :, 0].astype(np.float32)
        g = data[:, :, 1].astype(np.float32)
        b = data[:, :, 2].astype(np.float32)
        gray = (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)
        return gray
    if data.ndim == 3 and data.shape[2] == 1:
        return data[:, :, 0]
    # Fallback: return as-is (may fail downstream)
    return data


def _estimate_dpi(gray: NDArray[np.uint8]) -> float:
    """Estimate DPI from text line height.

    Uses horizontal projection profile to find text line heights,
    then estimates DPI assuming standard 12pt text (~1/6 inch).

    Returns:
        Estimated DPI, or -1.0 if no text detected.
    """
    h, w = gray.shape[:2]
    if h < 50 or w < 50:
        return -1.0

    # Binarize with Otsu-like threshold
    threshold = np.mean(gray)
    binary = (gray < threshold).astype(np.uint8)

    # Horizontal projection profile
    h_proj = np.sum(binary, axis=1)

    # Find text lines (runs of high projection)
    is_text = h_proj > (w * 0.01)  # At least 1% of width has ink
    line_heights: list[int] = []
    in_line = False
    start = 0

    for i in range(len(is_text)):
        if is_text[i] and not in_line:
            in_line = True
            start = i
        elif not is_text[i] and in_line:
            in_line = False
            line_height = i - start
            if 5 <= line_height <= 200:  # Reasonable text line height range
                line_heights.append(line_height)

    if len(line_heights) < 3:
        return -1.0

    # Median text line height
    median_height = float(np.median(line_heights))

    # Standard 12pt text is 1/6 inch
    # DPI = text_height_px / (12pt / 72pt_per_inch) = text_height_px * 6
    estimated_dpi = median_height * 6.0

    return estimated_dpi


def _detect_skew(gray: NDArray[np.uint8]) -> float:
    """Detect image skew angle using Hough Line Transform.

    Returns:
        Skew angle in degrees. 0.0 if no lines detected.
    """
    import cv2

    h, w = gray.shape[:2]
    if h < 50 or w < 50:
        return 0.0

    # Edge detection
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # Hough Line Transform (probabilistic)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=w // 4,
        maxLineGap=20,
    )

    if lines is None or len(lines) == 0:
        return 0.0

    # Calculate angles of detected lines
    angles: list[float] = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(x2 - x1) < 1:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # Only consider near-horizontal lines (within 15 degrees)
        if abs(angle) < 15:
            angles.append(angle)

    if not angles:
        return 0.0

    return float(np.median(angles))


def _measure_contrast(gray: NDArray[np.uint8]) -> float:
    """Measure image contrast using histogram percentile difference.

    Returns:
        Contrast ratio (0.0 = no contrast, 1.0 = full contrast).
    """
    if gray.size == 0:
        return 0.0

    p5 = float(np.percentile(gray, 5))
    p95 = float(np.percentile(gray, 95))

    return (p95 - p5) / 255.0


def _measure_noise(gray: NDArray[np.uint8]) -> float:
    """Measure noise level using Laplacian variance.

    High Laplacian variance indicates sharp edges (good or noisy).
    We normalize and invert to get a noise score where:
    - 0.0 = clean (moderate Laplacian, well-defined edges)
    - 1.0 = very noisy (extreme Laplacian variance)

    Returns:
        Noise score (0.0 = clean, 1.0 = very noisy).
    """
    import cv2

    if gray.size == 0:
        return 0.0

    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    lap_var = float(np.var(laplacian))

    # Normalize: typical document images have Laplacian variance 100-2000
    # Very noisy images can have 5000+
    # Very blurry images have <50
    # We want: blurry -> low noise score, noisy -> high noise score
    # But also: very high Laplacian can mean salt-and-pepper noise

    # Salt-and-pepper detection: count extreme pixel values
    total = gray.size
    sp_ratio = float(np.sum((gray < 5) | (gray > 250)) / total)

    # Combine: high Laplacian variance + high s&p = noisy
    noise_from_lap = min(1.0, max(0.0, (lap_var - 2000) / 8000))
    noise_from_sp = min(1.0, sp_ratio * 5)  # 20%+ extreme pixels = score 1.0

    return min(1.0, (noise_from_lap + noise_from_sp) / 2)


def _measure_bg_uniformity(gray: NDArray[np.uint8]) -> float:
    """Measure background uniformity by analyzing block brightness variation.

    Divides image into NxN grid and measures std of block mean brightness.

    Returns:
        Non-uniformity score (0.0 = uniform, 1.0 = very non-uniform).
    """
    h, w = gray.shape[:2]
    grid_size = 4  # 4x4 grid

    if h < grid_size * 10 or w < grid_size * 10:
        return 0.0

    block_h = h // grid_size
    block_w = w // grid_size

    means: list[float] = []
    for r in range(grid_size):
        for c in range(grid_size):
            block = gray[r * block_h:(r + 1) * block_h, c * block_w:(c + 1) * block_w]
            means.append(float(np.mean(block)))

    if not means:
        return 0.0

    std = float(np.std(means))
    # Normalize: std of 0 = perfectly uniform, std of 50+ = very non-uniform
    return min(1.0, std / 50.0)
