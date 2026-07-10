"""Tests for core data contracts.

Proves the Phase 1 acceptance criteria:
- All models required by the design spec exist.
- Models round-trip through JSON.
- ObservationDraft cannot contain identity or escalation fields.
- Observation always contains identity and escalation fields.
"""

from __future__ import annotations

import json
import typing

import pytest

from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    CoverageGap,
    DimensionScore,
    EscalationSummary,
    ImprovementSummary,
    RecoveryItem,
    RunAssessment,
    Score,
    ScoreComparison,
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
from verdx.contracts.disposition import (
    DISPOSITION_SCHEMA_VERSION,
    Disposition,
)
from verdx.contracts.independence import IndependenceRecord
from verdx.contracts.observation import (
    OBSERVATION_SCHEMA_VERSION,
    AppliedDisposition,
    Evidence,
    Observation,
    ObservationDraft,
)

# ---------------------------------------------------------------------------
# Fixtures — reusable model instances
# ---------------------------------------------------------------------------


def _make_evidence(**overrides):
    defaults = {
        "source": "yaml-lint",
        "kind": "syntax-error",
        "details": "Invalid YAML at line 42",
        "data": {"line": 42, "column": 1},
    }
    return Evidence(**(defaults | overrides))


def _make_independence(**overrides):
    defaults = {
        "producer_role": "content-author",
        "validator_role": "qa-validator",
        "actor_kind": "agent",
        "relation": "independent",
        "policy_id": "verdx-v1",
        "result": "satisfied",
    }
    return IndependenceRecord(**(defaults | overrides))


def _make_draft(**overrides):
    defaults = {
        "target_key": "sample/ch04/ge01",
        "location": "exercise:ge01/step:3",
        "rule_id": "yaml-syntax",
        "category": "correctness",
        "severity": Severity.HIGH,
        "confidence": Confidence.CONFIRMED,
        "dimensions": {"correctness": DimLevel.HIGH},
        "summary": "YAML syntax error in guided exercise step 3",
    }
    return ObservationDraft(**(defaults | overrides))


def _make_observation(**overrides):
    defaults = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "observation_id": "obs-001",
        "issue_fingerprint": "fp-abc123",
        "target_key": "sample/ch04/ge01",
        "location": "exercise:ge01/step:3",
        "rule_id": "yaml-syntax",
        "producer_role": "content-author",
        "producer_id": None,
        "validator_id": "yaml-lint",
        "validator_version": "1.0.0",
        "independence": _make_independence(),
        "category": "correctness",
        "severity": Severity.HIGH,
        "confidence": Confidence.CONFIRMED,
        "dimensions": {"correctness": DimLevel.HIGH},
        "escalation": EscalationLevel.L3,
        "summary": "YAML syntax error in guided exercise step 3",
    }
    return Observation(**(defaults | overrides))


def _make_disposition(**overrides):
    defaults = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "issue_fingerprint": "fp-abc123",
        "decision": "accepted-risk",
        "reason": "Known limitation, documented workaround exists",
        "team": "training-content",
        "timestamp": "2026-04-30T12:00:00Z",
    }
    return Disposition(**(defaults | overrides))


def _make_applied_disposition(**overrides):
    defaults = {
        "decision": "accepted-risk",
        "reason": "Known limitation",
        "team": "training-content",
        "timestamp": "2026-04-30T12:00:00Z",
        "expires": None,
        "effect": "capped",
        "original_escalation": EscalationLevel.L3,
        "final_escalation": EscalationLevel.L1,
    }
    return AppliedDisposition(**(defaults | overrides))


def _make_validator_status(**overrides):
    defaults = {
        "validator_id": "yaml-lint",
        "validator_version": "1.0.0",
        "status": "completed",
        "checks_run": 10,
        "checks_passed": 8,
        "observation_count": 2,
        "duration_ms": 150,
    }
    return ValidatorStatus(**(defaults | overrides))


