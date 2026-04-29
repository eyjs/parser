"""Tests for page type classification."""

from docforge.domain.enums import PageType
from docforge.infrastructure.config import ParserConfig
from docforge.processing.page_classifier import classify_page


class TestPageClassification:
    """Test page type classification logic."""

    def test_digital_page(self) -> None:
        config = ParserConfig()
        result = classify_page(
            char_count=500,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="일반 텍스트 내용",
            config=config,
        )
        assert result == PageType.DIGITAL

    def test_scanned_page(self) -> None:
        config = ParserConfig()
        result = classify_page(
            char_count=10,
            has_images=True,
            image_area_ratio=0.8,
            raw_text="",
            config=config,
        )
        assert result == PageType.SCANNED

    def test_mixed_page(self) -> None:
        config = ParserConfig()
        result = classify_page(
            char_count=200,
            has_images=True,
            image_area_ratio=0.3,
            raw_text="텍스트와 이미지 혼합",
            config=config,
        )
        assert result == PageType.MIXED

    def test_empty_page_noise(self) -> None:
        config = ParserConfig()
        result = classify_page(
            char_count=2,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="",
            config=config,
        )
        assert result == PageType.NOISE

    def test_toc_page_noise(self) -> None:
        config = ParserConfig()
        toc_text = "\n".join([
            "제1장 총칙 ......... 3",
            "제2장 보험금 ......... 10",
            "제3장 보험료 ......... 20",
            "제4장 해약 ......... 30",
            "제5장 분쟁 ......... 40",
        ])
        result = classify_page(
            char_count=200,
            has_images=False,
            image_area_ratio=0.0,
            raw_text=toc_text,
            config=config,
        )
        assert result == PageType.NOISE

    def test_low_text_no_images_scanned(self) -> None:
        config = ParserConfig()
        result = classify_page(
            char_count=20,
            has_images=False,
            image_area_ratio=0.0,
            raw_text="짧은 텍스트",
            config=config,
        )
        assert result == PageType.SCANNED

    def test_garbled_text_scanned(self) -> None:
        config = ParserConfig()
        garbled = "Ư��/��Ÿ/�ݷ��" * 10
        result = classify_page(
            char_count=len(garbled),
            has_images=False,
            image_area_ratio=0.0,
            raw_text=garbled,
            config=config,
        )
        assert result == PageType.SCANNED
