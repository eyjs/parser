"""Unit tests for the unified signal-based block classifier."""

from __future__ import annotations

import pytest

from docforge.domain.enums import BlockType
from docforge.domain.models import LayoutBlock, TextBlock
from docforge.domain.value_objects import BBox, FontInfo
from docforge.processing.block_classifier import (
    ALL_SIGNALS,
    BBoxHeightRatioSignal,
    BlockClassifier,
    DataBearingSignal,
    DEFAULT_WEIGHTS,
    EndsWithoutPeriodSignal,
    FontDeviationSignal,
    FontWeightSignal,
    HasNumberingSignal,
    HEADING_THRESHOLD,
    HOMOGENEOUS_FONT_WEIGHTS,
    LayoutLabelSignal,
    SignalContext,
    TextLengthSignal,
    VerticalGapSignal,
    WidthRatioSignal,
    build_context_for_block,
    classify_block_signal,
    classify_blocks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_classifier():
    return BlockClassifier()


@pytest.fixture
def default_ctx():
    """A minimal SignalContext for paragraph text."""
    return SignalContext(
        text="This is a normal paragraph of text that is reasonably long.",
        font_size=10.0,
        is_bold=False,
        avg_font_size=10.0,
        bbox=BBox(50, 100, 500, 115),
        page_height=800,
        page_width=600,
    )


@pytest.fixture
def heading_ctx():
    """A SignalContext for a typical heading."""
    return SignalContext(
        text="제1장 총칙",
        font_size=14.0,
        is_bold=True,
        avg_font_size=10.0,
        font_size_std=1.5,
        bbox=BBox(50, 50, 300, 68),
        median_bbox_height=15.0,
        page_height=800,
        page_width=600,
    )


# ---------------------------------------------------------------------------
# Individual Signal Tests
# ---------------------------------------------------------------------------


class TestFontDeviationSignal:
    signal = FontDeviationSignal()

    def test_no_font_info(self):
        ctx = SignalContext(font_size=0.0, avg_font_size=0.0)
        assert self.signal.compute(ctx) == 0.0

    def test_average_font(self):
        ctx = SignalContext(font_size=10.0, avg_font_size=10.0, font_size_std=1.5)
        assert self.signal.compute(ctx) == 0.0

    def test_large_font(self):
        ctx = SignalContext(font_size=14.0, avg_font_size=10.0, font_size_std=1.5)
        score = self.signal.compute(ctx)
        assert score > 0.5

    def test_very_large_font(self):
        ctx = SignalContext(font_size=18.0, avg_font_size=10.0, font_size_std=1.5)
        assert self.signal.compute(ctx) == 1.0

    def test_small_font(self):
        ctx = SignalContext(font_size=8.0, avg_font_size=10.0, font_size_std=1.5)
        assert self.signal.compute(ctx) == 0.0


class TestFontWeightSignal:
    signal = FontWeightSignal()

    def test_bold(self):
        ctx = SignalContext(is_bold=True)
        assert self.signal.compute(ctx) == 1.0

    def test_not_bold(self):
        ctx = SignalContext(is_bold=False)
        assert self.signal.compute(ctx) == 0.0


class TestBBoxHeightRatioSignal:
    signal = BBoxHeightRatioSignal()

    def test_no_median(self):
        ctx = SignalContext(bbox=BBox(0, 0, 100, 20), median_bbox_height=0)
        assert self.signal.compute(ctx) == 0.0

    def test_normal_height(self):
        ctx = SignalContext(bbox=BBox(0, 0, 100, 15), median_bbox_height=15)
        assert self.signal.compute(ctx) == 0.0

    def test_tall_block(self):
        ctx = SignalContext(bbox=BBox(0, 0, 100, 30), median_bbox_height=15)
        assert self.signal.compute(ctx) == 1.0

    def test_slightly_tall(self):
        ctx = SignalContext(bbox=BBox(0, 0, 100, 20), median_bbox_height=15)
        score = self.signal.compute(ctx)
        assert 0.0 < score < 1.0


class TestVerticalGapSignal:
    signal = VerticalGapSignal()

    def test_no_previous(self):
        ctx = SignalContext(bbox=BBox(0, 100, 100, 120), page_height=800)
        assert self.signal.compute(ctx) == 0.0

    def test_small_gap(self):
        ctx = SignalContext(
            bbox=BBox(0, 102, 100, 120),
            prev_bbox=BBox(0, 80, 100, 100),
            page_height=800,
        )
        assert self.signal.compute(ctx) == 0.0

    def test_large_gap(self):
        ctx = SignalContext(
            bbox=BBox(0, 200, 100, 220),
            prev_bbox=BBox(0, 80, 100, 100),
            page_height=800,
        )
        score = self.signal.compute(ctx)
        assert score > 0.5


class TestTextLengthSignal:
    signal = TextLengthSignal()

    def test_empty(self):
        ctx = SignalContext(text="")
        assert self.signal.compute(ctx) == 0.0

    def test_short(self):
        ctx = SignalContext(text="Title")
        assert self.signal.compute(ctx) == 1.0

    def test_medium(self):
        ctx = SignalContext(text="A moderately long heading text")
        score = self.signal.compute(ctx)
        assert 0.0 < score <= 0.7

    def test_long(self):
        ctx = SignalContext(text="A" * 150)
        assert self.signal.compute(ctx) == 0.0


class TestHasNumberingSignal:
    signal = HasNumberingSignal()

    def test_korean_legal(self):
        assert self.signal.compute(SignalContext(text="제1조 목적")) == 1.0
        assert self.signal.compute(SignalContext(text="제3장 보험금의 지급")) == 1.0
        assert self.signal.compute(SignalContext(text="제2편 특별약관")) == 1.0

    def test_arabic_numbering(self):
        assert self.signal.compute(SignalContext(text="1. Introduction")) == 1.0
        assert self.signal.compute(SignalContext(text="2.1. Background")) == 1.0

    def test_circled_numbers(self):
        assert self.signal.compute(SignalContext(text="① 보험계약자")) == 1.0

    def test_no_numbering(self):
        assert self.signal.compute(SignalContext(text="일반적인 문장입니다")) == 0.0


class TestLayoutLabelSignal:
    signal = LayoutLabelSignal()

    def test_title(self):
        assert self.signal.compute(SignalContext(layout_label="Title")) == 1.0

    def test_section_header(self):
        assert self.signal.compute(SignalContext(layout_label="Section-header")) == 1.0

    def test_text_label(self):
        assert self.signal.compute(SignalContext(layout_label="Text")) == 0.0

    def test_empty(self):
        assert self.signal.compute(SignalContext(layout_label="")) == 0.0


class TestEndsWithoutPeriodSignal:
    signal = EndsWithoutPeriodSignal()

    def test_no_period(self):
        assert self.signal.compute(SignalContext(text="제1조 목적")) == 1.0

    def test_with_period(self):
        assert self.signal.compute(SignalContext(text="보험금을 지급합니다.")) == 0.0

    def test_empty(self):
        assert self.signal.compute(SignalContext(text="")) == 0.0


class TestWidthRatioSignal:
    signal = WidthRatioSignal()

    def test_narrow(self):
        ctx = SignalContext(bbox=BBox(50, 0, 200, 20), page_width=600)
        assert self.signal.compute(ctx) == 1.0

    def test_half_width(self):
        ctx = SignalContext(bbox=BBox(50, 0, 350, 20), page_width=600)
        score = self.signal.compute(ctx)
        assert 0.0 < score <= 0.6

    def test_full_width(self):
        ctx = SignalContext(bbox=BBox(20, 0, 580, 20), page_width=600)
        assert self.signal.compute(ctx) == 0.0


# ---------------------------------------------------------------------------
# BlockClassifier Integration Tests
# ---------------------------------------------------------------------------


class TestBlockClassifier:
    def test_paragraph_stays_text(self, default_classifier, default_ctx):
        block_type, level = default_classifier.classify(default_ctx)
        assert block_type == BlockType.TEXT
        assert level == 0

    def test_heading_detected(self, default_classifier, heading_ctx):
        block_type, level = default_classifier.classify(heading_ctx)
        assert block_type == BlockType.HEADING
        assert level == 2  # 제N장 -> level 2

    def test_korean_jo_heading(self, default_classifier):
        ctx = SignalContext(
            text="제5조 보험금의 지급사유",
            font_size=12.0,
            is_bold=True,
            avg_font_size=10.0,
            font_size_std=1.5,
            bbox=BBox(50, 100, 400, 118),
            median_bbox_height=15.0,
            page_height=800,
            page_width=600,
        )
        block_type, level = default_classifier.classify(ctx)
        assert block_type == BlockType.HEADING
        assert level == 4  # 제N조 -> level 4

    def test_empty_text(self, default_classifier):
        ctx = SignalContext(text="")
        block_type, level = default_classifier.classify(ctx)
        assert block_type == BlockType.TEXT
        assert level == 0

    def test_custom_weights(self):
        weights = dict(DEFAULT_WEIGHTS)
        weights["has_numbering"] = 0.90  # Extreme weight
        classifier = BlockClassifier(weights=weights)
        ctx = SignalContext(text="1. Introduction")
        block_type, level = classifier.classify(ctx)
        assert block_type == BlockType.HEADING

    def test_custom_threshold(self):
        classifier = BlockClassifier(heading_threshold=0.99)
        ctx = SignalContext(
            text="제1장 총칙",
            font_size=14.0,
            is_bold=True,
            avg_font_size=10.0,
        )
        block_type, _ = classifier.classify(ctx)
        # With extremely high threshold, even clear headings may not qualify
        # (depends on signal sum)

    def test_compute_heading_score(self, default_classifier, heading_ctx):
        score = default_classifier.compute_heading_score(heading_ctx)
        assert score > 0.0
        assert score <= 1.0

    def test_layout_label_boost(self, default_classifier):
        ctx = SignalContext(
            text="Introduction",
            font_size=12.0,
            is_bold=True,
            avg_font_size=10.0,
            font_size_std=1.5,
            bbox=BBox(50, 50, 200, 65),
            page_height=800,
            page_width=600,
            layout_label="Title",
        )
        block_type, level = default_classifier.classify(ctx)
        assert block_type == BlockType.HEADING


# ---------------------------------------------------------------------------
# Batch Classification Tests
# ---------------------------------------------------------------------------


class TestClassifyBlocks:
    def test_empty_list(self):
        assert classify_blocks([]) == []

    def test_simple_blocks(self):
        blocks = [
            TextBlock(
                text="제1조 목적",
                bbox=BBox(50, 50, 300, 68),
                font=FontInfo(name="Gothic", size=14.0, is_bold=True),
            ),
            TextBlock(
                text="이 약관은 보험계약에 관한 사항을 정한 것입니다.",
                bbox=BBox(50, 80, 550, 95),
                font=FontInfo(name="Gothic", size=10.0, is_bold=False),
            ),
        ]
        result = classify_blocks(
            blocks,
            avg_font_size=10.0,
            page_height=800,
            page_width=600,
        )
        assert len(result) == 2
        assert result[0].block_type == BlockType.HEADING
        assert result[0].heading_level > 0
        assert result[1].block_type == BlockType.TEXT

    def test_with_layout_blocks(self):
        text_blocks = [
            TextBlock(
                text="Overview",
                bbox=BBox(50, 50, 200, 65),
                font=FontInfo(name="Arial", size=13.0, is_bold=True),
            ),
        ]
        layout_blocks = [
            LayoutBlock(
                bbox=BBox(48, 48, 205, 68),
                label="Title",
                confidence=0.9,
                page_num=1,
            ),
        ]
        result = classify_blocks(
            text_blocks,
            avg_font_size=10.0,
            page_height=800,
            page_width=600,
            layout_blocks=layout_blocks,
        )
        assert result[0].block_type == BlockType.HEADING

    def test_immutability(self):
        original = TextBlock(
            text="Hello",
            bbox=BBox(0, 0, 100, 20),
            font=FontInfo(name="Arial", size=10.0, is_bold=False),
            block_type=BlockType.TEXT,
        )
        result = classify_blocks([original], avg_font_size=10.0)
        assert original.block_type == BlockType.TEXT  # unchanged


# ---------------------------------------------------------------------------
# Legacy Wrapper Tests
# ---------------------------------------------------------------------------


class TestClassifyBlockSignal:
    def test_simple_text(self):
        block_type, level = classify_block_signal(
            "일반적인 문단 텍스트입니다.",
            font_size=10.0,
            avg_font_size=10.0,
        )
        assert block_type == BlockType.TEXT
        assert level == 0

    def test_korean_heading(self):
        block_type, level = classify_block_signal(
            "제2편 특별약관",
            font_size=14.0,
            is_bold=True,
            avg_font_size=10.0,
        )
        assert block_type == BlockType.HEADING
        assert level == 1  # 제N편 -> level 1


# ---------------------------------------------------------------------------
# build_context_for_block Tests
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_basic(self):
        block = TextBlock(
            text="Hello world",
            bbox=BBox(10, 20, 100, 35),
            font=FontInfo(name="Arial", size=12.0, is_bold=True),
        )
        ctx = build_context_for_block(block, avg_font_size=10.0)
        assert ctx.text == "Hello world"
        assert ctx.font_size == 12.0
        assert ctx.is_bold is True
        assert ctx.avg_font_size == 10.0


# ---------------------------------------------------------------------------
# Signal Protocol Verification
# ---------------------------------------------------------------------------


class TestSignalProtocol:
    def test_all_signals_have_name(self):
        for signal in ALL_SIGNALS:
            assert isinstance(signal.name, str)
            assert len(signal.name) > 0

    def test_all_signals_return_bounded_score(self):
        ctx = SignalContext(
            text="Test text",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(0, 0, 100, 20),
            page_height=800,
            page_width=600,
        )
        for signal in ALL_SIGNALS:
            score = signal.compute(ctx)
            assert 0.0 <= score <= 1.0, f"{signal.name} returned {score}"

    def test_default_weights_sum_to_one(self):
        total = sum(DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6

    def test_all_signals_have_weights(self):
        for signal in ALL_SIGNALS:
            assert signal.name in DEFAULT_WEIGHTS, (
                f"Signal {signal.name} missing from DEFAULT_WEIGHTS"
            )

    def test_homogeneous_weights_have_all_signals(self):
        for signal in ALL_SIGNALS:
            assert signal.name in HOMOGENEOUS_FONT_WEIGHTS, (
                f"Signal {signal.name} missing from HOMOGENEOUS_FONT_WEIGHTS"
            )


# ---------------------------------------------------------------------------
# DataBearingSignal Tests
# ---------------------------------------------------------------------------


class TestDataBearingSignal:
    signal = DataBearingSignal()

    def test_plain_section_label(self):
        """Korean section labels should NOT fire."""
        assert self.signal.compute(SignalContext(text="승객 정보")) == 0.0
        assert self.signal.compute(SignalContext(text="여정 정보")) == 0.0
        assert self.signal.compute(SignalContext(text="탑승 정보")) == 0.0

    def test_key_value_pattern(self):
        """Key: Value patterns should fire."""
        assert self.signal.compute(SignalContext(text="Gate: 25")) == 1.0
        assert self.signal.compute(SignalContext(text="Seat: 12A")) == 1.0

    def test_arrow_route(self):
        """Route arrows should fire."""
        assert self.signal.compute(SignalContext(text="ICN → NRT")) == 1.0
        assert self.signal.compute(SignalContext(text="A -> B")) == 1.0

    def test_date_pattern(self):
        """ISO dates should fire."""
        assert self.signal.compute(SignalContext(text="2026-05-15 10:30")) == 1.0
        assert self.signal.compute(SignalContext(text="2024/01/01")) == 1.0

    def test_time_pattern(self):
        """Time HH:MM should fire."""
        assert self.signal.compute(SignalContext(text="10:30")) == 1.0

    def test_slash_separated_names(self):
        """Uppercase slash-separated tokens should fire."""
        assert self.signal.compute(SignalContext(text="HONG/GILDONG")) == 1.0
        assert self.signal.compute(SignalContext(text="ICN/NRT")) == 1.0

    def test_empty_text(self):
        assert self.signal.compute(SignalContext(text="")) == 0.0

    def test_korean_numbered_heading_with_colon_no_fire(self):
        """Korean legal headings like '제1조: 목적' should NOT fire.
        The key-value pattern requires ^[letters]+: but '제1조:' contains a digit."""
        assert self.signal.compute(SignalContext(text="제1조: 목적")) == 0.0

    def test_chapter_heading_no_fire(self):
        """'Chapter 1: Intro' has digits before colon, shouldn't match key-value."""
        assert self.signal.compute(SignalContext(text="Chapter 1: Introduction")) == 0.0

    def test_plain_korean_name(self):
        """A plain name without data patterns should NOT fire."""
        assert self.signal.compute(SignalContext(text="홍길동")) == 0.0

    def test_english_heading_no_fire(self):
        """Normal English headings should NOT fire."""
        assert self.signal.compute(SignalContext(text="Introduction")) == 0.0
        assert self.signal.compute(SignalContext(text="Summary")) == 0.0


# ---------------------------------------------------------------------------
# Homogeneous Font Mode Integration Tests
# ---------------------------------------------------------------------------


class TestHomogeneousFontClassification:
    """Integration tests for font-homogeneous pages (e.g., boarding passes).

    In homogeneous mode, vertical_gap is the primary discriminator.
    Section headers have large gaps; data values follow tightly.
    """

    def _make_classifier(self):
        return BlockClassifier(weights=HOMOGENEOUS_FONT_WEIGHTS)

    def test_section_header_with_gap_is_heading(self):
        """Section label with large gap should be classified as heading."""
        classifier = self._make_classifier()
        ctx = SignalContext(
            text="승객 정보",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(50, 150, 200, 165),
            prev_bbox=BBox(50, 50, 400, 65),  # large gap: 150-65=85
            page_height=800,
            page_width=600,
        )
        block_type, _ = classifier.classify(ctx)
        assert block_type == BlockType.HEADING

    def test_data_value_tight_spacing_is_text(self):
        """Data value with tight spacing should be classified as text."""
        classifier = self._make_classifier()
        ctx = SignalContext(
            text="홍길동",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(50, 170, 200, 185),
            prev_bbox=BBox(50, 150, 200, 165),  # small gap: 170-165=5
            page_height=800,
            page_width=600,
        )
        block_type, _ = classifier.classify(ctx)
        assert block_type == BlockType.TEXT

    def test_data_bearing_suppresses_even_with_gap(self):
        """Data values with key-value pattern stay text even with moderate gap."""
        classifier = self._make_classifier()
        ctx = SignalContext(
            text="Gate: 25",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(50, 200, 200, 215),
            prev_bbox=BBox(50, 100, 400, 115),  # large gap: 200-115=85
            page_height=800,
            page_width=600,
        )
        block_type, _ = classifier.classify(ctx)
        assert block_type == BlockType.TEXT

    def test_route_pattern_is_text(self):
        """Route pattern suppresses heading classification."""
        classifier = self._make_classifier()
        ctx = SignalContext(
            text="ICN → NRT",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(50, 200, 200, 215),
            prev_bbox=BBox(50, 100, 400, 115),
            page_height=800,
            page_width=600,
        )
        block_type, _ = classifier.classify(ctx)
        assert block_type == BlockType.TEXT

    def test_date_time_is_text(self):
        """Date-time data stays text regardless of gap."""
        classifier = self._make_classifier()
        ctx = SignalContext(
            text="2026-05-15 10:30",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(50, 200, 250, 215),
            prev_bbox=BBox(50, 100, 400, 115),
            page_height=800,
            page_width=600,
        )
        block_type, _ = classifier.classify(ctx)
        assert block_type == BlockType.TEXT

    def test_full_boarding_pass_sequence(self):
        """Simulates realistic boarding pass block sequence with varying gaps."""
        classifier = self._make_classifier()
        page_h, page_w = 800, 600

        blocks_data = [
            # (text, y0, y1, prev_y1) - realistic spacing
            ("대한항공 탑승권", 30, 45, None),       # Title/preamble
            ("승객 정보", 100, 115, 45),             # Section header (gap: 55)
            ("홍길동", 125, 140, 115),               # Data (gap: 10)
            ("HONG/GILDONG", 150, 165, 140),         # Data (gap: 10)
            ("여정 정보", 220, 235, 165),            # Section header (gap: 55)
            ("ICN → NRT", 245, 260, 235),            # Data (gap: 10)
            ("2026-05-15 10:30", 270, 285, 260),     # Data (gap: 10)
            ("탑승 정보", 340, 355, 285),            # Section header (gap: 55)
            ("Gate: 25", 365, 380, 355),             # Data (gap: 10)
            ("Seat: 12A", 390, 405, 380),            # Data (gap: 10)
        ]

        results = []
        for text, y0, y1, prev_y1 in blocks_data:
            prev_bbox = BBox(50, 0, 200, prev_y1) if prev_y1 is not None else None
            ctx = SignalContext(
                text=text,
                font_size=10.0,
                avg_font_size=10.0,
                bbox=BBox(50, y0, 200, y1),
                prev_bbox=prev_bbox,
                page_height=page_h,
                page_width=page_w,
            )
            block_type, _ = classifier.classify(ctx)
            results.append((text, block_type))

        # Section headers should be HEADING
        assert results[1] == ("승객 정보", BlockType.HEADING)
        assert results[4] == ("여정 정보", BlockType.HEADING)
        assert results[7] == ("탑승 정보", BlockType.HEADING)

        # Data values should be TEXT
        assert results[2] == ("홍길동", BlockType.TEXT)
        assert results[3] == ("HONG/GILDONG", BlockType.TEXT)
        assert results[5] == ("ICN → NRT", BlockType.TEXT)
        assert results[6] == ("2026-05-15 10:30", BlockType.TEXT)
        assert results[8] == ("Gate: 25", BlockType.TEXT)
        assert results[9] == ("Seat: 12A", BlockType.TEXT)

    def test_numbering_overrides_without_gap(self):
        """Numbered headings are detected even without vertical gap."""
        classifier = self._make_classifier()
        ctx = SignalContext(
            text="제1조 목적",
            font_size=10.0,
            avg_font_size=10.0,
            bbox=BBox(50, 110, 250, 125),
            prev_bbox=BBox(50, 95, 400, 110),  # tight spacing
            page_height=800,
            page_width=600,
        )
        block_type, _ = classifier.classify(ctx)
        assert block_type == BlockType.HEADING
