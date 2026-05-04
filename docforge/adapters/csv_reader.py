"""CSV parser adapter — CSV bytes to Markdown table conversion.

Ported from ai-platform's csv_parser.py, adapted to DocForge conventions:
frozen dataclasses, pure functions, logging module.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MAX_ROWS_DEFAULT = 10000
_SNIFF_SAMPLE_SIZE = 8192


@dataclass(frozen=True)
class CsvParseResult:
    """CSV 파싱 결과."""

    markdown: str
    metadata: dict
    stats: dict


def parse_csv_bytes(
    data: bytes,
    filename: str = "",
    max_rows: int = _MAX_ROWS_DEFAULT,
) -> CsvParseResult:
    """CSV 바이트 데이터를 Markdown 테이블로 변환한다.

    Parameters
    ----------
    data:
        UTF-8 인코딩된 CSV 바이트.
    filename:
        원본 파일명 (마크다운 헤더용).
    max_rows:
        최대 데이터 행 수. 초과 시 잘림 경고 추가.

    Returns
    -------
    CsvParseResult:
        markdown, metadata, stats를 포함하는 불변 결과.
    """
    t0 = time.time()

    text = data.decode("utf-8", errors="replace")

    dialect = _detect_dialect(text)
    reader = csv.reader(io.StringIO(text), dialect=dialect)

    rows: list[list[str]] = []
    truncated = False
    for i, row in enumerate(reader):
        if i > max_rows:  # header(0) + data(1..max_rows)
            truncated = True
            break
        rows.append(row)

    elapsed_ms = (time.time() - t0) * 1000

    if not rows:
        logger.info("csv_parsed: empty file, filename=%s", filename)
        return CsvParseResult(
            markdown="",
            metadata={"filename": filename, "rows": 0, "cols": 0, "truncated": False},
            stats={"parse_time_ms": round(elapsed_ms, 2)},
        )

    markdown = _rows_to_markdown_table(rows, filename)
    data_rows = len(rows) - 1  # exclude header

    if truncated:
        markdown += (
            f"\n\n> CSV 데이터가 {max_rows}행에서 잘렸습니다."
            " 원본은 더 많은 데이터를 포함합니다.\n"
        )

    col_count = len(rows[0]) if rows else 0

    logger.info(
        "csv_parsed: filename=%s, rows=%d, cols=%d, truncated=%s, latency_ms=%.1f",
        filename,
        data_rows,
        col_count,
        truncated,
        elapsed_ms,
    )

    return CsvParseResult(
        markdown=markdown,
        metadata={
            "filename": filename,
            "rows": data_rows,
            "cols": col_count,
            "truncated": truncated,
        },
        stats={"parse_time_ms": round(elapsed_ms, 2)},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_dialect(text: str) -> csv.Dialect:
    """CSV 구분자를 자동 감지한다."""
    try:
        sample = text[:_SNIFF_SAMPLE_SIZE]
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel  # type: ignore[return-value]


def _rows_to_markdown_table(rows: list[list[str]], filename: str = "") -> str:
    """행 리스트를 Markdown 테이블 문자열로 변환한다."""
    if not rows:
        return ""

    header = rows[0]
    data_rows = rows[1:]
    col_count = len(header)

    # 열 수 정규화
    normalized: list[list[str]] = []
    for row in data_rows:
        if len(row) < col_count:
            row = row + [""] * (col_count - len(row))
        elif len(row) > col_count:
            row = row[:col_count]
        normalized.append(row)

    parts: list[str] = []
    if filename:
        parts.append(f"## {filename}\n")

    header_cells = [_escape_pipe(h) for h in header]
    parts.append("| " + " | ".join(header_cells) + " |")
    parts.append("| " + " | ".join(["---"] * col_count) + " |")

    for row in normalized:
        cells = [_escape_pipe(c) for c in row]
        parts.append("| " + " | ".join(cells) + " |")

    return "\n".join(parts)


def _escape_pipe(value: str) -> str:
    """마크다운 테이블 셀에서 파이프 문자를 이스케이프한다."""
    return value.replace("|", "\\|").replace("\n", " ").strip()
