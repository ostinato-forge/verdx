"""Tests for RunAssessment assembly — the full pipeline integration test."""

from __future__ import annotations

from tests.contracts.test_models import (
    _make_observation,
    _make_recovery_item,
    _make_validator_status,
)
from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    Trend,
)
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    EscalationLevel,
    Severity,
)
from verdx.contracts.subjects import (
    SUBJECT_SCHEMA_VERSION,
    ExpectedCoverageSubject,
    SubjectCoverageState,
    SubjectSelector,
    ValidationSubject,
)
from verdx.engine.validation.coverage import derive_subject_coverage
from verdx.engine.validation.scoring import assemble_assessment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obs(**overrides):
    return _make_observation(**overrides)


def _vs(**overrides):
    return _make_validator_status(**overrides)


def _assemble(**overrides):
    defaults = {
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "target_content_hash": "sha256:abc123",
        "timestamp": "2026-04-30T12:00:00Z",
        "observations": [],
        "validator_statuses": [_vs()],
        "critical_validator_ids": frozenset(),
        "recovery_items": [],
    }
    return assemble_assessment(**(defaults | overrides))


# ===========================================================================
# Assembly
# ===========================================================================


class TestAssembly:
    def test_basic_assembly(self):
        ra = _assemble()
        assert ra.schema_version == ASSESSMENT_SCHEMA_VERSION
        assert ra.run_id == "run-001"
        assert ra.target_key == "sample/ch04/ge01"

    def test_coverage_status_computed(self):
        ra = _assemble()
        assert ra.coverage_status == CoverageStatus.FULL

    def test_checks_summed_from_statuses(self):
        ra = _assemble(
            validator_statuses=[
                _vs(checks_run=10, checks_passed=8),
                _vs(checks_run=5, checks_passed=5),
            ],
        )
        assert ra.checks_run == 15
        assert ra.checks_passed == 13

    def test_validators_clean_computed(self):
        ra = _assemble(
            validator_statuses=[
                _vs(status="completed", observation_count=0),
                _vs(status="completed", observation_count=3),
            ],
        )
        assert ra.validators_clean == 1

    def test_confidence_computed(self):
        ra = _assemble(observations=[], validator_statuses=[_vs()])
        assert ra.confidence == AssessmentConfidence.STRONG

    def test_confidence_guarded_with_observations(self):
        ra = _assemble(observations=[_obs()])
        assert ra.confidence == AssessmentConfidence.GUARDED

    def test_confidence_low_with_degraded_coverage(self):
        ra = _assemble(
            validator_statuses=[
                _vs(
                    validator_id="critical-v",
                    status="failed",
                    error="crash",
                ),
            ],
            critical_validator_ids=frozenset({"critical-v"}),
        )
        assert ra.confidence == AssessmentConfidence.LOW
        assert ra.coverage_status == CoverageStatus.DEGRADED

    def test_score_computed(self):
        ra = _assemble()
        assert ra.score.overall == 100
        assert ra.score.coverage_penalty == 0

    def test_score_reflects_observations(self):
        ra = _assemble(
            observations=[
                _obs(
                    severity=Severity.HIGH,
                    confidence=Confidence.CONFIRMED,
                ),
            ],
        )
        assert ra.score.overall == 70

    def test_score_reflects_coverage_gaps(self):
        ra = _assemble(
            validator_statuses=[
                _vs(validator_id="opt-v", status="failed", error="crash"),
            ],
        )
        assert ra.score.overall < 100
        assert ra.score.coverage_penalty > 0

    def test_escalation_summary_computed(self):
        ra = _assemble(
            observations=[
                _obs(escalation=EscalationLevel.L3),
                _obs(escalation=EscalationLevel.L1),
            ],
        )
        assert ra.escalation_summary.highest == EscalationLevel.L3
        assert ra.escalation_summary.counts[EscalationLevel.L3] == 1
        assert ra.escalation_summary.counts[EscalationLevel.L1] == 1

    def test_improvement_none_without_previous(self):
        ra = _assemble()
        assert ra.improvement is None

    def test_improvement_computed_with_previous(self):
        ra = _assemble(
            observations=[
                _obs(issue_fingerprint="fp-new"),
            ],
            previous_fingerprints={"fp-old", "fp-new"},
        )
        assert ra.improvement is not None
        assert ra.improvement.resolved_count == 1
        assert ra.improvement.new_count == 0
        assert ra.improvement.persistent_count == 1
        assert ra.improvement.trend == Trend.IMPROVING

    def test_coverage_gaps_auto_generated(self):
        ra = _assemble(
            validator_statuses=[
                _vs(validator_id="lint-v", status="failed", error="timeout"),
            ],
        )
        assert len(ra.coverage_gaps) == 1
        assert ra.coverage_gaps[0].validator_id == "lint-v"

    def test_manual_and_auto_gaps_combined(self):
        from verdx.contracts.assessment import CoverageGap

        manual_gap = CoverageGap(
            validator_id="external",
            reason="external service unreachable",
            impact="API checks not performed",
            dimensions_affected=("integration",),
        )
        ra = _assemble(
            validator_statuses=[
                _vs(validator_id="lint-v", status="failed", error="crash"),
            ],
            coverage_gaps=[manual_gap],
        )
        assert len(ra.coverage_gaps) == 2

    def test_recovery_items_attached(self):
        ri = _make_recovery_item()
        ra = _assemble(recovery_items=[ri])
        assert len(ra.recovery_items) == 1
        assert ra.recovery_items[0].kind == "retryable"

    def test_observations_attached(self):
        ra = _assemble(observations=[_obs()])
        assert len(ra.observations) == 1

    def test_score_comparison_with_previous(self):
        ra = _assemble(previous_score=80)
        assert ra.score.comparison is not None
        assert ra.score.comparison.previous_score == 80
        assert ra.score.comparison.delta == 20

    def test_subject_coverage_attached_and_rolls_up(self):
        subject = ValidationSubject(
            schema_version=SUBJECT_SCHEMA_VERSION,
            subject_key="outline:outline.yml",
            target_key="sample/ch04/ge01",
            kind="outline",
            label="outline.yml",
            selector=SubjectSelector(kind="subject", values=("outline:outline.yml",)),
            location="outline.yml",
        )
        expected = ExpectedCoverageSubject(
            subject_selector=SubjectSelector(kind="subject", values=(subject.subject_key,)),
            required_validator_ids=("training-structure",),
            criticality="critical",
            dimensions_affected=("structural-integrity",),
            reason="outline must be checked",
        )
        records = derive_subject_coverage(
            run_id="run-001",
            target_key="sample/ch04/ge01",
            target_content_hash="sha256:abc123",
            subjects=(subject,),
            expected_subjects=(expected,),
            validator_statuses=(),
            observations=(),
        ).records
        ra = _assemble(subject_coverage=records, validator_statuses=[])
        assert ra.subject_coverage[0].state == SubjectCoverageState.MISSING
        assert ra.coverage_status == CoverageStatus.DEGRADED
        assert ra.score.coverage_penalty == 20


