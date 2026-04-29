"""Tests for domain profile classification (P1-1)."""

from __future__ import annotations

from docforge.domain.enums import BlockType
from docforge.processing.domain_profiles import (
    EnglishAcademicProfile,
    KoreanLegalProfile,
    get_profile,
)
from docforge.processing.text_structurer import classify_block


HBR = 1.3   # heading_bold_ratio
HSR = 1.2   # heading_size_ratio


class TestKoreanLegalProfile:
    """KoreanLegalProfile must reproduce the historical text_structurer rules."""

    profile = KoreanLegalProfile()

    def test_name(self) -> None:
        assert self.profile.name() == "korean_legal"

    def test_pyeon_h1(self) -> None:
        assert self.profile.classify("제1편 총칙", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 1,
        )

    def test_jang_h2(self) -> None:
        assert self.profile.classify("제2장 보험금의 지급", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 2,
        )

    def test_jo_h4(self) -> None:
        assert self.profile.classify("제5조(목적) 이 약관은", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 4,
        )

    def test_clause_circled(self) -> None:
        bt, lvl = self.profile.classify("① 보험계약자는", 0, False, 0, HBR, HSR)
        assert bt == BlockType.CLAUSE
        assert lvl == 0

    def test_subclause_numbered(self) -> None:
        bt, _ = self.profile.classify("1. 피보험자", 0, False, 0, HBR, HSR)
        assert bt == BlockType.SUBCLAUSE

    def test_item_paren(self) -> None:
        bt, _ = self.profile.classify("가) 입원", 0, False, 0, HBR, HSR)
        assert bt == BlockType.ITEM

    def test_plain_text(self) -> None:
        bt, lvl = self.profile.classify("일반 본문 입니다.", 0, False, 0, HBR, HSR)
        assert bt == BlockType.TEXT
        assert lvl == 0


class TestEnglishAcademicProfile:
    """Stub profile for English academic documents."""

    profile = EnglishAcademicProfile()

    def test_name(self) -> None:
        assert self.profile.name() == "english_academic"

    def test_chapter_h1(self) -> None:
        assert self.profile.classify("Chapter 3 Methodology", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 1,
        )

    def test_section_h2(self) -> None:
        assert self.profile.classify("Section 2 Background", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 2,
        )

    def test_numeric_section_levels(self) -> None:
        assert self.profile.classify("1. Introduction", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 2,
        )
        assert self.profile.classify("1.1 Background", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 3,
        )
        assert self.profile.classify("1.1.1 Scope", 0, False, 0, HBR, HSR) == (
            BlockType.HEADING, 4,
        )

    def test_figure_caption(self) -> None:
        bt, _ = self.profile.classify("Figure 1: Architecture", 0, False, 0, HBR, HSR)
        assert bt == BlockType.ITEM

    def test_table_caption(self) -> None:
        bt, _ = self.profile.classify("Table 2 Comparison", 0, False, 0, HBR, HSR)
        assert bt == BlockType.ITEM

    def test_bullet_item(self) -> None:
        bt, _ = self.profile.classify("- first bullet item", 0, False, 0, HBR, HSR)
        assert bt == BlockType.ITEM

    def test_plain_paragraph(self) -> None:
        bt, lvl = self.profile.classify(
            "This is a normal paragraph.", 0, False, 0, HBR, HSR,
        )
        assert bt == BlockType.TEXT
        assert lvl == 0


class TestProfilesProduceDifferentResults:
    """Ensure profiles are actually distinguishable on the same input."""

    def test_chapter_only_recognized_by_english(self) -> None:
        text = "Chapter 1 Introduction"
        ko = KoreanLegalProfile().classify(text, 0, False, 0, HBR, HSR)
        en = EnglishAcademicProfile().classify(text, 0, False, 0, HBR, HSR)
        assert ko == (BlockType.TEXT, 0)
        assert en == (BlockType.HEADING, 1)

    def test_jang_only_recognized_by_korean(self) -> None:
        text = "제2장 보험금의 지급"
        ko = KoreanLegalProfile().classify(text, 0, False, 0, HBR, HSR)
        en = EnglishAcademicProfile().classify(text, 0, False, 0, HBR, HSR)
        assert ko == (BlockType.HEADING, 2)
        assert en == (BlockType.TEXT, 0)


class TestGetProfile:
    """Profile resolution from config strings."""

    def test_default_is_korean_legal(self) -> None:
        assert isinstance(get_profile("korean_legal"), KoreanLegalProfile)

    def test_english_academic(self) -> None:
        assert isinstance(get_profile("english_academic"), EnglishAcademicProfile)

    def test_unknown_falls_back_to_korean(self) -> None:
        # Misconfiguration must not crash the pipeline.
        assert isinstance(get_profile("klingon"), KoreanLegalProfile)
        assert isinstance(get_profile(""), KoreanLegalProfile)


class TestClassifyBlockBackwardCompat:
    """``classify_block`` must keep its historical signature/behaviour."""

    def test_no_profile_uses_korean_legal(self) -> None:
        bt, lvl = classify_block("제1편 총칙")
        assert bt == BlockType.HEADING
        assert lvl == 1

    def test_inject_english_profile(self) -> None:
        bt, lvl = classify_block(
            "Chapter 1 Intro",
            domain_profile=EnglishAcademicProfile(),
        )
        assert bt == BlockType.HEADING
        assert lvl == 1
