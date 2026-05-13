#!/usr/bin/env python3
"""Benchmark pipeline for DocForge PDF parser.

Measures Character Error Rate (CER) and table F1 score against
reference files to enable quantitative regression detection.

Usage:
    python scripts/benchmark.py --pdf-dir samples/ --ref-dir samples/ref/ [--output report.json]

Reference file conventions:
    - Text reference:  {ref_dir}/{stem}.txt  (UTF-8 plain text)
    - Table reference: {ref_dir}/{stem}_tables.json (list of 2D string arrays)

Missing reference files are skipped with a warning.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CER (Character Error Rate)
# ---------------------------------------------------------------------------


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    Pure Python implementation -- no external dependencies.
    Uses O(min(m, n)) space via two-row optimization.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


def compute_cer(hypothesis: str, reference: str) -> float:
    """Compute Character Error Rate.

    CER = levenshtein_distance(hyp, ref) / len(ref)

    Returns 0.0 if both strings are empty.
    Returns 1.0 if reference is empty but hypothesis is not.
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0
    dist = levenshtein_distance(hypothesis, reference)
    return dist / len(reference)


# ---------------------------------------------------------------------------
# Table F1
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _normalize_cell(text: str) -> str:
    """Normalize a cell value for comparison: strip + collapse whitespace."""
    return _WS_RE.sub(" ", text.strip())


def compute_table_f1(
    hyp_cells: list[list[str]],
    ref_cells: list[list[str]],
) -> dict[str, float]:
    """Compute precision, recall, F1 on cell-level exact match.

    Each table is a list of rows, each row a list of cell strings.
    Cells are compared after whitespace normalization.

    Returns:
        {"precision": float, "recall": float, "f1": float}
    """
    hyp_set = {
        (r, c, _normalize_cell(cell))
        for r, row in enumerate(hyp_cells)
        for c, cell in enumerate(row)
        if _normalize_cell(cell)
    }
    ref_set = {
        (r, c, _normalize_cell(cell))
        for r, row in enumerate(ref_cells)
        for c, cell in enumerate(row)
        if _normalize_cell(cell)
    }

    if not ref_set and not hyp_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    tp = len(hyp_set & ref_set)
    precision = tp / len(hyp_set) if hyp_set else 0.0
    recall = tp / len(ref_set) if ref_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize text for CER comparison: collapse whitespace."""
    return _WS_RE.sub(" ", text.strip())


def run_benchmark(pdf_dir: Path, ref_dir: Path) -> dict:
    """Run benchmark on all PDFs in pdf_dir against references in ref_dir.

    Returns a JSON-serializable report dict.
    """
    # Lazy import -- parse_pdf depends on heavy modules; keep script
    # importable for unit tests without triggering full init.
    from docforge.usecases.parse_pdf import parse_pdf

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in %s", pdf_dir)
        return {"files": [], "aggregate": {"mean_cer": None, "mean_table_f1": None}}

    results = []
    for pdf_path in pdf_files:
        stem = pdf_path.stem
        entry: dict[str, object] = {"name": pdf_path.name}

        # Parse the PDF
        try:
            parse_result = parse_pdf(pdf_path)
            parsed_text = parse_result.markdown
        except Exception as exc:
            logger.error("Failed to parse %s: %s", pdf_path.name, exc)
            entry["error"] = str(exc)
            results.append(entry)
            continue

        # CER against text reference
        ref_txt = ref_dir / f"{stem}.txt"
        if ref_txt.exists():
            reference_text = ref_txt.read_text(encoding="utf-8")
            cer = compute_cer(
                _normalize_text(parsed_text),
                _normalize_text(reference_text),
            )
            entry["cer"] = round(cer, 4)
        else:
            logger.warning("No text reference for %s, skipping CER", pdf_path.name)
            entry["cer"] = None

        # Table F1 against table reference
        ref_tables = ref_dir / f"{stem}_tables.json"
        if ref_tables.exists():
            try:
                ref_table_data = json.loads(ref_tables.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.error("Invalid JSON in %s: %s", ref_tables, exc)
                entry["table_f1"] = None
            else:
                # Build hypothesis table cells from parsed pages
                hyp_table_data: list[list[str]] = []
                for page in parse_result.pages:
                    for table in page.tables:
                        row_map: dict[int, list[str]] = {}
                        for cell in table.cells:
                            if cell.row not in row_map:
                                row_map[cell.row] = []
                            row_map[cell.row].append(cell.text)
                        for row_idx in sorted(row_map):
                            hyp_table_data.append(row_map[row_idx])

                entry["table_f1"] = compute_table_f1(hyp_table_data, ref_table_data)
        else:
            logger.warning("No table reference for %s, skipping table F1", pdf_path.name)
            entry["table_f1"] = None

        results.append(entry)

    # Aggregate
    cers = [r["cer"] for r in results if r.get("cer") is not None]
    f1s = [
        r["table_f1"]["f1"]
        for r in results
        if isinstance(r.get("table_f1"), dict) and r["table_f1"].get("f1") is not None
    ]

    aggregate = {
        "mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
        "mean_table_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
    }

    return {"files": results, "aggregate": aggregate}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="DocForge benchmark: CER and table F1 evaluation",
    )
    parser.add_argument(
        "--pdf-dir", type=Path, required=True,
        help="Directory containing PDF files to parse",
    )
    parser.add_argument(
        "--ref-dir", type=Path, required=True,
        help="Directory containing reference text/table files",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output path for JSON report (default: stdout)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.pdf_dir.is_dir():
        logger.error("PDF directory does not exist: %s", args.pdf_dir)
        return 1
    if not args.ref_dir.is_dir():
        logger.error("Reference directory does not exist: %s", args.ref_dir)
        return 1

    report = run_benchmark(args.pdf_dir, args.ref_dir)
    report_json = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        args.output.write_text(report_json, encoding="utf-8")
        logger.info("Report written to %s", args.output)
    else:
        print(report_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
