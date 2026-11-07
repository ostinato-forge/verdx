"""WS1: translate_assessment is a pure view of the engine assessment.

Suppression and scoring happen in the engine. translate shows every finding,
marks the ones a team dispositioned, carries the engine's score unchanged, and
computes the verdict with the shared gate. No re-suppression, no recompute.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.contracts.test_models import (
    _make_applied_disposition,
    _make_coverage_gap,
    _make_observation,
    _make_run_assessment,
    _make_score,
)
from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus
from verdx.translate import translate_assessment


def test_dispositioned_findings_shown_marked_with_engine_score_carried() -> None:
    clean = _make_observation(issue_fingerprint="fp-clean")
    suppressed = _make_observation(
        issue_fingerprint="fp-supp", disposition=_make_applied_disposition()
    )
    engine = _make_run_assessment(
        observations=(clean, suppressed),
        score=_make_score(overall=85),
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.STRONG,
    )

    result = translate_assessment(engine, threshold=80, profile="standard")

    # Both findings are shown; the dispositioned one is marked, not hidden, so a
    # block always has a visible reason.
    by_sig = {o.signature: o for o in result.observations}
    assert set(by_sig) == {"fp-clean", "fp-supp"}
    assert by_sig["fp-clean"].disposition is None
    assert by_sig["fp-supp"].disposition == "accepted-risk"
    assert result.score == 85  # engine score carried as-is, no recompute
    assert result.verdict == "pass"


def test_verdict_blocks_on_degraded_coverage_despite_good_score() -> None:
    engine = _make_run_assessment(
        observations=(),
        score=_make_score(overall=95),
        coverage_status=CoverageStatus.DEGRADED,
        confidence=AssessmentConfidence.STRONG,
    )

    result = translate_assessment(engine, threshold=80, profile="standard")

    assert result.verdict == "block"


def test_verdict_blocks_on_low_confidence_despite_good_score() -> None:
    engine = _make_run_assessment(
        observations=(),
        score=_make_score(overall=95),
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.LOW,
    )

    result = translate_assessment(engine, threshold=80, profile="standard")

    assert result.verdict == "block"


def test_gate_reasons_surface_when_blocked_on_coverage() -> None:
    engine = _make_run_assessment(
        observations=(),
        score=_make_score(overall=95),
        coverage_status=CoverageStatus.DEGRADED,
        confidence=AssessmentConfidence.LOW,
        coverage_gaps=(_make_coverage_gap(),),
    )

    result = translate_assessment(engine, threshold=80, profile="standard")

    # A coverage/confidence block carries a visible reason, even with no findings.
    assert result.verdict == "block"
    gate = result.metadata["gate"]
    assert gate["coverage_status"] == "degraded"
    assert gate["confidence"] == "low"
    assert len(gate["coverage_gaps"]) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"coverage_status": CoverageStatus.DEGRADED},
        {"confidence": AssessmentConfidence.LOW},
    ],
)
def test_opposite_verdicts_have_different_hashes(changed) -> None:
    engine = _make_run_assessment(
        observations=(),
        score=_make_score(overall=95),
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.STRONG,
    )
    before = translate_assessment(engine, threshold=80, profile="standard")
    after = translate_assessment(replace(engine, **changed), threshold=80, profile="standard")
    assert before.verdict == "pass"
    assert after.verdict == "block"
    assert before.decision_hash != after.decision_hash


def test_threshold_and_checker_version_affect_hash_but_timing_does_not() -> None:
    engine = _make_run_assessment()
    before = translate_assessment(engine, threshold=80, profile="standard")
    stricter = translate_assessment(engine, threshold=99, profile="standard")
    assert before.decision_hash != stricter.decision_hash
    status = engine.validator_statuses[0]
    changed = replace(engine, validator_statuses=(replace(status, validator_version="99"),))
    assert translate_assessment(changed, threshold=80, profile="standard").decision_hash != (
        before.decision_hash
    )
    retimed = replace(
        engine,
        run_id="later",
        timestamp="later",
        validator_statuses=tuple(
            replace(item, duration_ms=item.duration_ms + 100) for item in engine.validator_statuses
        ),
    )
    assert translate_assessment(retimed, threshold=80, profile="standard").decision_hash == (
        before.decision_hash
    )
