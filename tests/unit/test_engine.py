"""Tests for DocumentEngine."""

import pytest
from pathlib import Path

from docforge.usecases.engine import DocumentEngine


class TestDocumentEngine:
    """Test document format dispatcher."""

    def test_supported_extensions(self) -> None:
        engine = DocumentEngine()
        exts = engine.supported_extensions()
        assert ".pdf" in exts

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        engine = DocumentEngine()
        fake_file = tmp_path / "test.xyz"
        fake_file.write_text("data")
        with pytest.raises(ValueError, match="Unsupported"):
            engine.parse(fake_file)

    def test_file_not_found_raises(self) -> None:
        engine = DocumentEngine()
        with pytest.raises(FileNotFoundError):
            engine.parse(Path("/nonexistent/file.pdf"))

    def test_unimplemented_format_raises(self, tmp_path: Path) -> None:
        engine = DocumentEngine()
        fake_html = tmp_path / "test.html"
        fake_html.write_text("<html></html>")
        with pytest.raises(ValueError, match="not yet implemented"):
            engine.parse(fake_html)
