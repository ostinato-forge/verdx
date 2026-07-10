"""WS1: the single verdict gate, shared by the porcelain layer and the CLI.

A finding-free high score is not enough to pass: coverage must not be degraded
and confidence must not be low (F4 -- the two surfaces must agree).
"""

from __future__ import annotations

from tests.contracts.test_models import _make_run_assessment, _make_score
from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus
from verdx.engine.validation.gate import passes_gate


def test_passes_with_good_score_coverage_and_confidence() -> None:
    a = _make_run_assessment(
        score=_make_score(overall=90),
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.STRONG,
    )
    assert passes_gate(a, 80) is True


def test_blocks_on_low_score() -> None:
    a = _make_run_assessment(
        score=_make_score(overall=70),
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.STRONG,
    )
    assert passes_gate(a, 80) is False


def test_blocks_on_degraded_coverage_despite_good_score() -> None:
    a = _make_run_assessment(
        score=_make_score(overall=95),
        coverage_status=CoverageStatus.DEGRADED,
        confidence=AssessmentConfidence.STRONG,
    )
    assert passes_gate(a, 80) is False


def test_blocks_on_low_confidence_despite_good_score() -> None:
    a = _make_run_assessment(
        score=_make_score(overall=95),
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.LOW,
    )
    assert passes_gate(a, 80) is False


def test_partial_coverage_still_passes() -> None:
    a = _make_run_assessment(
        score=_make_score(overall=90),
        coverage_status=CoverageStatus.PARTIAL,
        confidence=AssessmentConfidence.GUARDED,
    )
    assert passes_gate(a, 80) is True
