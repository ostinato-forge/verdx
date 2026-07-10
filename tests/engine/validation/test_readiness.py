"""Tests for readiness and promotion records."""

from __future__ import annotations

import pytest

from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus
from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    SUBJECT_SCHEMA_VERSION,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
)
from verdx.engine.validation.readiness import (
    READINESS_SCHEMA_VERSION,
    ReadinessBlocker,
    ReadinessRecord,
    ReadinessState,
    derive_readiness,
)


def _subject(**overrides):
    defaults = {
        "schema_version": SUBJECT_SCHEMA_VERSION,
        "subject_key": "section:ch01/setup/ge",
        "target_key": "target",
        "kind": "section",
        "label": "GE",
        "selector": SubjectSelector(kind="subject", values=("section:ch01/setup/ge",)),
        "location": "content/ch01/setup/ge.adoc",
        "metadata": {"file_path": "content/ch01/setup/ge.adoc"},
    }
    return ValidationSubject(**(defaults | overrides))


def _coverage_record(**overrides):
    defaults = {
        "schema_version": SUBJECT_COVERAGE_SCHEMA_VERSION,
        "run_id": "run-001",
        "target_key": "target",
        "target_content_hash": "sha256:abc",
        "subject": _subject(),
        "state": SubjectCoverageState.COMPLETE_CLEAN,
        "criticality": "critical",
        "required_validator_ids": ("training-structure",),
        "completed_validator_ids": ("training-structure",),
        "failed_validator_ids": (),
        "skipped_validator_ids": (),
        "checks_run": 5,
        "checks_passed": 5,
        "observation_fingerprints": (),
        "coverage_gap_validator_ids": (),
        "blocking_reason": None,
        "model_health": SubjectModelHealth(),
    }
    return SubjectCoverageRecord(**(defaults | overrides))


def _readiness(**overrides):
    defaults = {
        "score": 95,
        "threshold": 80,
        "coverage_status": CoverageStatus.FULL,
        "confidence": AssessmentConfidence.STRONG,
        "gate_passed": True,
        "gate_reason": "score 95 >= threshold 80",
        "subject_coverage": (),
        "target_key": "target",
        "run_id": "run-001",
    }
    return derive_readiness(**(defaults | overrides))


class TestCleanReadiness:
    def test_ready_with_high_score_full_coverage(self):
        record = _readiness()
        assert record.state is ReadinessState.READY
        assert record.score == 95
        assert record.threshold == 80
        assert record.blockers == ()
        assert record.debt_summary == ()
        assert "ready to advance" in record.explanation

    def test_ready_without_threshold(self):
        record = _readiness(threshold=None)
        assert record.state is ReadinessState.READY
        assert record.threshold is None

    def test_ready_with_clean_critical_subjects(self):
        cov = _coverage_record(state=SubjectCoverageState.COMPLETE_CLEAN)
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.READY
        assert record.blockers == ()


