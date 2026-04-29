"""File I/O utilities for reading and writing parse results."""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    """Read a text file with UTF-8 encoding."""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Write a text file with UTF-8 encoding, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def resolve_output_path(input_path: Path, output_path: Path | None, suffix: str) -> Path:
    """Determine output file path from input path and optional override."""
    if output_path is not None:
        return output_path
    return input_path.with_suffix(suffix)