def _make_coverage_gap(**overrides):
    defaults = {
        "validator_id": "md-lint",
        "reason": "md-lint crashed on content parse",
        "impact": "Playbook structure not validated",
        "dimensions_affected": ("correctness", "automation"),
    }
    return CoverageGap(**(defaults | overrides))


def _make_recovery_item(**overrides):
    defaults = {
        "id": "recovery-001",
        "kind": "retryable",
        "description": "md-lint timed out, can retry",
        "validator_id": "md-lint",
        "context": {"timeout_ms": 30000},
    }
    return RecoveryItem(**(defaults | overrides))


def _make_improvement_summary(**overrides):
    defaults = {
        "resolved_count": 2,
        "new_count": 1,
        "persistent_count": 3,
        "resolved_fingerprints": ("fp-old-1", "fp-old-2"),
        "new_fingerprints": ("fp-new-1",),
        "persistent_fingerprints": ("fp-persist-1", "fp-persist-2", "fp-persist-3"),
        "trend": Trend.IMPROVING,
    }
    return ImprovementSummary(**(defaults | overrides))


def _make_score(**overrides):
    defaults = {
        "overall": 72,
        "dimension_scores": {
            "correctness": DimensionScore(dimension="correctness", score=65, observation_count=2),
        },
        "observation_count": 2,
        "escalation_distribution": {EscalationLevel.L3: 1, EscalationLevel.L1: 1},
        "coverage_penalty": 10,
    }
    return Score(**(defaults | overrides))


def _make_escalation_summary(**overrides):
    defaults = {
        "counts": {EscalationLevel.L3: 1, EscalationLevel.L1: 1},
        "highest": EscalationLevel.L3,
    }
    return EscalationSummary(**(defaults | overrides))


def _make_run_assessment(**overrides):
    defaults = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "target_content_hash": "sha256:abc123",
        "timestamp": "2026-04-30T12:00:00Z",
        "coverage_status": CoverageStatus.FULL,
        "validator_statuses": (_make_validator_status(),),
        "coverage_gaps": (),
        "checks_run": 10,
        "checks_passed": 8,
        "validators_clean": 0,
        "observations": (_make_observation(),),
        "recovery_items": (),
        "confidence": AssessmentConfidence.GUARDED,
        "score": _make_score(),
        "escalation_summary": _make_escalation_summary(),
    }
    return RunAssessment(**(defaults | overrides))


# ===========================================================================
# Evidence
# ===========================================================================


class TestEvidence:
    def test_construction(self):
        e = _make_evidence()
        assert e.source == "yaml-lint"
        assert e.kind == "syntax-error"
        assert e.truncated is False
        assert e.original_size is None

    def test_frozen(self):
        e = _make_evidence()
        with pytest.raises(AttributeError):
            e.source = "other"  # type: ignore[misc]

    def test_truncated_evidence(self):
        e = _make_evidence(truncated=True, original_size=50000)
        assert e.truncated is True
        assert e.original_size == 50000

    def test_round_trip(self):
        e = _make_evidence(truncated=True, original_size=50000)
        d = e.to_dict()
        restored = Evidence.from_dict(d)
        assert restored == e

    def test_json_round_trip(self):
        e = _make_evidence()
        s = json.dumps(e.to_dict())
        restored = Evidence.from_dict(json.loads(s))
        assert restored == e

    def test_to_dict_omits_original_size_when_none(self):
        e = _make_evidence()
        d = e.to_dict()
        assert "original_size" not in d


# ===========================================================================
# IndependenceRecord
# ===========================================================================


