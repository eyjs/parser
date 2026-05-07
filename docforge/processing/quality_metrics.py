"""Parsing quality metrics calculation and anomaly detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from docforge.domain.models import ParseStats, NoiseStats, PageContent


@dataclass(frozen=True)
class QualityWarning:
    """A quality anomaly detected in the parsing result."""

    code: str
    message: str
    severity: str  # "info", "warning", "error"


def calculate_metrics(
    pages: list[PageContent],
    markdown: str,
    noise_stats: NoiseStats,
    parse_time_ms: float,
    retry_stats: dict[str, int | float] | None = None,
) -> ParseStats:
    """Calculate quality metrics from parsed pages and generated markdown.

    Args:
        pages: List of parsed page contents.
        markdown: The generated markdown string.
        noise_stats: Accumulated noise removal statistics.
        parse_time_ms: Total parsing time in milliseconds.
        retry_stats: Phase 3 block-level retry statistics (optional).

    Returns:
        ParseStats with all quality metrics.
    """
    total_pages = len(pages)
    parsed_pages = sum(1 for p in pages if p.blocks or p.tables)
    tables_found = sum(len(p.tables) for p in pages)
    tables_need_review = sum(
        sum(1 for t in p.tables if t.needs_review) for p in pages
    )
    text_blocks = sum(len(p.blocks) for p in pages)

    # Markdown analysis
    lines = markdown.split("\n")
    non_empty_lines = [ln for ln in lines if ln.strip()]
    total_lines = len(lines)

    heading_count = sum(1 for ln in lines if re.match(r"^#{1,6}\s", ln))

    empty_line_ratio = 0.0
    if total_lines > 0:
        empty_line_ratio = (total_lines - len(non_empty_lines)) / total_lines

    avg_line_length = 0.0
    if non_empty_lines:
        avg_line_length = sum(len(ln) for ln in non_empty_lines) / len(non_empty_lines)

    # Phase 3: block-level retry statistics
    rs = retry_stats or {}

    return ParseStats(
        total_pages=total_pages,
        parsed_pages=parsed_pages,
        tables_found=tables_found,
        tables_need_review=tables_need_review,
        text_blocks=text_blocks,
        heading_count=heading_count,
        empty_line_ratio=round(empty_line_ratio, 3),
        avg_line_length=round(avg_line_length, 1),
        noise_removed=noise_stats,
        parse_time_ms=round(parse_time_ms, 1),
        blocks_retried=int(rs.get("blocks_retried", 0)),
        blocks_fallback_ocr=int(rs.get("blocks_fallback_ocr", 0)),
        blocks_fallback_vlm=int(rs.get("blocks_fallback_vlm", 0)),
        avg_block_quality=float(rs.get("avg_block_quality", 1.0)),
    )


def detect_anomalies(stats: ParseStats) -> list[QualityWarning]:
    """Detect quality anomalies from parsing statistics.

    Returns:
        List of QualityWarning objects.
    """
    warnings: list[QualityWarning] = []

    if stats.heading_count == 0:
        warnings.append(QualityWarning(
            code="NO_HEADINGS",
            message="No headings detected - structure recognition may have failed",
            severity="warning",
        ))

    if stats.empty_line_ratio > 0.5:
        warnings.append(QualityWarning(
            code="HIGH_EMPTY_RATIO",
            message=f"High empty line ratio ({stats.empty_line_ratio:.1%}) - check parsing quality",
            severity="warning",
        ))

    if stats.tables_need_review > 0:
        warnings.append(QualityWarning(
            code="TABLES_NEED_REVIEW",
            message=f"{stats.tables_need_review} table(s) need manual review",
            severity="info",
        ))

    if stats.parsed_pages < stats.total_pages * 0.5:
        warnings.append(QualityWarning(
            code="LOW_PARSE_RATE",
            message=(
                f"Only {stats.parsed_pages}/{stats.total_pages} pages parsed - "
                "many pages may be noise or image-only"
            ),
            severity="warning",
        ))

    return warnings
