"""Resource path utilities — handles both development and PyInstaller frozen modes."""

from __future__ import annotations

import sys
from pathlib import Path


def get_base_dir() -> Path:
    """Return the base directory of the application."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent.parent


def get_static_dir() -> Path:
    return get_base_dir() / "web" / "static"


def get_template_dir() -> Path:
    return get_base_dir() / "web" / "templates"


def get_upload_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path.cwd()
    upload_dir = base / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def get_model_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "models"
    return Path.cwd() / "models"
