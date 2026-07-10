"""Tests for CompositeAssessment contracts — multi-domain gate decisions."""

from __future__ import annotations

import json

import pytest

from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.composite import (
    COMPOSITE_ASSESSMENT_SCHEMA_VERSION,
    CompositeAssessment,
    CompositeGateDecision,
    DomainDecision,
)
from verdx.contracts.dimensions import CoverageStatus

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _domain_decision(**overrides):
    defaults = {
        "domain": "training",
        "required": True,
        "threshold": 80,
        "score": 85,
        "coverage_status": CoverageStatus.FULL,
        "confidence": AssessmentConfidence.STRONG,
        "passed": True,
    }
    return DomainDecision(**(defaults | overrides))


def _gate_decision(**overrides):
    defaults = {
        "strategy": "all-pass",
        "passed": True,
        "score": 85,
        "required_threshold": 80,
    }
    return CompositeGateDecision(**(defaults | overrides))


def _composite(**overrides):
    defaults = {
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "target_content_hash": "sha256:abc",
        "timestamp": "2026-05-21T12:00:00Z",
        "decision": _gate_decision(),
    }
    return CompositeAssessment(**(defaults | overrides))


# ===========================================================================
# DomainDecision
# ===========================================================================


class TestDomainDecision:
    def test_roundtrip(self):
        original = _domain_decision()
        rebuilt = DomainDecision.from_dict(original.to_dict())
        assert rebuilt.domain == "training"
        assert rebuilt.passed is True
        assert rebuilt.score == 85

    def test_with_reason(self):
        dd = _domain_decision(reason="below threshold", passed=False)
        d = dd.to_dict()
        assert d["reason"] == "below threshold"
        rebuilt = DomainDecision.from_dict(d)
        assert rebuilt.reason == "below threshold"

    def test_no_reason_excluded_from_dict(self):
        dd = _domain_decision()
        d = dd.to_dict()
        assert "reason" not in d

    def test_json_serializable(self):
        json.dumps(_domain_decision().to_dict())


# ===========================================================================
# CompositeGateDecision
# ===========================================================================


class TestCompositeGateDecision:
    def test_roundtrip(self):
        original = _gate_decision()
        rebuilt = CompositeGateDecision.from_dict(original.to_dict())
        assert rebuilt.strategy == "all-pass"
        assert rebuilt.passed is True

    def test_with_failed_domains(self):
        gd = _gate_decision(
            passed=False,
            failed_domains=("training", "editing"),
            reason="training below threshold",
        )
        rebuilt = CompositeGateDecision.from_dict(gd.to_dict())
        assert rebuilt.failed_domains == ("training", "editing")
        assert rebuilt.reason == "training below threshold"

    def test_requires_human(self):
        gd = _gate_decision(requires_human=True)
        rebuilt = CompositeGateDecision.from_dict(gd.to_dict())
        assert rebuilt.requires_human is True

    def test_defaults(self):
        gd = CompositeGateDecision()
        assert gd.strategy == "all-pass"
        assert gd.passed is False
        assert gd.score == 0
        assert gd.required_threshold == 80
        assert gd.failed_domains == ()
        assert gd.requires_human is False


# ===========================================================================
# CompositeAssessment
# ===========================================================================


class TestCompositeAssessment:
    def test_roundtrip(self):
        original = _composite(
            domain_decisions=(_domain_decision(),),
            warnings=("md-lint version unknown",),
        )
        d = original.to_dict()
        rebuilt = CompositeAssessment.from_dict(d)
        assert rebuilt.run_id == "run-001"
        assert len(rebuilt.domain_decisions) == 1
        assert rebuilt.warnings == ("md-lint version unknown",)

    def test_schema_version(self):
        c = _composite()
        assert c.schema_version == COMPOSITE_ASSESSMENT_SCHEMA_VERSION

    def test_empty_run_id_rejected(self):
        with pytest.raises(ValueError, match="run_id must be non-empty"):
            _composite(run_id="")

    def test_empty_target_key_rejected(self):
        with pytest.raises(ValueError, match="target_key must be non-empty"):
            _composite(target_key="")

    def test_json_serializable(self):
        c = _composite(domain_decisions=(_domain_decision(),))
        json.dumps(c.to_dict())

    def test_frozen(self):
        c = _composite()
        with pytest.raises(AttributeError):
            c.run_id = "other"  # type: ignore[misc]
