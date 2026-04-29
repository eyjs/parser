"""Internal helpers for ``parse_pdf``.

Kept private (leading underscore) — extracted to keep ``parse_pdf.py``
focused on the top-level orchestration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from docforge.domain.enums import DocumentComplexity
from docforge.domain.models import (
    LLMFallbackRecord,
    Metadata,
    NoiseStats,
    PageContent,
    RegionVLMRecord,
    Table,
)
from docforge.infrastructure.config import ParserConfig
from docforge.processing import markdown_assembler, noise_detector, table_merger
from docforge.usecases.page_processor import PageResult

if TYPE_CHECKING:
    from docforge.domain.ports import VisionLLMEngine

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def learn_noise(reader, doc, total_pages: int, config: ParserConfig):
    pages_data: list[dict[str, object]] = []
    for page_idx in range(total_pages):
        blocks = reader.extract_text_blocks(doc, page_idx)
        _, page_height = reader.get_page_dimensions(doc, page_idx)
        lines = [(b.text, b.bbox.center_y, b.font.size) for b in blocks]
        pages_data.append({"lines": lines, "page_height": page_height})
    return noise_detector.learn_patterns(pages_data, config)


def doc_stats(reader, doc, total_pages: int) -> tuple[float, float]:
    font_sizes = reader.get_font_sizes(doc)
    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0

    all_gaps: list[float] = []
    sample_pages = min(10, total_pages)
    for page_idx in range(sample_pages):
        all_gaps.extend(reader.get_line_gaps(doc, page_idx))
    avg_line_gap = sum(all_gaps) / len(all_gaps) if all_gaps else 5.0
    return avg_font_size, avg_line_gap


def assemble_page_markdowns(parsed_pages, avg_font_size, config, on_page_done):
    page_markdowns: list[str] = []
    for page in parsed_pages:
        md = markdown_assembler.assemble_page(page, avg_font_size, config)
        if md.strip():
            page_markdowns.append(md)
            if on_page_done:
                try:
                    on_page_done(page.page_num, md)
                except Exception:
                    pass
    return page_markdowns


def log_records(llm_records, vlm_records) -> None:
    for record in llm_records:
        logger.info(
            "LLM fallback page=%d adopted=%s reason=%s",
            record.page_num, record.adopted, record.reason,
        )
    for record in vlm_records:
        logger.info(
            "Region VLM page=%d replaced=%s score=%.3f reason=%s",
            record.page_num, record.replaced, record.quality_score, record.reason,
        )


def build_morpheme_analyzer():
    try:
        from docforge.adapters.morpheme_analyzer import KiwiMorphemeAnalyzer
        analyzer = KiwiMorphemeAnalyzer()
        if analyzer.is_available():
            return analyzer
        from docforge.adapters.morpheme_analyzer import NullMorphemeAnalyzer
        return NullMorphemeAnalyzer()
    except Exception:
        from docforge.adapters.morpheme_analyzer import NullMorphemeAnalyzer
        logger.info("Morpheme analyzer unavailable, heading split disabled")
        return NullMorphemeAnalyzer()


def check_preprocessing() -> bool:
    try:
        from docforge.adapters.opencv_preprocessor import OpenCVPreprocessor  # noqa: F401
        from docforge.processing.preprocessing_router import process_scanned_page  # noqa: F401
        return True
    except ImportError:
        logger.info("OpenCV not available, preprocessing disabled")
        return False


def build_llm_engine(config: ParserConfig) -> "VisionLLMEngine | None":
    if not (config.llm_fallback_enabled or config.region_vlm_enabled):
        return None
    try:
        from docforge.adapters.vision_llm_engine import Qwen2VLMLXEngine
        candidate = Qwen2VLMLXEngine()
        if candidate.is_available():
            return candidate
        logger.info("LLM fallback disabled — mlx_vlm not installed")
        return None
    except Exception:
        logger.warning("LLM engine init failed, LLM fallback disabled", exc_info=True)
        return None


def aggregate_results(ordered_results: list[PageResult]) -> tuple[
    list[PageContent],
    list[tuple[list[Table], float, float]],
    NoiseStats,
    bool,
    list[LLMFallbackRecord],
    list[RegionVLMRecord],
]:
    parsed_pages: list[PageContent] = []
    all_page_tables: list[tuple[list[Table], float, float]] = []
    accumulated_noise = NoiseStats()
    toc_page_count = 0
    ocr_actually_used = False
    llm_records: list[LLMFallbackRecord] = []
    vlm_records: list[RegionVLMRecord] = []

    for pr in ordered_results:
        if pr.is_toc:
            toc_page_count += 1
            continue
        if pr.page_content is not None:
            parsed_pages.append(pr.page_content)
        if pr.tables_info is not None:
            all_page_tables.append(pr.tables_info)
        accumulated_noise = NoiseStats(
            headers=accumulated_noise.headers + pr.noise.headers,
            footers=accumulated_noise.footers + pr.noise.footers,
            page_numbers=accumulated_noise.page_numbers + pr.noise.page_numbers,
            watermarks=accumulated_noise.watermarks + pr.noise.watermarks,
        )
        if pr.ocr_used:
            ocr_actually_used = True
        if pr.llm_record is not None:
            llm_records.append(pr.llm_record)
        vlm_records.extend(pr.region_vlm_records)

    noise_with_toc = NoiseStats(
        headers=accumulated_noise.headers,
        footers=accumulated_noise.footers,
        page_numbers=accumulated_noise.page_numbers,
        toc_pages=toc_page_count,
        toc_entries=accumulated_noise.toc_entries,
        watermarks=accumulated_noise.watermarks,
    )
    return (
        parsed_pages, all_page_tables, noise_with_toc,
        ocr_actually_used, llm_records, vlm_records,
    )


def merge_cross_page_tables(
    parsed_pages: list[PageContent],
    all_page_tables: list[tuple[list[Table], float, float]],
    config: ParserConfig,
) -> list[PageContent]:
    if not all_page_tables:
        return parsed_pages

    merged = table_merger.merge_cross_page_tables(all_page_tables, config)
    updated: list[PageContent] = []
    idx = 0
    for page in parsed_pages:
        if idx < len(merged):
            tables = merged[idx]
            idx += 1
        else:
            tables = list(page.tables)
        updated.append(PageContent(
            page_num=page.page_num,
            page_type=page.page_type,
            blocks=page.blocks,
            tables=tuple(tables),
            raw_text=page.raw_text,
            width=page.width,
            height=page.height,
        ))
    return updated


def build_metadata(
    *,
    pdf_path: Path,
    total_pages: int,
    parsed_pages: list[PageContent],
    profile,
    use_ocr: bool,
    ocr_actually_used: bool,
    noise_with_toc: NoiseStats,
) -> Metadata:
    if use_ocr and profile.complexity == DocumentComplexity.IMAGE_HEAVY:
        source_type = "scanned_pdf"
    elif profile.complexity == DocumentComplexity.MIXED:
        source_type = "mixed_pdf"
    else:
        source_type = "digital_pdf"

    tables_need_review = sum(
        sum(1 for t in p.tables if t.needs_review) for p in parsed_pages
    )

    return Metadata(
        source=pdf_path.name,
        source_type=source_type,
        pages=total_pages,
        parsed_at=datetime.now(KST).isoformat(),
        parser_version="1.0.0",
        ocr_used=ocr_actually_used,
        tables_extracted=sum(len(p.tables) for p in parsed_pages),
        tables_need_review=tables_need_review,
        noise_removed=noise_with_toc,
    )