class TestIndependenceRecord:
    def test_construction(self):
        ir = _make_independence()
        assert ir.producer_role == "content-author"
        assert ir.result == "satisfied"

    def test_frozen(self):
        ir = _make_independence()
        with pytest.raises(AttributeError):
            ir.result = "unmet"  # type: ignore[misc]

    def test_round_trip(self):
        ir = _make_independence()
        restored = IndependenceRecord.from_dict(ir.to_dict())
        assert restored == ir

    @pytest.mark.parametrize(
        "field,value",
        [
            ("producer_role", ""),
            ("producer_role", "   "),
            ("validator_role", ""),
            ("policy_id", ""),
        ],
    )
    def test_rejects_empty_strings(self, field, value):
        with pytest.raises(ValueError, match=field):
            _make_independence(**{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("actor_kind", "robot"),
            ("relation", "coworker"),
            ("result", "maybe"),
        ],
    )
    def test_rejects_invalid_enum_strings(self, field, value):
        with pytest.raises(ValueError, match=field):
            _make_independence(**{field: value})

    def test_all_valid_actor_kinds(self):
        for kind in ("human", "agent", "system"):
            ir = _make_independence(actor_kind=kind)
            assert ir.actor_kind == kind

    def test_all_valid_relations(self):
        for rel in ("independent", "same-team", "self", "unknown"):
            ir = _make_independence(relation=rel)
            assert ir.relation == rel

    def test_all_valid_results(self):
        for result in ("satisfied", "waived", "unmet"):
            ir = _make_independence(result=result)
            assert ir.result == result


# ===========================================================================
# ObservationDraft
# ===========================================================================


class TestObservationDraft:
    def test_construction(self):
        d = _make_draft()
        assert d.target_key == "sample/ch04/ge01"
        assert d.severity == Severity.HIGH
        assert d.evidence == ()
        assert d.reproduction == ()
        assert d.invalidating_conditions == ()

    def test_frozen(self):
        d = _make_draft()
        with pytest.raises(AttributeError):
            d.summary = "changed"  # type: ignore[misc]

    def test_round_trip(self):
        d = _make_draft(
            evidence=(_make_evidence(),),
            reproduction=("step 1", "step 2"),
            invalidating_conditions=("condition A",),
        )
        restored = ObservationDraft.from_dict(d.to_dict())
        assert restored == d

    def test_json_round_trip(self):
        d = _make_draft()
        s = json.dumps(d.to_dict())
        restored = ObservationDraft.from_dict(json.loads(s))
        assert restored == d

    def test_draft_has_no_identity_fields(self):
        d = _make_draft()
        assert not hasattr(d, "observation_id")
        assert not hasattr(d, "issue_fingerprint")
        assert not hasattr(d, "schema_version")

    def test_draft_has_no_escalation(self):
        d = _make_draft()
        assert not hasattr(d, "escalation")

    def test_draft_has_no_independence(self):
        d = _make_draft()
        assert not hasattr(d, "independence")

    def test_draft_has_no_disposition(self):
        d = _make_draft()
        assert not hasattr(d, "disposition")

    @pytest.mark.parametrize(
        "field,value",
        [
            ("target_key", ""),
            ("rule_id", ""),
            ("category", ""),
            ("summary", ""),
        ],
    )
    def test_rejects_empty_required_strings(self, field, value):
        with pytest.raises(ValueError, match=field):
            _make_draft(**{field: value})

    def test_rejects_empty_dimensions(self):
        with pytest.raises(ValueError, match="dimensions"):
            _make_draft(dimensions={})

    def test_location_may_be_none(self):
        d = _make_draft(location=None)
        assert d.location is None

    def test_enum_serialization_uses_values(self):
        d = _make_draft()
        data = d.to_dict()
        assert data["severity"] == "high"
        assert data["confidence"] == "confirmed"
        assert data["dimensions"]["correctness"] == "HIGH"


# ===========================================================================
# Observation
# ===========================================================================


