"""Property-based tests for evidence schema round-trip stability.

Proves that serialize → deserialize → re-serialize produces identical output.
"""

from __future__ import annotations

import json

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings
from hypothesis import strategies as st

from verdx.contracts.assessment import (
    AssessmentConfidence,
    CoverageStatus,
)
from verdx.contracts.evidence import (
    ActionItem,
    AwarenessItem,
    EvidenceSummary,
    GateEvidence,
)

nonempty = st.text(
    alphabet=st.characters(categories=("L", "N")),
    min_size=1,
    max_size=15,
)
escalation_levels = st.sampled_from(["L0", "L1", "L2", "L3", "L4"])
severities = st.sampled_from(["critical", "high", "medium", "low", "info"])


@st.composite
def action_items(draw):
    return ActionItem(
        rule=draw(nonempty),
        severity=draw(severities),
        escalation=draw(escalation_levels),
        summary=draw(nonempty),
        location=draw(st.one_of(st.none(), nonempty)),
        count=draw(st.integers(min_value=1, max_value=100)),
    )


@st.composite
def awareness_items(draw):
    return AwarenessItem(
        rule=draw(nonempty),
        severity=draw(severities),
        escalation=draw(escalation_levels),
        count=draw(st.integers(min_value=1, max_value=50)),
        example=draw(nonempty),
    )


@st.composite
def gate_evidences(draw):
    actions = draw(st.lists(action_items(), max_size=3))
    awarenesses = draw(st.lists(awareness_items(), max_size=3))
    return GateEvidence(
        run_id=draw(nonempty),
        target_key=draw(nonempty),
        domain="testing",
        score=draw(st.integers(min_value=0, max_value=100)),
        threshold=80,
        passed=draw(st.booleans()),
        coverage_ok=draw(st.booleans()),
        confidence_ok=draw(st.booleans()),
        coverage=draw(st.sampled_from(list(CoverageStatus))),
        confidence=draw(st.sampled_from(list(AssessmentConfidence))),
        summary=EvidenceSummary(
            checks_run=draw(st.integers(min_value=0, max_value=50)),
            checks_passed=draw(st.integers(min_value=0, max_value=50)),
            validators_run=draw(st.integers(min_value=0, max_value=10)),
            validators_clean=draw(st.integers(min_value=0, max_value=10)),
            by_escalation={},
            blocking_findings=len(actions),
            awareness_items=len(awarenesses),
        ),
        action_required=tuple(actions),
        awareness=tuple(awarenesses),
        recovery=(),
        coverage_gaps=(),
        truncation=None,
    )


class TestEvidenceRoundTrip:
    @given(evidence=gate_evidences())
    @settings(max_examples=50)
    def test_to_dict_from_dict_round_trip(self, evidence):
        d = evidence.to_dict()
        restored = GateEvidence.from_dict(d)
        assert restored.run_id == evidence.run_id
        assert restored.score == evidence.score
        assert restored.passed == evidence.passed
        assert len(restored.action_required) == len(evidence.action_required)
        assert len(restored.awareness) == len(evidence.awareness)

    @given(evidence=gate_evidences())
    @settings(max_examples=50)
    def test_json_round_trip_stable(self, evidence):
        d1 = evidence.to_dict()
        json1 = json.dumps(d1, sort_keys=True)
        restored = GateEvidence.from_dict(json.loads(json1))
        d2 = restored.to_dict()
        json2 = json.dumps(d2, sort_keys=True)
        assert json1 == json2

    @given(evidence=gate_evidences())
    @settings(max_examples=30)
    def test_schema_version_preserved(self, evidence):
        d = evidence.to_dict()
        assert d["schema_version"] == "verdx.evidence.v1"
        restored = GateEvidence.from_dict(d)
        assert restored.schema_version == "verdx.evidence.v1"
