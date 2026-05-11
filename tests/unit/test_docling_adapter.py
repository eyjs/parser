"""Tests for ``docling_adapter`` -- graceful fallback, label mapping, availability."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

from docforge.adapters.layout.docling_adapter import (
    DoclingLayoutDetector,
    _DOCLING_LABEL_MAP,
)
from docforge.domain.models import LayoutBlock
from docforge.domain.value_objects import BBox


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def detector() -> DoclingLayoutDetector:
    """Fresh detector instance (no lazy-load yet)."""
    return DoclingLayoutDetector()


# ---------------------------------------------------------------------------
# _DOCLING_LABEL_MAP coverage
# ---------------------------------------------------------------------------


class TestDoclingLabelMap:
    """Verify the canonical label vocabulary mapping."""

    def test_text_passthrough(self) -> None:
        assert _DOCLING_LABEL_MAP["Text"] == "Text"

    def test_title_variants(self) -> None:
        for variant in ("Title", "Section-header", "Section-Header", "SectionHeader"):
            assert _DOCLING_LABEL_MAP[variant] == "Title"

    def test_table_and_figure(self) -> None:
        assert _DOCLING_LABEL_MAP["Table"] == "Table"
        assert _DOCLING_LABEL_MAP["Figure"] == "Figure"
        assert _DOCLING_LABEL_MAP["Picture"] == "Figure"

    def test_caption_and_formula(self) -> None:
        assert _DOCLING_LABEL_MAP["Caption"] == "Caption"
        assert _DOCLING_LABEL_MAP["Formula"] == "Formula"
        assert _DOCLING_LABEL_MAP["Equation"] == "Formula"

    def test_list_variants(self) -> None:
        assert _DOCLING_LABEL_MAP["List"] == "List"
        assert _DOCLING_LABEL_MAP["List-item"] == "List"

    def test_noise_labels(self) -> None:
        assert _DOCLING_LABEL_MAP["Footer"] == "Footer"
        assert _DOCLING_LABEL_MAP["Page-footer"] == "Footer"
        assert _DOCLING_LABEL_MAP["Page-header"] == "Page-Header"
        assert _DOCLING_LABEL_MAP["Header"] == "Page-Header"
        assert _DOCLING_LABEL_MAP["Page-number"] == "Page-Number"
        assert _DOCLING_LABEL_MAP["Footnote"] == "Footnote"

    def test_unknown_label_defaults_to_text(self) -> None:
        """Labels not in the map should fall through to 'Text' default."""
        assert _DOCLING_LABEL_MAP.get("SomeNewLabel", "Text") == "Text"


# ---------------------------------------------------------------------------
# is_available() behavior
# ---------------------------------------------------------------------------


class TestIsAvailable:
    """Test lazy-load and graceful fallback of is_available()."""

    def test_returns_false_when_docling_not_installed(self, detector: DoclingLayoutDetector) -> None:
        # Arrange: make _import_docling raise ImportError
        with patch.object(detector, "_import_docling", side_effect=ImportError("no docling")):
            # Act
            result = detector.is_available()

        # Assert
        assert result is False
        assert detector._import_error is not None

    def test_returns_true_when_docling_installed(self, detector: DoclingLayoutDetector) -> None:
        # Arrange: make _import_docling succeed
        with patch.object(detector, "_import_docling", return_value=MagicMock()):
            # Act
            result = detector.is_available()

        # Assert
        assert result is True

    def test_caches_import_error(self, detector: DoclingLayoutDetector) -> None:
        """Once an import error is cached, subsequent calls do not retry."""
        # Arrange
        with patch.object(detector, "_import_docling", side_effect=ImportError("nope")):
            detector.is_available()

        # Act: second call should not attempt import again
        result = detector.is_available()

        # Assert
        assert result is False

    def test_returns_true_if_pipeline_already_loaded(self, detector: DoclingLayoutDetector) -> None:
        # Arrange: simulate already-loaded pipeline
        detector._pipeline = MagicMock()

        # Act & Assert
        assert detector.is_available() is True


# ---------------------------------------------------------------------------
# detect() graceful degradation
# ---------------------------------------------------------------------------


class TestDetectGracefulFallback:
    """Test that detect() returns [] on any failure."""

    def test_returns_empty_when_not_available(self, detector: DoclingLayoutDetector) -> None:
        # Arrange: mark as unavailable
        detector._import_error = ImportError("no docling")

        # Act
        result = detector.detect(MagicMock(), page_num=1)

        # Assert
        assert result == []

    def test_returns_empty_on_invalid_image(self, detector: DoclingLayoutDetector) -> None:
        # Arrange: pipeline loaded but image is not a PIL Image
        detector._pipeline = MagicMock()
        with patch.object(detector, "_coerce_to_pil", return_value=None):
            # Act
            result = detector.detect("not-an-image", page_num=1)

        # Assert
        assert result == []

    def test_returns_empty_on_detection_exception(self, detector: DoclingLayoutDetector) -> None:
        # Arrange: pipeline loaded, image valid, but detection throws
        detector._pipeline = MagicMock()
        mock_pil = MagicMock()
        with patch.object(detector, "_coerce_to_pil", return_value=mock_pil), \
             patch.object(detector, "_run_detection", side_effect=RuntimeError("boom")):
            # Act
            result = detector.detect(mock_pil, page_num=1)

        # Assert
        assert result == []


# ---------------------------------------------------------------------------
# _parse_bbox variations
# ---------------------------------------------------------------------------


class TestParseBbox:
    """Test various bbox format parsing."""

    def test_list_format(self) -> None:
        result = DoclingLayoutDetector._parse_bbox([10.0, 20.0, 300.0, 400.0])
        assert result == (10.0, 20.0, 300.0, 400.0)

    def test_tuple_format(self) -> None:
        result = DoclingLayoutDetector._parse_bbox((5, 10, 100, 200))
        assert result == (5.0, 10.0, 100.0, 200.0)

    def test_object_ltrb_attrs(self) -> None:
        # Arrange: mock object with l, t, r, b attributes
        obj = types.SimpleNamespace(l=1.0, t=2.0, r=3.0, b=4.0)

        # Act
        result = DoclingLayoutDetector._parse_bbox(obj)

        # Assert
        assert result == (1.0, 2.0, 3.0, 4.0)

    def test_object_x0y0x1y1_attrs(self) -> None:
        obj = types.SimpleNamespace(x0=10.0, y0=20.0, x1=30.0, y1=40.0)
        result = DoclingLayoutDetector._parse_bbox(obj)
        assert result == (10.0, 20.0, 30.0, 40.0)

    def test_object_left_top_right_bottom_attrs(self) -> None:
        obj = types.SimpleNamespace(left=5.0, top=10.0, right=15.0, bottom=20.0)
        result = DoclingLayoutDetector._parse_bbox(obj)
        assert result == (5.0, 10.0, 15.0, 20.0)

    def test_none_returns_none(self) -> None:
        assert DoclingLayoutDetector._parse_bbox(None) is None

    def test_unrecognized_object_returns_none(self) -> None:
        obj = types.SimpleNamespace(foo=1, bar=2)
        assert DoclingLayoutDetector._parse_bbox(obj) is None

    def test_short_list_returns_none(self) -> None:
        assert DoclingLayoutDetector._parse_bbox([1, 2, 3]) is None


# ---------------------------------------------------------------------------
# _item_to_layout_block / _cell_to_layout_block
# ---------------------------------------------------------------------------


class TestItemToLayoutBlock:
    """Test conversion of Docling document items to LayoutBlock."""

    def test_item_with_prov(self) -> None:
        # Arrange
        detector = DoclingLayoutDetector()
        prov = types.SimpleNamespace(
            bbox=types.SimpleNamespace(l=10.0, t=20.0, r=300.0, b=400.0),
            confidence=0.95,
        )
        item = types.SimpleNamespace(
            label=types.SimpleNamespace(value="Table"),
            prov=[prov],
        )

        # Act
        result = detector._item_to_layout_block(item, page_num=2)

        # Assert
        assert result is not None
        assert isinstance(result, LayoutBlock)
        assert result.label == "Table"
        assert result.confidence == 0.95
        assert result.page_num == 2
        assert result.bbox == BBox(10.0, 20.0, 300.0, 400.0)

    def test_item_with_unknown_label_defaults_to_text(self) -> None:
        detector = DoclingLayoutDetector()
        prov = types.SimpleNamespace(
            bbox=[0, 0, 100, 100],
            confidence=0.5,
        )
        item = types.SimpleNamespace(
            label=types.SimpleNamespace(value="UnknownLabel"),
            prov=[prov],
        )

        result = detector._item_to_layout_block(item, page_num=1)
        assert result is not None
        assert result.label == "Text"

    def test_item_without_bbox_returns_none(self) -> None:
        detector = DoclingLayoutDetector()
        item = types.SimpleNamespace(label="Text")
        result = detector._item_to_layout_block(item, page_num=1)
        assert result is None

    def test_item_without_label_returns_none(self) -> None:
        detector = DoclingLayoutDetector()
        item = types.SimpleNamespace(bbox=[0, 0, 100, 100])
        result = detector._item_to_layout_block(item, page_num=1)
        assert result is None


class TestCellToLayoutBlock:
    """Test conversion of Docling cell/prediction to LayoutBlock."""

    def test_cell_as_object(self) -> None:
        detector = DoclingLayoutDetector()
        cell = types.SimpleNamespace(
            label=types.SimpleNamespace(value="Figure"),
            bbox=[10, 20, 300, 400],
            confidence=0.88,
        )

        result = detector._cell_to_layout_block(cell, page_num=3)
        assert result is not None
        assert result.label == "Figure"
        assert result.confidence == 0.88

    def test_cell_as_dict(self) -> None:
        detector = DoclingLayoutDetector()
        cell = {
            "label": "List",
            "bbox": [0, 0, 50, 50],
            "confidence": 0.7,
        }

        result = detector._cell_to_layout_block(cell, page_num=1)
        assert result is not None
        assert result.label == "List"

    def test_cell_missing_label_returns_none(self) -> None:
        detector = DoclingLayoutDetector()
        cell = types.SimpleNamespace(bbox=[0, 0, 10, 10])
        result = detector._cell_to_layout_block(cell, page_num=1)
        assert result is None

    def test_cell_missing_bbox_returns_none(self) -> None:
        detector = DoclingLayoutDetector()
        cell = types.SimpleNamespace(label="Text")
        result = detector._cell_to_layout_block(cell, page_num=1)
        assert result is None


# ---------------------------------------------------------------------------
# _ensure_loaded
# ---------------------------------------------------------------------------


class TestEnsureLoaded:
    """Test lazy loading behavior."""

    def test_returns_true_when_pipeline_already_set(self) -> None:
        detector = DoclingLayoutDetector()
        detector._pipeline = MagicMock()
        assert detector._ensure_loaded() is True

    def test_returns_false_when_import_error_cached(self) -> None:
        detector = DoclingLayoutDetector()
        detector._import_error = ImportError("test")
        assert detector._ensure_loaded() is False

    def test_returns_false_on_import_failure(self) -> None:
        detector = DoclingLayoutDetector()
        with patch.object(detector, "_import_docling", side_effect=ImportError("no pkg")):
            assert detector._ensure_loaded() is False
            assert detector._import_error is not None
