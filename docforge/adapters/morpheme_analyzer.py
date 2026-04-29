"""Morpheme analyzer adapters — Kiwi and Null implementations.

KiwiMorphemeAnalyzer wraps kiwipiepy for Korean morpheme analysis.
NullMorphemeAnalyzer is a no-op fallback when Kiwi is unavailable.
"""

from __future__ import annotations

import logging

from docforge.domain.ports import MorphemeToken

logger = logging.getLogger(__name__)


class KiwiMorphemeAnalyzer:
    """Morpheme analyzer backed by kiwipiepy (Kiwi).

    Creates a single Kiwi instance on init and reuses it for all tokenize calls.
    """

    def __init__(self) -> None:
        self._tokenize_error_logged = False
        try:
            from kiwipiepy import Kiwi  # type: ignore[import-untyped]

            self._kiwi = Kiwi()
            self._available = True
            logger.info("KiwiMorphemeAnalyzer initialized successfully")
        except ImportError:
            self._kiwi = None
            self._available = False
            logger.info("kiwipiepy not installed, KiwiMorphemeAnalyzer unavailable")
        except Exception:
            self._kiwi = None
            self._available = False
            logger.warning(
                "Kiwi initialization failed", exc_info=True,
            )

    def tokenize(self, text: str) -> list[MorphemeToken]:
        """Tokenize text using Kiwi and return MorphemeToken list."""
        if not self._available or self._kiwi is None:
            return []

        try:
            tokens = self._kiwi.tokenize(text)
            return [
                MorphemeToken(
                    form=token.form,
                    tag=token.tag,
                    start=token.start,
                    length=token.len,
                )
                for token in tokens
            ]
        except Exception:
            if not self._tokenize_error_logged:
                logger.warning("Kiwi tokenize failed for text: %r", text[:50], exc_info=True)
                self._tokenize_error_logged = True
            return []

    def is_available(self) -> bool:
        """Return True if Kiwi is installed and ready."""
        return self._available


class NullMorphemeAnalyzer:
    """No-op morpheme analyzer for graceful degradation.

    Always returns empty results and reports as unavailable.
    """

    def tokenize(self, text: str) -> list[MorphemeToken]:
        """Return empty list (no analysis)."""
        return []

    def is_available(self) -> bool:
        """Always returns False."""
        return False