class TestObservation:
    def test_construction(self):
        o = _make_observation()
        assert o.observation_id == "obs-001"
        assert o.issue_fingerprint == "fp-abc123"
        assert o.escalation == EscalationLevel.L3
        assert o.schema_version == OBSERVATION_SCHEMA_VERSION

    def test_frozen(self):
        o = _make_observation()
        with pytest.raises(AttributeError):
            o.observation_id = "other"  # type: ignore[misc]

    def test_observation_always_has_identity(self):
        o = _make_observation()
        assert hasattr(o, "observation_id")
        assert hasattr(o, "issue_fingerprint")
        assert hasattr(o, "schema_version")

    def test_observation_always_has_escalation(self):
        o = _make_observation()
        assert hasattr(o, "escalation")
        assert isinstance(o.escalation, EscalationLevel)

    def test_observation_always_has_independence(self):
        o = _make_observation()
        assert hasattr(o, "independence")
        assert isinstance(o.independence, IndependenceRecord)

    def test_round_trip(self):
        o = _make_observation(
            evidence=(_make_evidence(),),
            reproduction=("step 1",),
        )
        restored = Observation.from_dict(o.to_dict())
        assert restored == o

    def test_round_trip_with_disposition(self):
        o = _make_observation(disposition=_make_applied_disposition())
        restored = Observation.from_dict(o.to_dict())
        assert restored == o
        assert restored.disposition is not None
        assert restored.disposition.effect == "capped"

    def test_json_round_trip(self):
        o = _make_observation()
        s = json.dumps(o.to_dict())
        restored = Observation.from_dict(json.loads(s))
        assert restored == o

    def test_rejects_wrong_schema_version(self):
        with pytest.raises(ValueError, match="schema_version"):
            _make_observation(schema_version="wrong-version")

    def test_rejects_empty_observation_id(self):
        with pytest.raises(ValueError, match="observation_id"):
            _make_observation(observation_id="")

    def test_rejects_empty_issue_fingerprint(self):
        with pytest.raises(ValueError, match="issue_fingerprint"):
            _make_observation(issue_fingerprint="")

    def test_to_dict_omits_disposition_when_none(self):
        o = _make_observation()
        d = o.to_dict()
        assert "disposition" not in d


# ===========================================================================
# AppliedDisposition
# ===========================================================================


class TestAppliedDisposition:
    def test_construction(self):
        ad = _make_applied_disposition()
        assert ad.decision == "accepted-risk"
        assert ad.effect == "capped"
        assert ad.original_escalation == EscalationLevel.L3
        assert ad.final_escalation == EscalationLevel.L1

    def test_frozen(self):
        ad = _make_applied_disposition()
        with pytest.raises(AttributeError):
            ad.decision = "other"  # type: ignore[misc]

    def test_round_trip(self):
        ad = _make_applied_disposition(expires="2026-12-31T23:59:59Z")
        restored = AppliedDisposition.from_dict(ad.to_dict())
        assert restored == ad

    def test_to_dict_omits_expires_when_none(self):
        ad = _make_applied_disposition()
        d = ad.to_dict()
        assert "expires" not in d


# ===========================================================================
# Disposition
# ===========================================================================


class TestDisposition:
    def test_construction(self):
        d = _make_disposition()
        assert d.decision == "accepted-risk"
        assert d.team == "training-content"
        assert d.expires is None

    def test_frozen(self):
        d = _make_disposition()
        with pytest.raises(AttributeError):
            d.decision = "other"  # type: ignore[misc]

    def test_round_trip(self):
        d = _make_disposition(expires="2026-12-31T23:59:59Z")
        restored = Disposition.from_dict(d.to_dict())
        assert restored == d

    def test_json_round_trip(self):
        d = _make_disposition()
        s = json.dumps(d.to_dict())
        restored = Disposition.from_dict(json.loads(s))
        assert restored == d

    def test_rejects_wrong_schema_version(self):
        with pytest.raises(ValueError, match="schema_version"):
            _make_disposition(schema_version="wrong")

    def test_rejects_empty_fingerprint(self):
        with pytest.raises(ValueError, match="issue_fingerprint"):
            _make_disposition(issue_fingerprint="")

    def test_rejects_invalid_decision(self):
        with pytest.raises(ValueError, match="decision"):
            _make_disposition(decision="yolo")

    def test_rejects_empty_reason(self):
        with pytest.raises(ValueError, match="reason"):
            _make_disposition(reason="")

    def test_rejects_empty_team(self):
        with pytest.raises(ValueError, match="team"):
            _make_disposition(team="")

    def test_rejects_empty_timestamp(self):
        with pytest.raises(ValueError, match="timestamp"):
            _make_disposition(timestamp="")

    @pytest.mark.parametrize(
        "decision",
        ["accepted-risk", "false-positive", "deferred", "context-dependent"],
    )
    def test_all_valid_decisions(self, decision):
        d = _make_disposition(decision=decision)
        assert d.decision == decision

    def test_to_dict_omits_expires_when_none(self):
        d = _make_disposition()
        dd = d.to_dict()
        assert "expires" not in dd


