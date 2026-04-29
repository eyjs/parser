"""Domain-specific text structure profiles.

Each profile implements ``docforge.domain.ports.DomainProfile`` and
encapsulates the regex patterns + heuristics for a particular document
domain. The default ``KoreanLegalProfile`` preserves the historical
behaviour of ``text_structurer.classify_block``.
"""

from __future__ import annotations

from docforge.processing.domain_profiles.english_academic import (
    EnglishAcademicProfile,
)
from docforge.processing.domain_profiles.korean_legal import KoreanLegalProfile

__all__ = ["KoreanLegalProfile", "EnglishAcademicProfile", "get_profile"]


def get_profile(name: str):
    """Resolve a profile by configuration name.

    Falls back to ``KoreanLegalProfile`` for unknown names so a misconfigured
    ``ParserConfig.domain_profile`` cannot crash the pipeline.
    """
    normalized = (name or "").strip().lower()
    if normalized == "english_academic":
        return EnglishAcademicProfile()
    return KoreanLegalProfile()
