"""CLI entry point for DocForge PDF parsing engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docforge.infrastructure.config import ParserConfig
from docforge.infrastructure.file_io import resolve_output_path, write_text
from docforge.usecases.parse_pdf import parse_pdf
from docforge.usecases.verify_result import generate_verification_report


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        prog="docforge",
        description="DocForge - PDF to Markdown parsing engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m docforge input.pdf
  python -m docforge input.pdf -o output.md
  python -m docforge input.pdf --verify
  python -m docforge input.pdf --stats
  python -m docforge input.pdf --force-ocr
        """,
    )

    parser.add_argument("pdf", help="Input PDF file path")
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("--verify", action="store_true", help="Generate verification HTML report")
    parser.add_argument("--stats", action="store_true", help="Print parsing statistics")
    parser.add_argument("--force-ocr", action="store_true", help="Force OCR mode")
    parser.add_argument("--dpi", type=int, default=200, help="DPI for OCR/rendering (default: 200)")
    parser.add_argument(
        "--format", choices=["markdown", "chunks"], default="markdown",
        help="Output format: markdown (default) or chunks (RAG JSONL)",
    )
    parser.add_argument(
        "--strategy",
        choices=["by_page", "by_title", "semantic", "fixed"],
        default="by_title",
        help="Chunking strategy when --format=chunks (default: by_title)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=500,
        help="Max tokens per chunk (default: 500)",
    )
    parser.add_argument(
        "--header-ratio", type=float, default=0.08,
        help="Header area ratio (default: 0.08)",
    )
    parser.add_argument(
        "--footer-ratio", type=float, default=0.08,
        help="Footer area ratio (default: 0.08)",
    )
    parser.add_argument(
        "--extract-images", action="store_true",
        help="Extract embedded images and emit ![caption](path) markdown",
    )
    parser.add_argument(
        "--image-dir", default="images",
        help="Directory (relative or absolute) for extracted image files",
    )
    parser.add_argument(
        "--enable-layout", action="store_true",
        help="Enable Surya layout detection (requires surya-ocr installed)",
    )

    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        return 1

    config = ParserConfig(
        header_ratio=args.header_ratio,
        footer_ratio=args.footer_ratio,
        dpi=args.dpi,
        layout_detection_enabled=args.enable_layout,
        image_extraction_enabled=args.extract_images,
        image_output_dir=args.image_dir if args.extract_images else None,
    )


    try:
        result = parse_pdf(pdf_path, config=config, force_ocr=args.force_ocr)
    except Exception as exc:
        print(f"Error during parsing: {exc}", file=sys.stderr)
        return 1

    # Save output (markdown or chunks)
    if args.format == "chunks":
        from docforge.usecases.chunking import chunk_document
        from docforge.usecases.chunking.serializer import chunks_to_jsonl

        chunks = chunk_document(
            result, strategy=args.strategy, max_tokens=args.max_tokens,
        )
        output_path = resolve_output_path(
            pdf_path, Path(args.output) if args.output else None, ".jsonl",
        )
        write_text(output_path, chunks_to_jsonl(chunks))
        print(f"\nChunks ({args.strategy}, {len(chunks)}) saved: {output_path}")
    else:
        output_path = resolve_output_path(
            pdf_path, Path(args.output) if args.output else None, ".md",
        )
        write_text(output_path, result.markdown)
        print(f"\nMarkdown saved: {output_path}")

        if args.extract_images:
            img_root = Path(args.image_dir)
            if not img_root.is_absolute():
                img_root = output_path.parent / args.image_dir
            img_root.mkdir(parents=True, exist_ok=True)
            count = 0
            for page in result.pages:
                for image in page.images:
                    ext = "jpg" if image.format == "jpeg" else image.format
                    fname = f"page-{image.page_num}-img-{image.block_id}.{ext}"
                    (img_root / fname).write_bytes(image.data)
                    count += 1
            print(f"Images saved: {count} -> {img_root}")

    # Verification report
    if args.verify:
        try:
            report_path = generate_verification_report(pdf_path, result, config=config)
            print(f"Verification report: {report_path}")
        except Exception as exc:
            print(f"Warning: Verification report generation failed: {exc}", file=sys.stderr)

    # Statistics
    if args.stats:
        stats = result.stats
        noise = stats.noise_removed
        stats_dict = {
            "total_pages": stats.total_pages,
            "parsed_pages": stats.parsed_pages,
            "tables_found": stats.tables_found,
            "tables_need_review": stats.tables_need_review,
            "text_blocks": stats.text_blocks,
            "heading_count": stats.heading_count,
            "empty_line_ratio": stats.empty_line_ratio,
            "avg_line_length": stats.avg_line_length,
            "parse_time_ms": stats.parse_time_ms,
            "noise_removed": {
                "headers": noise.headers,
                "footers": noise.footers,
                "page_numbers": noise.page_numbers,
                "toc_pages": noise.toc_pages,
                "watermarks": noise.watermarks,
            },
            "document_profile": {
                "complexity": result.profile.complexity.value,
                "recommended_parser": result.profile.recommended_parser,
                "total_chars": result.profile.total_chars,
                "image_area_ratio": result.profile.image_area_ratio,
            },
        }
        print("\n--- Parsing Statistics ---")
        print(json.dumps(stats_dict, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
