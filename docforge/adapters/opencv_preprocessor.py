"""OpenCV-based image preprocessing. Implements ImagePreprocessor port.

Each preprocessing technique is applied independently based on the decision.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from docforge.domain.value_objects import PreprocessingDecision, RawImage

logger = logging.getLogger(__name__)


class OpenCVPreprocessor:
    """Image preprocessor using OpenCV. Implements the ImagePreprocessor protocol."""

    def preprocess(self, image: RawImage, decision: PreprocessingDecision) -> RawImage:
        """Apply selective preprocessing based on the decision.

        Each technique is applied independently. Returns a new RawImage.
        """
        img = image.data.copy()

        if decision.apply_upscale:
            img = self._upscale(img, target_dpi=300)

        if decision.apply_deskew:
            img = self._deskew(img, decision.skew_angle)

        if decision.apply_contrast:
            img = self._enhance_contrast(img)

        if decision.apply_denoise:
            img = self._denoise(img)

        if decision.apply_binarize:
            img = self._adaptive_binarize(img)

        h, w = img.shape[:2]
        channels = 1 if img.ndim == 2 else img.shape[2]
        return RawImage(data=img, width=w, height=h, channels=channels)

    def _upscale(self, img: np.ndarray, target_dpi: int = 300) -> np.ndarray:
        """Upscale image using Lanczos interpolation."""
        h, w = img.shape[:2]
        # Assume current DPI is ~150, scale to target
        scale = target_dpi / 150.0
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    def _deskew(self, img: np.ndarray, angle: float) -> np.ndarray:
        """Rotate image to correct skew."""
        if abs(angle) < 0.01:
            return img

        h, w = img.shape[:2]
        center = (w / 2, h / 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        # Use white background for rotation
        border_value = 255 if img.ndim == 2 else (255, 255, 255)
        return cv2.warpAffine(
            img, matrix, (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_value,
        )

    def _enhance_contrast(self, img: np.ndarray) -> np.ndarray:
        """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)."""
        if img.ndim == 3:
            # Convert to LAB, apply CLAHE to L channel
            lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
            return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        else:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            return clahe.apply(img)

    def _denoise(self, img: np.ndarray) -> np.ndarray:
        """Apply median filter for noise reduction."""
        return cv2.medianBlur(img, 3)

    def _adaptive_binarize(self, img: np.ndarray) -> np.ndarray:
        """Apply adaptive binarization for non-uniform backgrounds."""
        if img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img

        return cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=31,
            C=10,
        )
