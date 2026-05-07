"""Tests for ``ocr_multipass`` -- confidence tiers and VLM fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from docforge.domain.enums import BlockType
from docforge.domain.models import TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.ocr_multipass import evaluate_ocr_blocks, fallback_ocr_via_vlm


def _block(
    text: str,
    conf: float,
    x0: float = 0, y0: float = 0, x1: float = 100, y1: float = 20,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="AppleVision", size=0.0, is_bold=False),
        block_type=BlockType.TEXT,
        confidence=conf,
    )


class TestEvaluateOCRBlocks:
    def test_empty_input(self) -> None:
        acceptable, low = evaluate_ocr_blocks([])
        assert acceptable == []
        assert low == []

    def test_high_confidence_acceptable(self) -> None:
        blocks = [_block("good", 0.95)]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 1
        assert len(low) == 0

    def test_medium_confidence_acceptable(self) -> None:
        blocks = [_block("medium", 0.65)]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 1
        assert len(low) == 0

    def test_low_confidence_goes_to_low(self) -> None:
        blocks = [_block("bad", 0.3)]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 0
        assert len(low) == 1

    def test_boundary_05_is_medium(self) -> None:
        """Confidence exactly 0.5 is in [0.5, 0.8) -> medium -> acceptable."""
        blocks = [_block("boundary", 0.5)]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 1
        assert len(low) == 0

    def test_boundary_08_is_high(self) -> None:
        """Confidence exactly 0.8 is high."""
        blocks = [_block("high", 0.8)]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 1
        assert len(low) == 0

    def test_just_below_05_is_low(self) -> None:
        blocks = [_block("below", 0.49)]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 0
        assert len(low) == 1

    def test_mixed_distribution(self) -> None:
        blocks = [
            _block("high", 0.9),
            _block("medium", 0.6),
            _block("low", 0.3),
            _block("very_low", 0.1),
        ]
        acceptable, low = evaluate_ocr_blocks(blocks)
        assert len(acceptable) == 2  # high + medium
        assert len(low) == 2  # low + very_low


class TestFallbackOCRViaVLM:
    def test_empty_blocks_returns_empty(self) -> None:
        engine = MagicMock()
        result = fallback_ocr_via_vlm([], engine, b"", 100, 100)
        assert result == []

    def test_empty_image_returns_originals(self) -> None:
        blocks = [_block("text", 0.3)]
        engine = MagicMock()
        result = fallback_ocr_via_vlm(blocks, engine, b"", 100, 100)
        assert len(result) == 1
        assert result[0].text == "text"

    def test_vlm_replaces_text(self) -> None:
        """When VLM returns non-empty text, it replaces the original."""
        import io
        from PIL import Image

        # Create a valid page image
        img = Image.new("RGB", (200, 200), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page_bytes = buf.getvalue()

        blocks = [_block("garbled", 0.3, x0=10, y0=10, x1=100, y1=50)]

        engine = MagicMock()
        engine.describe_image.return_value = "correct text"

        result = fallback_ocr_via_vlm(blocks, engine, page_bytes, 200, 200)
        assert len(result) == 1
        assert result[0].text == "correct text"
        assert result[0].confidence == 0.85  # VLM-corrected confidence

    def test_vlm_empty_result_keeps_original(self) -> None:
        """When VLM returns empty text, original block is kept."""
        import io
        from PIL import Image

        img = Image.new("RGB", (200, 200), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page_bytes = buf.getvalue()

        blocks = [_block("garbled", 0.3, x0=10, y0=10, x1=100, y1=50)]

        engine = MagicMock()
        engine.describe_image.return_value = ""

        result = fallback_ocr_via_vlm(blocks, engine, page_bytes, 200, 200)
        assert result[0].text == "garbled"
        assert result[0].confidence == 0.3

    def test_vlm_exception_keeps_original(self) -> None:
        """When VLM raises, original block is kept."""
        import io
        from PIL import Image

        img = Image.new("RGB", (200, 200), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page_bytes = buf.getvalue()

        blocks = [_block("garbled", 0.3, x0=10, y0=10, x1=100, y1=50)]

        engine = MagicMock()
        engine.describe_image.side_effect = RuntimeError("VLM error")

        result = fallback_ocr_via_vlm(blocks, engine, page_bytes, 200, 200)
        assert result[0].text == "garbled"
