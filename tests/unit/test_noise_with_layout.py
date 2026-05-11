"""Tests for ``filter_noise_with_layout()`` -- ML-based noise filtering with heuristic fallback."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, NoiseStats, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.infrastructure.config import ParserConfig
from docforge.processing.noise_detector import (
    LearnedPatterns,
    filter_noise_from_blocks,
    filter_noise_with_layout,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_PATTERNS = LearnedPatterns(
    header_patterns=frozenset(),
    footer_patterns=frozenset(),
    watermark_patterns=frozenset(),
)

_DEFAULT_CONFIG = ParserConfig()

_PAGE_HEIGHT = 800.0


def _tb(
    text: str,
    x0: float, y0: float, x1: float, y1: float,
) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        font=FontInfo(name="Arial", size=10.0, is_bold=False),
        block_type=BlockType.TEXT,
    )


def _lb(
    label: str,
    x0: float, y0: float, x1: float, y1: float,
    confidence: float = 0.9,
) -> LayoutBlock:
    return LayoutBlock(
        bbox=BBox(x0, y0, x1, y1),
        label=label,
        confidence=confidence,
        page_num=1,
    )


# ---------------------------------------------------------------------------
# ML-based noise detection
# ---------------------------------------------------------------------------


class TestMLBasedNoiseFiltering:
    """Test that layout labels correctly classify blocks as noise."""

    def test_footer_label_filters_block(self) -> None:
        """Block overlapping a 'Footer' layout block is classified as footer noise."""
        # Arrange
        text_blocks = [
            _tb("content", 0, 100, 500, 150),
            _tb("DB Insurance", 0, 750, 500, 780),
        ]
        layout_blocks = [
            _lb("Footer", 0, 750, 500, 780),
        ]

        # Act
        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        # Assert
        assert len(clean) == 1
        assert clean[0].text == "content"
        assert stats.footers == 1

    def test_page_footer_label_filters_block(self) -> None:
        """Block overlapping a 'Page-Footer' layout block is classified as footer noise."""
        text_blocks = [
            _tb("content", 0, 100, 500, 150),
            _tb("footer text", 0, 760, 500, 790),
        ]
        layout_blocks = [
            _lb("Page-Footer", 0, 760, 500, 790),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert stats.footers == 1

    def test_page_number_label_filters_block(self) -> None:
        """Block overlapping a 'Page-Number' layout block is classified as page_number."""
        text_blocks = [
            _tb("body text", 0, 200, 500, 250),
            _tb("42", 250, 770, 300, 790),
        ]
        layout_blocks = [
            _lb("Page-Number", 250, 770, 300, 790),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert clean[0].text == "body text"
        assert stats.page_numbers == 1

    def test_page_header_label_filters_block(self) -> None:
        """Block overlapping a 'Page-Header' layout block is classified as header noise."""
        text_blocks = [
            _tb("Insurance Policy", 0, 10, 500, 40),
            _tb("body", 0, 200, 500, 250),
        ]
        layout_blocks = [
            _lb("Page-Header", 0, 10, 500, 40),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert clean[0].text == "body"
        assert stats.headers == 1

    def test_header_label_filters_block(self) -> None:
        """Block overlapping a 'Header' layout block is classified as header noise."""
        text_blocks = [
            _tb("Header Text", 0, 5, 500, 30),
            _tb("content", 0, 100, 500, 150),
        ]
        layout_blocks = [
            _lb("Header", 0, 5, 500, 30),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert clean[0].text == "content"
        assert stats.headers == 1

    def test_multiple_noise_labels(self) -> None:
        """Multiple noise layout blocks filter corresponding text blocks."""
        text_blocks = [
            _tb("header", 0, 10, 500, 40),
            _tb("body 1", 0, 200, 500, 250),
            _tb("body 2", 0, 300, 500, 350),
            _tb("page num", 250, 770, 300, 790),
            _tb("footer", 0, 750, 500, 780),
        ]
        layout_blocks = [
            _lb("Page-Header", 0, 10, 500, 40),
            _lb("Page-Number", 250, 770, 300, 790),
            _lb("Footer", 0, 750, 500, 780),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 2
        assert {b.text for b in clean} == {"body 1", "body 2"}
        assert stats.headers == 1
        assert stats.page_numbers == 1
        assert stats.footers == 1


# ---------------------------------------------------------------------------
# Heuristic fallback when no layout blocks
# ---------------------------------------------------------------------------


class TestHeuristicFallback:
    """When layout_blocks is empty, should fall back to heuristic-based filtering."""

    def test_empty_layout_falls_back_to_heuristics(self) -> None:
        """With empty layout_blocks, behavior should match filter_noise_from_blocks."""
        text_blocks = [
            _tb("content", 0, 200, 500, 250),
            _tb("42", 0, 750, 50, 760),  # page number by heuristic
        ]

        # Act: filter_noise_with_layout with empty layout
        clean_with_layout, stats_with_layout = filter_noise_with_layout(
            text_blocks, [], _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        # Act: filter_noise_from_blocks (pure heuristic)
        clean_heuristic, stats_heuristic = filter_noise_from_blocks(
            text_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        # Assert: same results
        assert len(clean_with_layout) == len(clean_heuristic)
        assert stats_with_layout == stats_heuristic

    def test_no_noise_labels_falls_back_to_heuristics(self) -> None:
        """Layout blocks without noise labels should trigger heuristic fallback."""
        text_blocks = [
            _tb("content", 0, 200, 500, 250),
            _tb("42", 0, 750, 50, 760),
        ]
        # Layout blocks with non-noise labels only
        layout_blocks = [
            _lb("Text", 0, 200, 500, 250),
            _lb("Text", 0, 750, 50, 760),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        # "42" should still be caught by heuristic page-number detection
        assert len(clean) == 1
        assert clean[0].text == "content"
        assert stats.page_numbers == 1


# ---------------------------------------------------------------------------
# IoU threshold behavior
# ---------------------------------------------------------------------------


class TestIoUThreshold:
    """Test IoU threshold controls ML noise matching."""

    def test_low_iou_does_not_match(self) -> None:
        """Layout block far from text block should not trigger noise classification."""
        text_blocks = [
            _tb("not noise", 0, 400, 100, 450),
        ]
        layout_blocks = [
            _lb("Footer", 400, 700, 500, 780),  # far away from text block
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert stats.footers == 0

    def test_custom_iou_threshold(self) -> None:
        """Custom iou_threshold should affect matching sensitivity."""
        text_blocks = [
            _tb("overlapping", 0, 0, 100, 100),
        ]
        # Partial overlap with Footer label
        layout_blocks = [
            _lb("Footer", 50, 50, 200, 200),
        ]

        # With very high threshold, should NOT match
        clean_strict, stats_strict = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
            iou_threshold=0.9,
        )
        assert len(clean_strict) == 1
        assert stats_strict.footers == 0

        # With very low threshold, should match (if IoU > 0)
        clean_loose, stats_loose = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
            iou_threshold=0.01,
        )
        assert len(clean_loose) == 0
        assert stats_loose.footers == 1


# ---------------------------------------------------------------------------
# Partial matching (some blocks matched by ML, others by heuristic)
# ---------------------------------------------------------------------------


class TestPartialMatching:
    """Test hybrid ML + heuristic noise classification."""

    def test_ml_and_heuristic_combined(self) -> None:
        """Some blocks filtered by ML labels, others by heuristic patterns."""
        patterns = LearnedPatterns(
            header_patterns=frozenset({"INSURANCE POLICY"}),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
        )

        text_blocks = [
            # ML-detectable: overlaps Page-Number layout block
            _tb("42", 250, 770, 300, 790),
            # Heuristic-detectable: learned header pattern at top of page
            _tb("INSURANCE POLICY", 0, 10, 500, 30),
            # Content: should survive both filters
            _tb("This is body text.", 0, 200, 500, 250),
        ]
        layout_blocks = [
            _lb("Page-Number", 250, 770, 300, 790),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, patterns, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert clean[0].text == "This is body text."
        assert stats.page_numbers == 1
        assert stats.headers == 1

    def test_ml_overrides_heuristic_for_same_block(self) -> None:
        """When ML labels a block as noise, it should be classified even if
        heuristic would not catch it (e.g., footer text not in learned patterns)."""
        text_blocks = [
            _tb("Confidential", 0, 760, 500, 790),  # would NOT be caught by empty patterns
        ]
        layout_blocks = [
            _lb("Footer", 0, 760, 500, 790),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 0
        assert stats.footers == 1


# ---------------------------------------------------------------------------
# NoiseStats correctness
# ---------------------------------------------------------------------------


class TestNoiseStatsAccuracy:
    """Verify that returned NoiseStats counts are accurate."""

    def test_all_noise_types_counted(self) -> None:
        """Each noise type should be counted correctly."""
        patterns = LearnedPatterns(
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset({"DRAFT"}),
        )

        text_blocks = [
            _tb("header text", 0, 10, 500, 30),
            _tb("DRAFT", 200, 350, 400, 450),  # center of page, large-ish
            _tb("page 42", 250, 770, 350, 790),
            _tb("footer info", 0, 760, 500, 785),
            _tb("real content", 0, 200, 500, 250),
        ]
        layout_blocks = [
            _lb("Page-Header", 0, 10, 500, 30),
            _lb("Page-Number", 250, 770, 350, 790),
            _lb("Footer", 0, 760, 500, 785),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, layout_blocks, _PAGE_HEIGHT, patterns, _DEFAULT_CONFIG,
        )

        assert len(clean) == 1
        assert clean[0].text == "real content"
        assert stats.headers == 1
        assert stats.page_numbers == 1
        assert stats.footers == 1
        assert stats.watermarks == 1

    def test_zero_noise_returns_zero_stats(self) -> None:
        """When no blocks are noise, all counts should be 0."""
        text_blocks = [
            _tb("content A", 0, 200, 500, 250),
            _tb("content B", 0, 300, 500, 350),
        ]

        clean, stats = filter_noise_with_layout(
            text_blocks, [], _PAGE_HEIGHT, _EMPTY_PATTERNS, _DEFAULT_CONFIG,
        )

        assert len(clean) == 2
        assert stats == NoiseStats()


# ---------------------------------------------------------------------------
# Import availability
# ---------------------------------------------------------------------------


class TestImportability:
    """Verify filter_noise_with_layout is importable from noise_detector."""

    def test_importable(self) -> None:
        from docforge.processing.noise_detector import filter_noise_with_layout as fn
        assert callable(fn)