# ===========================================================================
# Enum round-trips
# ===========================================================================


class TestEnumRoundTrips:
    @pytest.mark.parametrize("member", Severity)
    def test_severity(self, member):
        assert Severity(member.value) is member

    @pytest.mark.parametrize("member", Confidence)
    def test_confidence(self, member):
        assert Confidence(member.value) is member

    @pytest.mark.parametrize("member", DimLevel)
    def test_dim_level(self, member):
        assert DimLevel(member.value) is member

    @pytest.mark.parametrize("member", EscalationLevel)
    def test_escalation_level(self, member):
        assert EscalationLevel(member.value) is member

    @pytest.mark.parametrize("member", CoverageStatus)
    def test_coverage_status(self, member):
        assert CoverageStatus(member.value) is member

    @pytest.mark.parametrize("member", AssessmentConfidence)
    def test_assessment_confidence(self, member):
        assert AssessmentConfidence(member.value) is member

    @pytest.mark.parametrize("member", Trend)
    def test_trend(self, member):
        assert Trend(member.value) is member


# ===========================================================================
# ValidatorStatus
# ===========================================================================


class TestValidatorStatus:
    def test_construction(self):
        vs = _make_validator_status()
        assert vs.validator_id == "yaml-lint"
        assert vs.status == "completed"
        assert vs.checks_run == 10
        assert vs.error is None

    def test_frozen(self):
        vs = _make_validator_status()
        with pytest.raises(AttributeError):
            vs.status = "failed"  # type: ignore[misc]

    def test_failed_with_error(self):
        vs = _make_validator_status(status="failed", error="crash in parser", checks_passed=0)
        assert vs.error == "crash in parser"

    def test_skipped_with_reason(self):
        vs = _make_validator_status(
            status="skipped",
            skip_reason="not applicable",
            checks_run=0,
            checks_passed=0,
            observation_count=0,
            duration_ms=0,
        )
        assert vs.skip_reason == "not applicable"

    def test_round_trip(self):
        vs = _make_validator_status(status="failed", error="timeout after 30s")
        restored = ValidatorStatus.from_dict(vs.to_dict())
        assert restored == vs

    def test_rejects_empty_validator_id(self):
        with pytest.raises(ValueError, match="validator_id"):
            _make_validator_status(validator_id="")

    def test_rejects_empty_version(self):
        with pytest.raises(ValueError, match="validator_version"):
            _make_validator_status(validator_version="")

    def test_rejects_invalid_status(self):
        with pytest.raises(ValueError, match="status"):
            _make_validator_status(status="exploded")

    def test_rejects_negative_checks_run(self):
        with pytest.raises(ValueError, match="checks_run"):
            _make_validator_status(checks_run=-1)

    def test_rejects_negative_checks_passed(self):
        with pytest.raises(ValueError, match="checks_passed"):
            _make_validator_status(checks_passed=-1, checks_run=0)

    def test_rejects_passed_exceeding_run(self):
        with pytest.raises(ValueError, match="checks_passed cannot exceed"):
            _make_validator_status(checks_run=5, checks_passed=10)

    def test_rejects_negative_observation_count(self):
        with pytest.raises(ValueError, match="observation_count"):
            _make_validator_status(observation_count=-1)

    def test_rejects_negative_duration(self):
        with pytest.raises(ValueError, match="duration_ms"):
            _make_validator_status(duration_ms=-1)

    def test_to_dict_omits_error_when_none(self):
        vs = _make_validator_status()
        d = vs.to_dict()
        assert "error" not in d
        assert "skip_reason" not in d