# ===========================================================================
# Determinism
# ===========================================================================


class TestAssemblyDeterminism:
    def test_same_inputs_same_output(self):
        args = {
            "run_id": "run-001",
            "target_key": "sample/ch04/ge01",
            "target_content_hash": "sha256:abc123",
            "timestamp": "2026-04-30T12:00:00Z",
            "observations": [_obs()],
            "validator_statuses": [_vs()],
            "critical_validator_ids": frozenset(),
            "recovery_items": [],
        }
        r1 = assemble_assessment(**args)
        r2 = assemble_assessment(**args)
        assert r1.score.overall == r2.score.overall
        assert r1.confidence == r2.confidence
        assert r1.coverage_status == r2.coverage_status
        assert r1.checks_run == r2.checks_run


# ===========================================================================
# Honesty invariants
# ===========================================================================


class TestHonestyInvariants:
    def test_partial_zero_observations_not_clean(self):
        """A partial run with zero observations is NOT a clean run."""
        ra = _assemble(
            observations=[],
            validator_statuses=[
                _vs(validator_id="opt-v", status="failed", error="crash"),
            ],
        )
        assert ra.coverage_status != CoverageStatus.FULL
        assert ra.confidence != AssessmentConfidence.STRONG
        assert ra.score.overall < 100

    def test_degraded_zero_observations_not_clean(self):
        """Degraded coverage with zero observations is not clean."""
        ra = _assemble(
            observations=[],
            validator_statuses=[
                _vs(
                    validator_id="critical-v",
                    status="failed",
                    error="crash",
                ),
            ],
            critical_validator_ids=frozenset({"critical-v"}),
        )
        assert ra.coverage_status == CoverageStatus.DEGRADED
        assert ra.confidence == AssessmentConfidence.LOW
        assert ra.score.overall < 100

    def test_score_never_100_with_failed_validator(self):
        ra = _assemble(
            observations=[],
            validator_statuses=[
                _vs(validator_id="v", status="failed", error="crash"),
            ],
        )
        assert ra.score.overall < 100

    def test_failed_critical_gives_degraded_low(self):
        """Failed critical validator -> degraded, low confidence."""
        ra = _assemble(
            validator_statuses=[
                _vs(
                    validator_id="critical-v",
                    status="failed",
                    error="crash",
                ),
            ],
            critical_validator_ids=frozenset({"critical-v"}),
        )
        assert ra.coverage_status == CoverageStatus.DEGRADED
        assert ra.confidence == AssessmentConfidence.LOW
