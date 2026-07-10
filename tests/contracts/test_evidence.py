"""Tests for GateEvidence contracts — agent-facing assessment projection."""

from __future__ import annotations

import json

from verdx.contracts.assessment import AssessmentConfidence, CoverageGap
from verdx.contracts.dimensions import CoverageStatus
from verdx.contracts.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    ActionItem,
    AwarenessItem,
    EvidenceSummary,
    EvidenceTruncation,
    GateEvidence,
    RecoveryProjection,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _evidence_summary(**overrides):
    defaults = {
        "checks_run": 10,
        "checks_passed": 8,
        "validators_run": 3,
        "validators_clean": 2,
        "by_escalation": {"L3": 1, "L1": 1},
        "blocking_findings": 1,
        "awareness_items": 1,
    }
    return EvidenceSummary(**(defaults | overrides))


def _action_item(**overrides):
    defaults = {
        "rule": "yaml-syntax",
        "severity": "high",
        "escalation": "L3",
        "summary": "Invalid YAML in step 3",
        "location": "exercise:ge01/step:3",
        "count": 1,
    }
    return ActionItem(**(defaults | overrides))


def _awareness_item(**overrides):
    defaults = {
        "rule": "prose-readability",
        "severity": "low",
        "escalation": "L1",
        "count": 2,
        "example": "Sentence complexity score 14.2 in paragraph 3",
    }
    return AwarenessItem(**(defaults | overrides))


def _truncation(**overrides):
    defaults = {
        "truncated": True,
        "estimated_tokens": 750,
        "hard_max_tokens": 1000,
        "omitted_action_groups": 2,
    }
    return EvidenceTruncation(**(defaults | overrides))


def _gate_evidence(**overrides):
    defaults = {
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "score": 72,
        "threshold": 80,
        "passed": False,
        "summary": _evidence_summary(),
        "action_required": (_action_item(),),
        "awareness": (_awareness_item(),),
    }
    return GateEvidence(**(defaults | overrides))


# ===========================================================================
# EvidenceSummary
# ===========================================================================


class TestEvidenceSummary:
    def test_roundtrip(self):
        original = _evidence_summary(domains=("training",))
        rebuilt = EvidenceSummary.from_dict(original.to_dict())
        assert rebuilt.checks_run == 10
        assert rebuilt.validators_clean == 2
        assert rebuilt.domains == ("training",)

    def test_json_serializable(self):
        s = _evidence_summary()
        json.dumps(s.to_dict())


# ===========================================================================
# ActionItem
# ===========================================================================


class TestActionItem:
    def test_roundtrip(self):
        original = _action_item()
        rebuilt = ActionItem.from_dict(original.to_dict())
        assert rebuilt.rule == "yaml-syntax"
        assert rebuilt.location == "exercise:ge01/step:3"

    def test_no_location(self):
        item = _action_item(location=None)
        d = item.to_dict()
        assert "location" not in d
        rebuilt = ActionItem.from_dict(d)
        assert rebuilt.location is None


# ===========================================================================
# AwarenessItem
# ===========================================================================


class TestAwarenessItem:
    def test_roundtrip(self):
        original = _awareness_item()
        rebuilt = AwarenessItem.from_dict(original.to_dict())
        assert rebuilt.rule == "prose-readability"
        assert rebuilt.count == 2


# ===========================================================================
# EvidenceTruncation
# ===========================================================================


class TestEvidenceTruncation:
    def test_roundtrip(self):
        original = _truncation()
        rebuilt = EvidenceTruncation.from_dict(original.to_dict())
        assert rebuilt.truncated is True
        assert rebuilt.omitted_action_groups == 2

    def test_defaults(self):
        t = EvidenceTruncation(truncated=False, estimated_tokens=200)
        assert t.hard_max_tokens == 1000
        assert t.omitted_observations == 0


# ===========================================================================
# GateEvidence
# ===========================================================================


class TestGateEvidence:
    def test_roundtrip(self):
        original = _gate_evidence()
        d = original.to_dict()
        rebuilt = GateEvidence.from_dict(d)
        assert rebuilt.run_id == "run-001"
        assert rebuilt.score == 72
        assert rebuilt.passed is False
        assert rebuilt.coverage_ok is True
        assert rebuilt.confidence_ok is True
        assert len(rebuilt.action_required) == 1
        assert len(rebuilt.awareness) == 1

    def test_schema_version(self):
        ev = _gate_evidence()
        assert ev.schema_version == EVIDENCE_SCHEMA_VERSION

    def test_json_serializable(self):
        ev = _gate_evidence(truncation=_truncation())
        json.dumps(ev.to_dict())

    def test_gate_fields_roundtrip(self):
        ev = _gate_evidence(passed=True, coverage_ok=False, confidence_ok=False)
        d = ev.to_dict()
        assert d["passed"] is True
        assert d["coverage_ok"] is False
        assert d["confidence_ok"] is False

        rebuilt = GateEvidence.from_dict(d)
        assert rebuilt.passed is True
        assert rebuilt.coverage_ok is False
        assert rebuilt.confidence_ok is False

    def test_gate_fields_default_for_legacy_payload(self):
        d = _gate_evidence().to_dict()
        del d["coverage_ok"]
        del d["confidence_ok"]

        rebuilt = GateEvidence.from_dict(d)
        assert rebuilt.coverage_ok is True
        assert rebuilt.confidence_ok is True

    def test_with_truncation_roundtrip(self):
        ev = _gate_evidence(truncation=_truncation())
        rebuilt = GateEvidence.from_dict(ev.to_dict())
        assert rebuilt.truncation is not None
        assert rebuilt.truncation.truncated is True

    def test_without_truncation(self):
        ev = _gate_evidence()
        assert ev.truncation is None
        d = ev.to_dict()
        assert "truncation" not in d

    def test_with_recovery_items(self):
        ri = RecoveryProjection(
            id="recovery-001",
            kind="retryable",
            severity="degraded",
            description="timed out",
            validator_id="md-lint",
        )
        ev = _gate_evidence(recovery=(ri,))
        rebuilt = GateEvidence.from_dict(ev.to_dict())
        assert len(rebuilt.recovery) == 1

    def test_with_coverage_gaps(self):
        gap = CoverageGap(
            validator_id="md-lint",
            reason="crashed",
            impact="playbooks not validated",
            dimensions_affected=("correctness",),
        )
        ev = _gate_evidence(coverage_gaps=(gap,))
        rebuilt = GateEvidence.from_dict(ev.to_dict())
        assert len(rebuilt.coverage_gaps) == 1

    def test_defaults(self):
        ev = GateEvidence(run_id="r1", target_key="t1")
        assert ev.score == 0
        assert ev.threshold == 80
        assert ev.coverage_ok is True
        assert ev.confidence_ok is True
        assert ev.coverage == CoverageStatus.FULL
        assert ev.confidence == AssessmentConfidence.STRONG
        assert ev.drill_down_available is True
