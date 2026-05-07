"""Internal helpers for ``parse_pdf``.

Kept private (leading underscore) — extracted to keep ``parse_pdf.py``
focused on the top-level orchestration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import re

from docforge.domain.enums import BlockType, DocumentComplexity
from docforge.domain.models import (
    LLMFallbackRecord,
    Metadata,
    NoiseStats,
    PageContent,
    RegionVLMRecord,
    Table,
    TextBlock,
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


def build_layout_detector(config: ParserConfig):
    """Construct the layout detector indicated by ``config``.

    Returns a :class:`NullLayoutDetector` when layout detection is
    disabled or Surya is not importable. Adapters are imported lazily
    so the cold path stays fast.
    """
    from docforge.adapters.layout import NullLayoutDetector, SuryaLayoutDetector

    if not config.layout_detection_enabled:
        return NullLayoutDetector()
    try:
        detector = SuryaLayoutDetector()
        if detector.is_available():
            return detector
        logger.info("Surya not installed — falling back to NullLayoutDetector")
        return NullLayoutDetector()
    except Exception:  # pragma: no cover - defensive
        logger.warning("Layout detector init failed", exc_info=True)
        return NullLayoutDetector()


def build_llm_engine(config: ParserConfig) -> "VisionLLMEngine | None":
    """Build a VLM engine using the fallback chain: local -> cloud.

    Respects ``config.vlm_provider``:
      - ``"auto"``: try local Qwen2-VL, then cloud (OpenAI -> Anthropic)
      - ``"local"``: local only, None if unavailable
      - ``"openai"`` / ``"anthropic"``: cloud only with specified provider
    """
    if not (config.llm_fallback_enabled or config.region_vlm_enabled):
        return None

    provider = config.vlm_provider

    # 1) Local Qwen2-VL attempt
    if provider in ("auto", "local"):
        engine = _try_local_vlm()
        if engine is not None:
            return engine
        if provider == "local":
            logger.info("VLM disabled — local Qwen2-VL not available and provider=local")
            return None

    # 2) Cloud VLM attempt
    if provider in ("auto", "openai", "anthropic"):
        engine = _try_cloud_vlm(provider)
        if engine is not None:
            return engine

    logger.info("VLM disabled — no available VLM engine")
    return None


def _try_local_vlm() -> "VisionLLMEngine | None":
    """Attempt to create a local Qwen2-VL MLX engine."""
    try:
        from docforge.adapters.vision_llm_engine import Qwen2VLMLXEngine
        candidate = Qwen2VLMLXEngine()
        if candidate.is_available():
            logger.info("VLM engine: local Qwen2-VL MLX")
            return candidate
        logger.info("Local Qwen2-VL not available (mlx_vlm not installed or model not cached)")
        return None
    except Exception:
        logger.warning("Local VLM engine init failed", exc_info=True)
        return None


def _try_cloud_vlm(provider: str) -> "VisionLLMEngine | None":
    """Attempt to create a cloud VLM engine (OpenAI or Anthropic)."""
    try:
        from docforge.adapters.cloud_vlm_engine import CloudVisionEngine
        cloud_provider = provider if provider in ("openai", "anthropic") else "auto"
        candidate = CloudVisionEngine(provider=cloud_provider)
        if candidate.is_available():
            logger.info("VLM engine: cloud (%s)", cloud_provider)
            return candidate
        logger.info("Cloud VLM not available (no API keys configured)")
        return None
    except Exception:
        logger.warning("Cloud VLM engine init failed", exc_info=True)
        return None


def aggregate_results(ordered_results: list[PageResult]) -> tuple[
    list[PageContent],
    list[tuple[list[Table], float, float]],
    NoiseStats,
    bool,
    list[LLMFallbackRecord],
    list[RegionVLMRecord],
    dict[str, int | float],
]:
    parsed_pages: list[PageContent] = []
    all_page_tables: list[tuple[list[Table], float, float]] = []
    accumulated_noise = NoiseStats()
    toc_page_count = 0
    ocr_actually_used = False
    llm_records: list[LLMFallbackRecord] = []
    vlm_records: list[RegionVLMRecord] = []

    # Phase 3: block-level retry statistics
    total_blocks_retried = 0
    total_blocks_fallback_ocr = 0
    total_blocks_fallback_vlm = 0
    quality_scores: list[float] = []

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

        # Phase 3: accumulate retry stats
        total_blocks_retried += pr.blocks_retried
        total_blocks_fallback_ocr += pr.blocks_fallback_ocr
        total_blocks_fallback_vlm += pr.blocks_fallback_vlm
        if pr.avg_block_quality < 1.0:
            quality_scores.append(pr.avg_block_quality)

    noise_with_toc = NoiseStats(
        headers=accumulated_noise.headers,
        footers=accumulated_noise.footers,
        page_numbers=accumulated_noise.page_numbers,
        toc_pages=toc_page_count,
        toc_entries=accumulated_noise.toc_entries,
        watermarks=accumulated_noise.watermarks,
    )

    avg_block_quality = (
        round(sum(quality_scores) / len(quality_scores), 4)
        if quality_scores else 1.0
    )
    retry_stats: dict[str, int | float] = {
        "blocks_retried": total_blocks_retried,
        "blocks_fallback_ocr": total_blocks_fallback_ocr,
        "blocks_fallback_vlm": total_blocks_fallback_vlm,
        "avg_block_quality": avg_block_quality,
    }

    return (
        parsed_pages, all_page_tables, noise_with_toc,
        ocr_actually_used, llm_records, vlm_records,
        retry_stats,
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
            confidence=page.confidence,
            images=page.images,
        ))
    return updated


_SIMPLE_NUMBERED_RE = re.compile(r"^\d{1,2}\.\s+\S")
_MAX_PROMOTE_LENGTH = 80


def promote_numbered_headings(
    parsed_pages: list[PageContent],
) -> list[PageContent]:
    """Promote 'N. ' subclauses to headings when the document has none.

    Documents like 사업방법서 use simple numbered format (1., 2., ...)
    without bold/size distinction. When no HEADING blocks exist at all,
    short 'N. ' SUBCLAUSE blocks are promoted to HEADING h3.
    """
    has_heading = any(
        b.block_type == BlockType.HEADING
        for p in parsed_pages
        for b in p.blocks
    )
    if has_heading:
        return parsed_pages

    updated: list[PageContent] = []
    for page in parsed_pages:
        new_blocks: list[TextBlock] = []
        changed = False
        for b in page.blocks:
            if (
                b.block_type == BlockType.SUBCLAUSE
                and _SIMPLE_NUMBERED_RE.match(b.text.strip())
                and len(b.text.strip()) <= _MAX_PROMOTE_LENGTH
            ):
                new_blocks.append(TextBlock(
                    text=b.text,
                    bbox=b.bbox,
                    font=b.font,
                    block_type=BlockType.HEADING,
                    heading_level=3,
                    confidence=b.confidence,
                    block_id=b.block_id,
                    parent_id=b.parent_id,
                ))
                changed = True
            else:
                new_blocks.append(b)
        if changed:
            updated.append(PageContent(
                page_num=page.page_num,
                page_type=page.page_type,
                blocks=tuple(new_blocks),
                tables=page.tables,
                raw_text=page.raw_text,
                width=page.width,
                height=page.height,
                confidence=page.confidence,
                images=page.images,
            ))
        else:
            updated.append(page)
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
