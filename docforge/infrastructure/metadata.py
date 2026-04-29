"""YAML front matter generation for parsed markdown output."""

from __future__ import annotations

from docforge.domain.models import Metadata


def generate_front_matter(metadata: Metadata) -> str:
    """Generate YAML front matter string from Metadata."""
    noise = metadata.noise_removed
    lines = [
        "---",
        f'source: "{metadata.source}"',
        f'source_type: "{metadata.source_type}"',
        f"pages: {metadata.pages}",
        f'parsed_at: "{metadata.parsed_at}"',
        f'parser_version: "{metadata.parser_version}"',
        f"ocr_used: {'true' if metadata.ocr_used else 'false'}",
        f"tables_extracted: {metadata.tables_extracted}",
        f"tables_need_review: {metadata.tables_need_review}",
        "noise_removed:",
        f"  headers: {noise.headers}",
        f"  footers: {noise.footers}",
        f"  page_numbers: {noise.page_numbers}",
        f"  toc_pages: {noise.toc_pages}",
        "---",
    ]
    return "\n".join(lines)
