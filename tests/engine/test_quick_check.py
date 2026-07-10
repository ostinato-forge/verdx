"""Tests for engine-level quick_check() fingerprint comparison."""

from __future__ import annotations

from unittest.mock import patch

from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    EscalationSummary,
    RunAssessment,
    Score,
    Trend,
    ValidatorStatus,
)
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.observation import IndependenceRecord, Observation


def _obs(fingerprint: str, summary: str = "finding") -> Observation:
    return Observation(
        schema_version="verdx.observation.v1",
        observation_id=f"obs-{fingerprint}",
        issue_fingerprint=fingerprint,
        target_key="test-target",
        location="file.yml:1",
        rule_id="test-rule",
        producer_role="content-author",
        producer_id=None,
        validator_id="test-validator",
        validator_version="1.0.0",
        independence=IndependenceRecord(
            producer_role="content-author",
            validator_role="qa-validator",
            actor_kind="agent",
            relation="independent",
            policy_id="verdx-v1",
            result="satisfied",
        ),
        category="correctness",
        severity=Severity.MEDIUM,
        confidence=Confidence.CONFIRMED,
        dimensions={"correctness": DimLevel.MEDIUM},
        escalation=EscalationLevel.L2,
        summary=summary,
    )


def _assessment(*fingerprints: str) -> RunAssessment:
    observations = tuple(_obs(fp) for fp in fingerprints)
    return RunAssessment(
        schema_version=ASSESSMENT_SCHEMA_VERSION,
        run_id="run-test",
        target_key="test-target",
        target_content_hash="sha256:abc",
        timestamp="2026-06-24T00:00:00Z",
        coverage_status=CoverageStatus.FULL,
        validator_statuses=(
            ValidatorStatus(
                validator_id="test-validator",
                validator_version="1.0.0",
                status="completed",
                checks_run=1,
                checks_passed=0,
                observation_count=len(observations),
                duration_ms=10,
            ),
        ),
        coverage_gaps=(),
        checks_run=1,
        checks_passed=0 if observations else 1,
        validators_clean=0 if observations else 1,
        observations=observations,
        recovery_items=(),
        confidence=AssessmentConfidence.GUARDED,
        score=Score(
            overall=80,
            dimension_scores={},
            observation_count=len(observations),
            escalation_distribution={EscalationLevel.L2: len(observations)},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={EscalationLevel.L2: len(observations)},
            highest=EscalationLevel.L2 if observations else EscalationLevel.L0,
        ),
    )


class TestQuickCheckFingerprinting:
    def test_returns_only_new_findings(self) -> None:
        from pathlib import Path

        from verdx.engine.api import quick_check

        baseline = _assessment("fp-a", "fp-b")
        current = _assessment("fp-b", "fp-c")

        with patch("verdx.engine.api.validate", return_value=current):
            result = quick_check(Path("/tmp"), baseline)

        fps = {obs.issue_fingerprint for obs in result.observations}
        assert fps == {"fp-c"}

    def test_improvement_summary_present(self) -> None:
        from pathlib import Path

        from verdx.engine.api import quick_check

        baseline = _assessment("fp-a", "fp-b")
        current = _assessment("fp-b", "fp-c")

        with patch("verdx.engine.api.validate", return_value=current):
            result = quick_check(Path("/tmp"), baseline)

        assert result.improvement is not None
        assert result.improvement.new_count == 1
        assert result.improvement.resolved_count == 1
        assert result.improvement.persistent_count == 1

    def test_no_changes_stable(self) -> None:
        from pathlib import Path

        from verdx.engine.api import quick_check

        baseline = _assessment("fp-a", "fp-b")
        current = _assessment("fp-a", "fp-b")

        with patch("verdx.engine.api.validate", return_value=current):
            result = quick_check(Path("/tmp"), baseline)

        assert result.observations == ()
        assert result.improvement is not None
        assert result.improvement.trend == Trend.STABLE
        assert result.improvement.new_count == 0
        assert result.improvement.resolved_count == 0
        assert result.improvement.persistent_count == 2

    def test_all_resolved_improving(self) -> None:
        from pathlib import Path

        from verdx.engine.api import quick_check

        baseline = _assessment("fp-a", "fp-b")
        current = _assessment()

        with patch("verdx.engine.api.validate", return_value=current):
            result = quick_check(Path("/tmp"), baseline)

        assert result.improvement is not None
        assert result.improvement.trend == Trend.IMPROVING
        assert result.improvement.resolved_count == 2
        assert result.improvement.new_count == 0

    def test_new_findings_only_regressing(self) -> None:
        from pathlib import Path

        from verdx.engine.api import quick_check

        baseline = _assessment()
        current = _assessment("fp-new")

        with patch("verdx.engine.api.validate", return_value=current):
            result = quick_check(Path("/tmp"), baseline)

        assert result.improvement is not None
        assert result.improvement.trend == Trend.REGRESSING
        assert result.improvement.new_count == 1

    def test_more_resolved_than_new_improving(self) -> None:
        from pathlib import Path

        from verdx.engine.api import quick_check

        baseline = _assessment("fp-a", "fp-b", "fp-c")
        current = _assessment("fp-c", "fp-new")

        with patch("verdx.engine.api.validate", return_value=current):
            result = quick_check(Path("/tmp"), baseline)

        assert result.improvement is not None
        assert result.improvement.trend == Trend.IMPROVING
        assert result.improvement.resolved_count == 2
        assert result.improvement.new_count == 1
