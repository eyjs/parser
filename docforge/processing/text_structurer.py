"""Text structure recognition -- domain-agnostic facade.

Delegates classification to a ``DomainProfile`` implementation. The default
profile (``KoreanLegalProfile``) preserves the historical behaviour for
Korean legal/insurance documents; other domains are selected by injecting
a different profile (e.g. ``EnglishAcademicProfile``).

This module no longer owns any regex constants -- they live in
``docforge.processing.domain_profiles``.

The new signal-based ``BlockClassifier`` from ``block_classifier.py`` is
available via ``classify_block_signal()`` for callers that can provide
richer context (bbox, page dimensions, layout labels). The legacy
``classify_block()`` function is preserved exactly as-is for backward
compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docforge.domain.enums import BlockType
from docforge.infrastructure.config import ParserConfig

if TYPE_CHECKING:
    from docforge.domain.ports import DomainProfile


def classify_block(
    text: str,
    font_size: float = 0.0,
    is_bold: bool = False,
    avg_font_size: float = 0.0,
    config: ParserConfig | None = None,
    domain_profile: "DomainProfile | None" = None,
) -> tuple[BlockType, int]:
    """Classify a text block into its structural type and heading level.

    This is the **legacy** entry point. It delegates to a ``DomainProfile``
    and uses only font_size + regex heuristics. For the signal-based
    classifier see :func:`block_classifier.classify_block_signal`.

    Args:
        text: Raw block text.
        font_size: Block font size, used for font-based heading detection.
        is_bold: Whether the block is rendered bold.
        avg_font_size: Document-average font size.
        config: Parser configuration (provides heading ratio thresholds).
            Defaults to ``ParserConfig()``.
        domain_profile: Optional profile override. When ``None`` the
            ``KoreanLegalProfile`` is used so existing call sites keep
            their behaviour.

    Returns:
        Tuple of ``(BlockType, heading_level)``. ``heading_level`` is 0
        for non-heading blocks.
    """
    if config is None:
        config = ParserConfig()

    if domain_profile is None:
        # Imported lazily to avoid a circular import at module load time
        # (domain_profiles -> domain.enums; safe but kept lazy for symmetry
        # with the optional override path).
        from docforge.processing.domain_profiles import KoreanLegalProfile

        domain_profile = KoreanLegalProfile()

    return domain_profile.classify(
        text=text,
        font_size=font_size,
        is_bold=is_bold,
        avg_font_size=avg_font_size,
        heading_bold_ratio=config.heading_bold_ratio,
        heading_size_ratio=config.heading_size_ratio,
    )
