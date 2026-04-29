"""Region-level VLM router for low-quality table re-extraction.

Pure business logic: receives a cropped image (RawImage) and delegates
to VisionLLMEngine. Image cropping is performed at the usecases/adapters level.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from docforge.domain.models import RegionVLMRecord, Table, TableCell
from docforge.domain.value_objects import BBox, RawImage

if TYPE_CHECKING:
    from docforge.domain.ports import VisionLLMEngine

logger = logging.getLogger(__name__)

_TABLE_PROMPT = """\
이 표의 모든 셀 내용을 빠짐없이 마크다운 표로 변환하세요.
도메인: {domain_hint}
요구사항:
- 모든 행과 열을 정확히 유지
- 빈 셀도 빈 칸으로 표현
- 병합 셀은 적절히 분리
- 출력: 마크다운 표만 (설명 없이)
"""


def route_table_to_vlm(
    cropped_image: RawImage,
    original_bbox: BBox,
    quality_score: float,
    page_num: int,
    llm_engine: VisionLLMEngine,
    domain_hint: str = "보험약관",
) -> tuple[Table | None, RegionVLMRecord]:
    """Attempt VLM-based table extraction on a cropped region image.

    Args:
        cropped_image: Pre-cropped image of the table region.
        original_bbox: Original table BBox in PDF coordinate space.
        quality_score: Quality score of the original table (for logging).
        page_num: Page number (1-based) for audit record.
        llm_engine: VisionLLMEngine instance for VLM calls.
        domain_hint: Domain hint for the VLM prompt.

    Returns:
        Tuple of (replacement Table or None, RegionVLMRecord).
    """
    prompt_hint = _TABLE_PROMPT.format(domain_hint=domain_hint or "문서")

    try:
        # VisionLLMEngine.correct_page expects (image, ocr_blocks, prompt_hint)
        # We pass empty blocks and the table-specific prompt
        result_blocks = llm_engine.correct_page(
            image=cropped_image,
            ocr_blocks=[],
            prompt_hint=prompt_hint,
        )
    except Exception:
        logger.warning(
            "Region VLM failed for page %d bbox %s", page_num, original_bbox,
            exc_info=True,
        )
        return None, RegionVLMRecord(
            page_num=page_num,
            table_bbox=original_bbox,
            quality_score=quality_score,
            replaced=False,
            reason="VLM invocation failed",
        )

    # Extract markdown text from VLM result blocks
    vlm_text = "\n".join(b.text for b in result_blocks)
    table = parse_markdown_table(vlm_text, original_bbox)

    if table is None:
        return None, RegionVLMRecord(
            page_num=page_num,
            table_bbox=original_bbox,
            quality_score=quality_score,
            replaced=False,
            reason="VLM response did not contain a valid markdown table",
        )

    return table, RegionVLMRecord(
        page_num=page_num,
        table_bbox=original_bbox,
        quality_score=quality_score,
        replaced=True,
        reason=f"VLM table adopted (original quality={quality_score:.3f})",
    )


def parse_markdown_table(text: str, bbox: BBox) -> Table | None:
    """Parse markdown table text into a Table domain object.

    Handles both fenced code blocks and raw pipe-delimited rows.

    Args:
        text: Raw text potentially containing a markdown table.
        bbox: BBox to assign to the resulting Table.

    Returns:
        Table object or None if no valid table found.
    """
    # Extract table lines from fenced code block or raw text
    table_lines = _extract_table_lines(text)
    if not table_lines:
        return None

    # Filter separator rows (---|---|---)
    data_lines = [
        line for line in table_lines
        if not re.match(r"^\|[\s\-:|]+\|$", line.strip())
    ]

    if len(data_lines) < 2:
        return None

    cells: list[TableCell] = []
    max_cols = 0

    for row_idx, line in enumerate(data_lines):
        # Split by | and strip
        raw_cells = line.strip().strip("|").split("|")
        col_values = [c.strip() for c in raw_cells]
        max_cols = max(max_cols, len(col_values))

        for col_idx, value in enumerate(col_values):
            cells.append(TableCell(
                text=value,
                row=row_idx,
                col=col_idx,
            ))

    if not cells or max_cols < 2:
        return None

    return Table(
        cells=tuple(cells),
        rows=len(data_lines),
        cols=max_cols,
        bbox=bbox,
        confidence=0.85,
        needs_review=False,
    )


def _extract_table_lines(text: str) -> list[str]:
    """Extract pipe-delimited table lines from text.

    Looks for lines containing | characters, optionally inside a fenced block.
    """
    lines = text.strip().split("\n")

    # Try to find fenced code block first
    in_fence = False
    fenced_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                break
            in_fence = True
            continue
        if in_fence and "|" in stripped:
            fenced_lines.append(stripped)

    if len(fenced_lines) >= 2:
        return fenced_lines

    # Fall back to raw pipe-delimited lines
    pipe_lines = [
        line.strip() for line in lines
        if "|" in line.strip() and line.strip().startswith("|")
    ]

    return pipe_lines if len(pipe_lines) >= 2 else []
