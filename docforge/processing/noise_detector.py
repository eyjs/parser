"""Noise detection and removal: headers, footers, page numbers, TOC, watermarks.

Strategy: scan all pages first to learn repeated patterns, then filter noise per page.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from docforge.domain.models import NoiseStats, TextBlock
from docforge.domain.value_objects import BBox
from docforge.infrastructure.config import ParserConfig


# Precompiled page number patterns
_PAGE_NUM_PATTERNS = [
    re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$"),
    re.compile(r"^\s*page\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),
    re.compile(r"^\s*제?\s*\d+\s*쪽\s*$"),
]


@dataclass
class LearnedPatterns:
    """Patterns learned from scanning the entire document."""

    header_patterns: frozenset[str]
    footer_patterns: frozenset[str]
    watermark_patterns: frozenset[str]


def _normalize_for_matching(text: str) -> str:
    """Normalize text for pattern matching: unify whitespace, remove pure numbers."""
    text = re.sub(r"\s+", " ", text).strip()
    if re.match(r"^\d+$", text):
        return ""
    return text


def learn_patterns(
    pages_data: list[dict[str, object]],
    config: ParserConfig,
) -> LearnedPatterns:
    """Learn repeated noise patterns from all pages.

    Args:
        pages_data: List of dicts with keys:
            - "lines": list of (text, y_center, font_size) tuples
            - "page_height": float
        config: Parser configuration.

    Returns:
        LearnedPatterns with header/footer/watermark patterns.
    """
    header_candidates: Counter[str] = Counter()
    footer_candidates: Counter[str] = Counter()
    watermark_candidates: Counter[str] = Counter()
    total_pages = len(pages_data)

    for page in pages_data:
        page_height = float(page["page_height"])  # type: ignore[arg-type]
        header_threshold = page_height * config.header_ratio
        footer_threshold = page_height * (1 - config.footer_ratio)
        center_low = page_height * 0.20
        center_high = page_height * 0.80

        for text, y_center, font_size in page["lines"]:  # type: ignore[union-attr]
            y = float(y_center)  # type: ignore[arg-type]
            normalized = _normalize_for_matching(str(text))
            if not normalized:
                continue

            if y < header_threshold:
                header_candidates[normalized] += 1
            elif y > footer_threshold:
                footer_candidates[normalized] += 1

            # Watermark: centered text that repeats with large font
            if center_low < y < center_high and float(font_size) > 20:  # type: ignore[arg-type]
                watermark_candidates[normalized] += 1

    threshold = min(config.min_noise_repeat, max(2, total_pages // 3))

    return LearnedPatterns(
        header_patterns=frozenset(t for t, c in header_candidates.items() if c >= threshold),
        footer_patterns=frozenset(t for t, c in footer_candidates.items() if c >= threshold),
        watermark_patterns=frozenset(t for t, c in watermark_candidates.items() if c >= threshold),
    )


def is_page_number(text: str) -> bool:
    """Check if text matches a page number pattern."""
    stripped = text.strip()
    return any(p.match(stripped) for p in _PAGE_NUM_PATTERNS)


def classify_noise(
    text: str,
    y_center: float,
    page_height: float,
    patterns: LearnedPatterns,
    config: ParserConfig,
) -> str | None:
    """Classify a line as noise and return the noise type, or None if content.

    Returns:
        "page_number", "header", "footer", "watermark", or None.
    """
    stripped = text.strip()
    if not stripped:
        return "empty"

    if is_page_number(stripped):
        return "page_number"

    normalized = _normalize_for_matching(stripped)
    if not normalized:
        return None

    header_threshold = page_height * config.header_ratio
    footer_threshold = page_height * (1 - config.footer_ratio)

    if y_center < header_threshold and normalized in patterns.header_patterns:
        return "header"

    if y_center > footer_threshold and normalized in patterns.footer_patterns:
        return "footer"

    center_low = page_height * 0.20
    center_high = page_height * 0.80
    if center_low < y_center < center_high and normalized in patterns.watermark_patterns:
        return "watermark"

    return None


# Leader dots patterns for TOC-like entries in tables
_LEADER_DOTS_RE = re.compile(r"\.{3,}|…{2,}")
_TOC_ENTRY_RE = re.compile(r"^.{2,50}\s*(?:\.{3,}|…{2,})\s*\d{1,4}\s*$")


def is_leader_dots_row(row_texts: list[str]) -> bool:
    """Check if a table row is a TOC-style leader dots entry.

    Leader dots rows look like: "제1조 ………………… 3"
    These should be filtered from table extraction results.
    """
    combined = " ".join(t.strip() for t in row_texts if t.strip())
    if not combined:
        return False
    # Check if the combined row text matches leader dots pattern
    if _TOC_ENTRY_RE.match(combined):
        return True
    # Also check individual cells for leader dots
    dots_cells = sum(1 for t in row_texts if _LEADER_DOTS_RE.search(t))
    return dots_cells > 0


def filter_leader_dots_from_table(
    cells: list[object],
    rows: int,
    cols: int,
) -> tuple[list[object], int]:
    """Remove leader dots rows from table cells.

    Returns:
        Tuple of (filtered_cells, new_row_count).
    """
    from docforge.domain.models import TableCell

    # Group cells by row
    row_map: dict[int, list[str]] = {}
    for cell in cells:
        if isinstance(cell, TableCell):
            if cell.row not in row_map:
                row_map[cell.row] = []
            row_map[cell.row].append(cell.text)

    # Find rows that are leader dots
    dots_rows: set[int] = set()
    for row_idx, texts in row_map.items():
        if is_leader_dots_row(texts):
            dots_rows.add(row_idx)

    if not dots_rows:
        return list(cells), rows  # type: ignore[arg-type]

    # Filter cells and re-index rows
    filtered: list[object] = []
    row_remap: dict[int, int] = {}
    new_idx = 0
    for old_idx in range(rows):
        if old_idx not in dots_rows:
            row_remap[old_idx] = new_idx
            new_idx += 1

    for cell in cells:
        if isinstance(cell, TableCell) and cell.row not in dots_rows:
            new_row = row_remap[cell.row]
            filtered.append(TableCell(
                text=cell.text,
                row=new_row,
                col=cell.col,
                colspan=cell.colspan,
                rowspan=cell.rowspan,
            ))

    return filtered, new_idx


def filter_noise_from_blocks(
    blocks: list[TextBlock],
    page_height: float,
    patterns: LearnedPatterns,
    config: ParserConfig,
) -> tuple[list[TextBlock], NoiseStats]:
    """Filter noise blocks and return clean blocks with noise statistics.

    Returns:
        Tuple of (clean_blocks, noise_stats_delta).
    """
    clean: list[TextBlock] = []
    headers = 0
    footers = 0
    page_numbers = 0
    watermarks = 0

    for block in blocks:
        noise_type = classify_noise(
            block.text,
            block.bbox.center_y,
            page_height,
            patterns,
            config,
        )
        if noise_type is None:
            clean.append(block)
        elif noise_type == "page_number":
            page_numbers += 1
        elif noise_type == "header":
            headers += 1
        elif noise_type == "footer":
            footers += 1
        elif noise_type == "watermark":
            watermarks += 1
        # "empty" blocks are silently dropped

    return clean, NoiseStats(
        headers=headers,
        footers=footers,
        page_numbers=page_numbers,
        watermarks=watermarks,
    )
