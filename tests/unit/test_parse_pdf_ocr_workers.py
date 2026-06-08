"""OCR 세마포어 permit 해석 회귀 테스트.

버그: config.max_ocr_workers 기본값 0이 Semaphore(0)으로 직결돼 OCR이 필요한
첫 SCANNED 페이지에서 acquire가 영구 블록(데드락)됐다. 0 = "auto"는 양수
기본값으로 해석돼야 한다.
"""
from __future__ import annotations

import threading

from docforge.usecases.parse_pdf import _resolve_ocr_workers


def test_zero_resolves_to_positive_not_deadlock_semaphore() -> None:
    """0(=auto)은 절대 0이 되면 안 된다 — Semaphore(0)은 첫 OCR에서 데드락."""
    workers = _resolve_ocr_workers(0)
    assert workers >= 1
    # 해석된 값으로 만든 세마포어는 즉시 acquire 가능해야 한다(블록 없음).
    sem = threading.Semaphore(workers)
    assert sem.acquire(blocking=False) is True
    sem.release()


def test_negative_also_resolves_positive() -> None:
    assert _resolve_ocr_workers(-1) >= 1


def test_explicit_positive_is_respected() -> None:
    assert _resolve_ocr_workers(3) == 3
    assert _resolve_ocr_workers(1) == 1
