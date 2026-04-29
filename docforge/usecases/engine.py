"""Document parsing engine — routes files to the appropriate parser by extension/MIME.

Adopts the dispatcher pattern from ai-platform's engine.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from docforge.domain.models import ParseResult

logger = logging.getLogger(__name__)

_EXTENSION_MAP: dict[str, str] = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".csv": "csv",
}


class DocumentEngine:
    """Document parsing entry point — dispatches to format-specific parsers."""

    def parse(self, path: Path) -> ParseResult:
        """Parse a document file and return structured result."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        ext = path.suffix.lower()
        format_key = _EXTENSION_MAP.get(ext)

        if format_key is None:
            raise ValueError(
                f"Unsupported file format: {ext}. "
                f"Supported: {', '.join(sorted(_EXTENSION_MAP.keys()))}"
            )

        parser_fn = _get_parser(format_key)
        return parser_fn(path)

    def supported_extensions(self) -> list[str]:
        return sorted(_EXTENSION_MAP.keys())


def _get_parser(format_key: str) -> Any:
    """Lazy-load the parser for the given format."""
    if format_key == "pdf":
        from docforge.usecases.parse_pdf import parse_pdf
        return parse_pdf

    raise ValueError(
        f"Parser for '{format_key}' is not yet implemented. "
        f"Currently supported: pdf"
    )