# ===========================================================================
# CoverageGap
# ===========================================================================


class TestCoverageGap:
    def test_construction(self):
        cg = _make_coverage_gap()
        assert cg.validator_id == "md-lint"
        assert cg.dimensions_affected == ("correctness", "automation")

    def test_frozen(self):
        cg = _make_coverage_gap()
        with pytest.raises(AttributeError):
            cg.reason = "other"  # type: ignore[misc]

    def test_round_trip(self):
        cg = _make_coverage_gap()
        restored = CoverageGap.from_dict(cg.to_dict())
        assert restored == cg

    def test_dimensions_serialized_as_list(self):
        cg = _make_coverage_gap()
        d = cg.to_dict()
        assert isinstance(d["dimensions_affected"], list)

    def test_rejects_empty_validator_id(self):
        with pytest.raises(ValueError, match="validator_id"):
            _make_coverage_gap(validator_id="")

    def test_rejects_empty_reason(self):
        with pytest.raises(ValueError, match="reason"):
            _make_coverage_gap(reason="")

    def test_rejects_empty_impact(self):
        with pytest.raises(ValueError, match="impact"):
            _make_coverage_gap(impact="")


# ===========================================================================
# RecoveryItem
# ===========================================================================


class TestRecoveryItem:
    def test_construction(self):
        ri = _make_recovery_item()
        assert ri.kind == "retryable"
        assert ri.context == {"timeout_ms": 30000}

    def test_frozen(self):
        ri = _make_recovery_item()
        with pytest.raises(AttributeError):
            ri.kind = "blocked"  # type: ignore[misc]

    def test_round_trip(self):
        ri = _make_recovery_item()
        restored = RecoveryItem.from_dict(ri.to_dict())
        assert restored == ri

    @pytest.mark.parametrize("kind", ["blocked", "retryable", "cleanup-needed"])
    def test_all_valid_kinds(self, kind):
        ri = _make_recovery_item(kind=kind)
        assert ri.kind == kind

    def test_rejects_invalid_kind(self):
        with pytest.raises(ValueError, match="kind"):
            _make_recovery_item(kind="exploded")

    def test_rejects_empty_id(self):
        with pytest.raises(ValueError, match="id"):
            _make_recovery_item(id="")

    def test_rejects_empty_description(self):
        with pytest.raises(ValueError, match="description"):
            _make_recovery_item(description="")

    def test_rejects_empty_validator_id(self):
        with pytest.raises(ValueError, match="validator_id"):
            _make_recovery_item(validator_id="")


# ===========================================================================
# ImprovementSummary
# ===========================================================================


class TestImprovementSummary:
    def test_construction(self):
        imp = _make_improvement_summary()
        assert imp.resolved_count == 2
        assert imp.trend == Trend.IMPROVING

    def test_frozen(self):
        imp = _make_improvement_summary()
        with pytest.raises(AttributeError):
            imp.trend = Trend.STABLE  # type: ignore[misc]

    def test_round_trip(self):
        imp = _make_improvement_summary()
        restored = ImprovementSummary.from_dict(imp.to_dict())
        assert restored == imp

    def test_rejects_negative_counts(self):
        with pytest.raises(ValueError, match="resolved_count"):
            _make_improvement_summary(resolved_count=-1)
        with pytest.raises(ValueError, match="new_count"):
            _make_improvement_summary(new_count=-1)
        with pytest.raises(ValueError, match="persistent_count"):
            _make_improvement_summary(persistent_count=-1)

    def test_fingerprints_serialized_as_list(self):
        imp = _make_improvement_summary()
        d = imp.to_dict()
        assert isinstance(d["resolved_fingerprints"], list)
        assert isinstance(d["new_fingerprints"], list)
        assert isinstance(d["persistent_fingerprints"], list)


# ===========================================================================
# Score
# ===========================================================================


