"""Smoke tests for the benchmark pipeline script.

Tests the core metric functions (levenshtein, CER, table F1) and the
CLI --help flag. Does NOT require actual PDF parsing infrastructure.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


class TestLevenshteinDistance:
    """Pure-function tests for levenshtein_distance."""

    def test_identical_strings(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("hello", "hello") == 0

    def test_empty_both(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("", "") == 0

    def test_one_empty(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("abc", "") == 3
        assert levenshtein_distance("", "abc") == 3

    def test_substitution(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("kitten", "sitting") == 3

    def test_insertion(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("abc", "abcd") == 1

    def test_deletion(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("abcd", "abc") == 1

    def test_korean_characters(self) -> None:
        from scripts.benchmark import levenshtein_distance
        assert levenshtein_distance("가나다", "가나라") == 1


class TestComputeCER:
    """CER computation tests."""

    def test_perfect_match(self) -> None:
        from scripts.benchmark import compute_cer
        assert compute_cer("hello world", "hello world") == 0.0

    def test_completely_wrong(self) -> None:
        from scripts.benchmark import compute_cer
        cer = compute_cer("xyz", "abc")
        assert cer == pytest.approx(1.0)

    def test_empty_reference(self) -> None:
        from scripts.benchmark import compute_cer
        assert compute_cer("", "") == 0.0
        assert compute_cer("something", "") == 1.0

    def test_partial_errors(self) -> None:
        from scripts.benchmark import compute_cer
        # "abcd" vs "abce": 1 substitution, len(ref)=4 -> CER=0.25
        cer = compute_cer("abcd", "abce")
        assert cer == pytest.approx(0.25)


class TestComputeTableF1:
    """Table F1 computation tests."""

    def test_perfect_match(self) -> None:
        from scripts.benchmark import compute_table_f1
        table = [["a", "b"], ["c", "d"]]
        result = compute_table_f1(table, table)
        assert result["f1"] == pytest.approx(1.0)
        assert result["precision"] == pytest.approx(1.0)
        assert result["recall"] == pytest.approx(1.0)

    def test_no_overlap(self) -> None:
        from scripts.benchmark import compute_table_f1
        hyp = [["a", "b"]]
        ref = [["c", "d"]]
        result = compute_table_f1(hyp, ref)
        assert result["f1"] == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        from scripts.benchmark import compute_table_f1
        hyp = [["a", "b"], ["c", "x"]]
        ref = [["a", "b"], ["c", "d"]]
        result = compute_table_f1(hyp, ref)
        # 3 out of 4 match: precision=3/4=0.75, recall=3/4=0.75
        assert result["precision"] == pytest.approx(0.75)
        assert result["recall"] == pytest.approx(0.75)

    def test_both_empty(self) -> None:
        from scripts.benchmark import compute_table_f1
        result = compute_table_f1([], [])
        assert result["f1"] == pytest.approx(1.0)

    def test_whitespace_normalization(self) -> None:
        from scripts.benchmark import compute_table_f1
        hyp = [["  hello   world  "]]
        ref = [["hello world"]]
        result = compute_table_f1(hyp, ref)
        assert result["f1"] == pytest.approx(1.0)


class TestBenchmarkCLI:
    """CLI smoke test."""

    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.benchmark", "--help"],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0
        assert "CER" in result.stdout or "benchmark" in result.stdout.lower()
