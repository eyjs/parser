"""Image format conversion utilities.

Bridges PIL Image and domain RawImage to keep processing layer pure.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from docforge.domain.value_objects import RawImage


def pil_to_raw_image(pil_image: Any) -> RawImage:
    """Convert a PIL Image to RawImage.

    Args:
        pil_image: PIL.Image.Image instance.

    Returns:
        RawImage with numpy array data.
    """
    from PIL import Image

    if not isinstance(pil_image, Image.Image):
        raise TypeError(f"Expected PIL Image, got {type(pil_image)}")

    arr = np.array(pil_image, dtype=np.uint8)
    w, h = pil_image.size

    if arr.ndim == 2:
        channels = 1
    elif arr.ndim == 3:
        channels = arr.shape[2]
    else:
        channels = 1

    return RawImage(data=arr, width=w, height=h, channels=channels)


def raw_image_to_pil(raw_image: RawImage) -> Any:
    """Convert a RawImage back to PIL Image.

    Args:
        raw_image: RawImage instance.

    Returns:
        PIL.Image.Image instance.
    """
    from PIL import Image

    if raw_image.channels == 1:
        if raw_image.data.ndim == 2:
            return Image.fromarray(raw_image.data, mode="L")
        return Image.fromarray(raw_image.data[:, :, 0], mode="L")
    return Image.fromarray(raw_image.data, mode="RGB")