class TestScore:
    def test_construction(self):
        s = _make_score()
        assert s.overall == 72
        assert s.coverage_penalty == 10
        assert s.comparison is None

    def test_frozen(self):
        s = _make_score()
        with pytest.raises(AttributeError):
            s.overall = 50  # type: ignore[misc]

    def test_round_trip(self):
        s = _make_score(comparison=ScoreComparison(previous_score=80, delta=-8))
        restored = Score.from_dict(s.to_dict())
        assert restored == s

    def test_json_round_trip(self):
        s = _make_score()
        text = json.dumps(s.to_dict())
        restored = Score.from_dict(json.loads(text))
        assert restored == s

    def test_rejects_score_below_zero(self):
        with pytest.raises(ValueError, match="overall"):
            _make_score(overall=-1)

    def test_rejects_score_above_100(self):
        with pytest.raises(ValueError, match="overall"):
            _make_score(overall=101)

    def test_rejects_negative_coverage_penalty(self):
        with pytest.raises(ValueError, match="coverage_penalty"):
            _make_score(coverage_penalty=-1)

    def test_escalation_distribution_uses_enum_keys(self):
        s = _make_score()
        d = s.to_dict()
        assert all(isinstance(k, str) for k in d["escalation_distribution"])
        assert "L3" in d["escalation_distribution"]


# ===========================================================================
# EscalationSummary
# ===========================================================================


class TestEscalationSummary:
    def test_construction(self):
        es = _make_escalation_summary()
        assert es.highest == EscalationLevel.L3

    def test_round_trip(self):
        es = _make_escalation_summary()
        restored = EscalationSummary.from_dict(es.to_dict())
        assert restored == es


# ===========================================================================
# RunAssessment
# ===========================================================================


class TestRunAssessment:
    def test_construction(self):
        ra = _make_run_assessment()
        assert ra.run_id == "run-001"
        assert ra.coverage_status == CoverageStatus.FULL
        assert ra.confidence == AssessmentConfidence.GUARDED
        assert ra.improvement is None

    def test_frozen(self):
        ra = _make_run_assessment()
        with pytest.raises(AttributeError):
            ra.run_id = "other"  # type: ignore[misc]

    def test_round_trip(self):
        ra = _make_run_assessment(
            improvement=_make_improvement_summary(),
            coverage_gaps=(_make_coverage_gap(),),
            recovery_items=(_make_recovery_item(),),
        )
        restored = RunAssessment.from_dict(ra.to_dict())
        assert restored == ra

    def test_json_round_trip(self):
        ra = _make_run_assessment()
        s = json.dumps(ra.to_dict())
        restored = RunAssessment.from_dict(json.loads(s))
        assert restored == ra

    def test_rejects_wrong_schema_version(self):
        with pytest.raises(ValueError, match="schema_version"):
            _make_run_assessment(schema_version="wrong")

    def test_rejects_empty_run_id(self):
        with pytest.raises(ValueError, match="run_id"):
            _make_run_assessment(run_id="")

    def test_rejects_empty_target_key(self):
        with pytest.raises(ValueError, match="target_key"):
            _make_run_assessment(target_key="")

    def test_rejects_empty_content_hash(self):
        with pytest.raises(ValueError, match="target_content_hash"):
            _make_run_assessment(target_content_hash="")

    def test_rejects_empty_timestamp(self):
        with pytest.raises(ValueError, match="timestamp"):
            _make_run_assessment(timestamp="")

    def test_rejects_negative_checks_run(self):
        with pytest.raises(ValueError, match="checks_run"):
            _make_run_assessment(checks_run=-1)

    def test_rejects_negative_checks_passed(self):
        with pytest.raises(ValueError, match="checks_passed"):
            _make_run_assessment(checks_passed=-1)

    def test_rejects_negative_validators_clean(self):
        with pytest.raises(ValueError, match="validators_clean"):
            _make_run_assessment(validators_clean=-1)

    def test_assessment_schema_version_constant(self):
        assert ASSESSMENT_SCHEMA_VERSION == "verdx.assessment.v1"

    def test_to_dict_omits_improvement_when_none(self):
        ra = _make_run_assessment()
        d = ra.to_dict()
        assert "improvement" not in d