class TestBlockedByCriticalDebt:
    def test_critical_subject_missing(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MISSING,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED
        assert any(b.kind == "critical_subject_debt" for b in record.blockers)

    def test_critical_subject_partial(self):
        cov = _coverage_record(
            state=SubjectCoverageState.PARTIAL,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED

    def test_critical_subject_failed(self):
        cov = _coverage_record(
            state=SubjectCoverageState.FAILED,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED

    def test_critical_subject_skipped(self):
        cov = _coverage_record(
            state=SubjectCoverageState.SKIPPED,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED

    def test_critical_subject_unsupported(self):
        cov = _coverage_record(
            state=SubjectCoverageState.UNSUPPORTED,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED

    def test_critical_subject_model_limited(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MODEL_LIMITED,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED

    def test_blocker_includes_subject_keys(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MISSING,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        blocker = next(b for b in record.blockers if b.kind == "critical_subject_debt")
        assert cov.subject.subject_key in blocker.subject_keys

    def test_high_score_does_not_override_critical_debt(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MISSING,
            criticality="critical",
        )
        record = _readiness(score=100, subject_coverage=(cov,))
        assert record.state is ReadinessState.BLOCKED


class TestBlockedByGateFailure:
    def test_gate_failed(self):
        record = _readiness(
            gate_passed=False,
            gate_reason="score 60 < threshold 80",
            score=60,
        )
        assert record.state is ReadinessState.BLOCKED
        assert any(b.kind == "gate_failed" for b in record.blockers)
        assert "Gate did not pass" in record.explanation


class TestBlockedByDegradedCoverage:
    def test_degraded_coverage(self):
        record = _readiness(coverage_status=CoverageStatus.DEGRADED)
        assert record.state is ReadinessState.BLOCKED
        assert any(b.kind == "degraded_coverage" for b in record.blockers)


class TestNeedsReviewLowConfidence:
    def test_low_confidence(self):
        record = _readiness(confidence=AssessmentConfidence.LOW)
        assert record.state is ReadinessState.NEEDS_REVIEW
        assert any(b.kind == "low_confidence" for b in record.blockers)
        assert "low confidence" in record.explanation

    def test_guarded_confidence_does_not_trigger_review(self):
        record = _readiness(confidence=AssessmentConfidence.GUARDED)
        assert record.state is ReadinessState.READY


class TestNeedsReviewPartialCoverage:
    def test_partial_coverage(self):
        record = _readiness(coverage_status=CoverageStatus.PARTIAL)
        assert record.state is ReadinessState.NEEDS_REVIEW


class TestStandardDebtVisible:
    def test_standard_debt_in_summary(self):
        cov = _coverage_record(
            state=SubjectCoverageState.PARTIAL,
            criticality="standard",
            subject=_subject(
                subject_key="section:ch02/lab",
                label="Lab",
                selector=SubjectSelector(kind="subject", values=("section:ch02/lab",)),
            ),
        )
        record = _readiness(subject_coverage=(cov,))
        assert any("standard" in line for line in record.debt_summary)
        assert record.state is ReadinessState.READY

    def test_standard_debt_not_blocking(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MISSING,
            criticality="standard",
            subject=_subject(
                subject_key="section:ch02/lab",
                label="Lab",
                selector=SubjectSelector(kind="subject", values=("section:ch02/lab",)),
            ),
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.READY
        assert len(record.debt_summary) == 1


class TestOptionalDebtVisible:
    def test_optional_debt_in_summary(self):
        cov = _coverage_record(
            state=SubjectCoverageState.SKIPPED,
            criticality="optional",
            subject=_subject(
                subject_key="section:appendix/a",
                label="Appendix A",
                selector=SubjectSelector(kind="subject", values=("section:appendix/a",)),
            ),
        )
        record = _readiness(subject_coverage=(cov,))
        assert any("optional" in line for line in record.debt_summary)
        assert record.state is ReadinessState.READY

    def test_optional_debt_not_blocking(self):
        cov = _coverage_record(
            state=SubjectCoverageState.FAILED,
            criticality="optional",
            subject=_subject(
                subject_key="section:appendix/a",
                label="Appendix A",
                selector=SubjectSelector(kind="subject", values=("section:appendix/a",)),
            ),
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.READY


class TestMultipleBlockers:
    def test_critical_debt_and_gate_failure(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MISSING,
            criticality="critical",
        )
        record = _readiness(
            gate_passed=False,
            gate_reason="score below threshold",
            subject_coverage=(cov,),
        )
        assert record.state is ReadinessState.BLOCKED
        kinds = {b.kind for b in record.blockers}
        assert "critical_subject_debt" in kinds
        assert "gate_failed" in kinds

    def test_degraded_coverage_and_low_confidence(self):
        record = _readiness(
            coverage_status=CoverageStatus.DEGRADED,
            confidence=AssessmentConfidence.LOW,
        )
        assert record.state is ReadinessState.BLOCKED
        kinds = {b.kind for b in record.blockers}
        assert "degraded_coverage" in kinds
        assert "low_confidence" in kinds

    def test_all_blockers_combined(self):
        cov = _coverage_record(
            state=SubjectCoverageState.FAILED,
            criticality="critical",
        )
        record = _readiness(
            score=50,
            gate_passed=False,
            gate_reason="score 50 < threshold 80",
            coverage_status=CoverageStatus.DEGRADED,
            confidence=AssessmentConfidence.LOW,
            subject_coverage=(cov,),
        )
        assert record.state is ReadinessState.BLOCKED
        assert len(record.blockers) == 4


class TestSerialization:
    def test_round_trip(self):
        cov = _coverage_record(
            state=SubjectCoverageState.MISSING,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        data = record.to_dict()
        restored = ReadinessRecord.from_dict(data)
        assert restored == record

    def test_round_trip_ready(self):
        record = _readiness()
        data = record.to_dict()
        restored = ReadinessRecord.from_dict(data)
        assert restored == record

    def test_round_trip_needs_review(self):
        record = _readiness(confidence=AssessmentConfidence.LOW)
        data = record.to_dict()
        restored = ReadinessRecord.from_dict(data)
        assert restored == record

    def test_round_trip_with_debt_summary(self):
        cov = _coverage_record(
            state=SubjectCoverageState.PARTIAL,
            criticality="standard",
            subject=_subject(
                subject_key="section:ch02/lab",
                label="Lab",
                selector=SubjectSelector(kind="subject", values=("section:ch02/lab",)),
            ),
        )
        record = _readiness(subject_coverage=(cov,))
        data = record.to_dict()
        restored = ReadinessRecord.from_dict(data)
        assert restored == record
        assert restored.debt_summary == record.debt_summary

    def test_round_trip_no_threshold(self):
        record = _readiness(threshold=None)
        data = record.to_dict()
        assert "threshold" not in data
        restored = ReadinessRecord.from_dict(data)
        assert restored.threshold is None

    def test_blocker_round_trip(self):
        blocker = ReadinessBlocker(
            kind="critical_subject_debt",
            description="1 critical subject(s) have coverage debt",
            subject_keys=("section:ch01/setup/ge",),
        )
        data = blocker.to_dict()
        restored = ReadinessBlocker.from_dict(data)
        assert restored == blocker

    def test_blocker_without_subject_keys(self):
        blocker = ReadinessBlocker(kind="gate_failed", description="Gate failed")
        data = blocker.to_dict()
        assert "subject_keys" not in data
        restored = ReadinessBlocker.from_dict(data)
        assert restored.subject_keys == ()


class TestSchemaVersion:
    def test_schema_version_set(self):
        record = _readiness()
        assert record.schema_version == READINESS_SCHEMA_VERSION

    def test_invalid_schema_version_rejected(self):
        with pytest.raises(ValueError, match="schema_version"):
            ReadinessRecord(
                schema_version="wrong",
                target_key="target",
                run_id="run-001",
                state=ReadinessState.READY,
                score=95,
                threshold=80,
                coverage_status=CoverageStatus.FULL,
                confidence=AssessmentConfidence.STRONG,
                blockers=(),
                debt_summary=(),
                explanation="ready",
            )


class TestExplanation:
    def test_ready_explanation_includes_score(self):
        record = _readiness(score=92, threshold=80)
        assert "92" in record.explanation
        assert "80" in record.explanation

    def test_ready_with_debt_mentions_debt(self):
        cov = _coverage_record(
            state=SubjectCoverageState.PARTIAL,
            criticality="standard",
            subject=_subject(
                subject_key="section:ch02/lab",
                label="Lab",
                selector=SubjectSelector(kind="subject", values=("section:ch02/lab",)),
            ),
        )
        record = _readiness(subject_coverage=(cov,))
        assert "debt" in record.explanation.lower()

    def test_blocked_explanation_includes_reason(self):
        record = _readiness(
            gate_passed=False,
            gate_reason="score 60 < threshold 80",
        )
        assert "Blocked" in record.explanation


class TestCriticalNotApplicableNotDebt:
    def test_not_applicable_critical_is_not_debt(self):
        cov = _coverage_record(
            state=SubjectCoverageState.NOT_APPLICABLE,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.READY

    def test_complete_with_observations_critical_is_not_debt(self):
        cov = _coverage_record(
            state=SubjectCoverageState.COMPLETE_WITH_OBSERVATIONS,
            criticality="critical",
        )
        record = _readiness(subject_coverage=(cov,))
        assert record.state is ReadinessState.READY