# ===========================================================================
# Draft/Observation boundary — structural proof
# ===========================================================================


class TestDraftObservationBoundary:
    """Structural proof that the canonicalization boundary is enforced."""

    DRAFT_FIELDS: typing.ClassVar[set[str]] = {
        f.name for f in ObservationDraft.__dataclass_fields__.values()
    }
    OBS_FIELDS: typing.ClassVar[set[str]] = {
        f.name for f in Observation.__dataclass_fields__.values()
    }

    def test_draft_lacks_observation_id(self):
        assert "observation_id" not in self.DRAFT_FIELDS

    def test_draft_lacks_issue_fingerprint(self):
        assert "issue_fingerprint" not in self.DRAFT_FIELDS

    def test_draft_lacks_schema_version(self):
        assert "schema_version" not in self.DRAFT_FIELDS

    def test_draft_lacks_escalation(self):
        assert "escalation" not in self.DRAFT_FIELDS

    def test_draft_lacks_independence(self):
        assert "independence" not in self.DRAFT_FIELDS

    def test_draft_lacks_disposition(self):
        assert "disposition" not in self.DRAFT_FIELDS

    def test_observation_has_observation_id(self):
        assert "observation_id" in self.OBS_FIELDS

    def test_observation_has_issue_fingerprint(self):
        assert "issue_fingerprint" in self.OBS_FIELDS

    def test_observation_has_schema_version(self):
        assert "schema_version" in self.OBS_FIELDS

    def test_observation_has_escalation(self):
        assert "escalation" in self.OBS_FIELDS

    def test_observation_has_independence(self):
        assert "independence" in self.OBS_FIELDS

    def test_observation_has_disposition(self):
        assert "disposition" in self.OBS_FIELDS

    def test_draft_content_fields_subset_of_observation(self):
        """Every content field on a draft also exists on Observation."""
        assert self.DRAFT_FIELDS.issubset(self.OBS_FIELDS)

    def test_observation_has_more_fields_than_draft(self):
        """Observation has identity, provenance, and escalation that drafts lack."""
        identity_fields = self.OBS_FIELDS - self.DRAFT_FIELDS
        expected = {
            "schema_version",
            "observation_id",
            "issue_fingerprint",
            "producer_role",
            "producer_id",
            "validator_id",
            "validator_version",
            "independence",
            "escalation",
            "disposition",
        }
        assert identity_fields == expected


# ===========================================================================
# Unknown-field tolerance
# ===========================================================================


class TestUnknownFieldTolerance:
    """Readers should tolerate extra fields for forward-compatibility."""

    def test_evidence_ignores_unknown_fields(self):
        data = _make_evidence().to_dict()
        data["future_field"] = "future_value"
        e = Evidence.from_dict(data)
        assert e.source == "yaml-lint"

    def test_draft_ignores_unknown_fields(self):
        data = _make_draft().to_dict()
        data["future_field"] = "future_value"
        d = ObservationDraft.from_dict(data)
        assert d.target_key == "sample/ch04/ge01"

    def test_observation_ignores_unknown_fields(self):
        data = _make_observation().to_dict()
        data["future_field"] = "future_value"
        o = Observation.from_dict(data)
        assert o.observation_id == "obs-001"

    def test_disposition_ignores_unknown_fields(self):
        data = _make_disposition().to_dict()
        data["future_field"] = "future_value"
        d = Disposition.from_dict(data)
        assert d.decision == "accepted-risk"

    def test_validator_status_ignores_unknown_fields(self):
        data = _make_validator_status().to_dict()
        data["future_field"] = "future_value"
        vs = ValidatorStatus.from_dict(data)
        assert vs.validator_id == "yaml-lint"

    def test_run_assessment_ignores_unknown_fields(self):
        data = _make_run_assessment().to_dict()
        data["future_field"] = "future_value"
        ra = RunAssessment.from_dict(data)
        assert ra.run_id == "run-001"
