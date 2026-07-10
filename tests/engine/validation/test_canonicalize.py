"""Tests for canonicalization: the core boundary function."""

from __future__ import annotations

from verdx.contracts.dimensions import (
    Confidence,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION, Disposition
from verdx.contracts.independence import IndependenceRecord
from verdx.contracts.observation import Evidence, ObservationDraft
from verdx.engine.validation.canonicalize import Provenance, canonicalize
from verdx.engine.validation.escalation import (
    EscalationFloor,
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_policy() -> EscalationPolicy:
    return EscalationPolicy(
        version=1,
        floors=(),
        rules=(
            EscalationRule(
                name="blocking",
                match=RuleMatch(
                    quantifier="any",
                    dimension="*",
                    level=DimLevel.BLOCKING,
                ),
                level=EscalationLevel.L4,
            ),
            EscalationRule(
                name="high-risk",
                match=RuleMatch(
                    quantifier="any",
                    dimension="*",
                    level=DimLevel.HIGH,
                ),
                level=EscalationLevel.L3,
            ),
            EscalationRule(
                name="medium-risk",
                match=RuleMatch(
                    quantifier="any",
                    dimension="*",
                    level=DimLevel.MEDIUM,
                ),
                level=EscalationLevel.L1,
            ),
            EscalationRule(
                name="low-risk",
                match=RuleMatch(
                    quantifier="all",
                    dimension="*",
                    level=DimLevel.LOW,
                ),
                level=EscalationLevel.L0,
            ),
        ),
    )


def _policy_with_floor(
    category: str,
    minimum: EscalationLevel,
) -> EscalationPolicy:
    base = _default_policy()
    return EscalationPolicy(
        version=base.version,
        floors=(EscalationFloor(category=category, minimum=minimum),),
        rules=base.rules,
    )


def _provenance(**overrides) -> Provenance:
    defaults = {
        "producer_role": "content-author",
        "producer_id": None,
        "validator_id": "yaml-lint",
        "validator_version": "1.0.0",
        "independence": IndependenceRecord(
            producer_role="content-author",
            validator_role="qa-validator",
            actor_kind="agent",
            relation="independent",
            policy_id="verdx-v1",
            result="satisfied",
        ),
    }
    return Provenance(**(defaults | overrides))


def _draft(**overrides) -> ObservationDraft:
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


def _disposition(fingerprint: str, **overrides) -> Disposition:
    defaults = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "issue_fingerprint": fingerprint,
        "decision": "accepted-risk",
        "reason": "Known limitation",
        "team": "training-content",
        "timestamp": "2026-04-30T12:00:00Z",
    }
    return Disposition(**(defaults | overrides))


# ===========================================================================
# Basic canonicalization
# ===========================================================================


class TestBasicCanonicalization:
    def test_single_draft_produces_observation(self):
        results = canonicalize(
            run_id="run-001",
            drafts=[_draft()],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert len(results) == 1
        obs = results[0]
        assert obs.target_key == "sample/ch04/ge01"
        assert obs.rule_id == "yaml-syntax"
        assert obs.observation_id.startswith("obs:run-001:")
        assert obs.issue_fingerprint.startswith("qu:v1:")
        assert obs.escalation == EscalationLevel.L3
        assert obs.disposition is None

    def test_empty_drafts_produces_empty(self):
        results = canonicalize(
            run_id="run-001",
            drafts=[],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results == []

    def test_provenance_attached(self):
        prov = _provenance(
            producer_role="developer",
            producer_id="opaque-123",
        )
        results = canonicalize(
            run_id="run-001",
            drafts=[_draft()],
            provenance=prov,
            policy=_default_policy(),
            active_dispositions={},
        )
        obs = results[0]
        assert obs.producer_role == "developer"
        assert obs.producer_id == "opaque-123"
        assert obs.validator_id == "yaml-lint"
        assert obs.validator_version == "1.0.0"

    def test_location_normalized_in_output(self):
        results = canonicalize(
            run_id="run-001",
            drafts=[_draft(location=r"  exercise\ge01\step:3  ")],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].location == "exercise/ge01/step:3"

    def test_none_location_preserved(self):
        results = canonicalize(
            run_id="run-001",
            drafts=[_draft(location=None)],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].location is None

    def test_multiple_drafts_sorted_deterministically(self):
        drafts = [
            _draft(rule_id="z-rule", target_key="B"),
            _draft(rule_id="a-rule", target_key="A"),
            _draft(rule_id="m-rule", target_key="A"),
        ]
        results = canonicalize(
            run_id="run-001",
            drafts=drafts,
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert [o.target_key for o in results] == ["A", "A", "B"]
        assert results[0].rule_id == "a-rule"
        assert results[1].rule_id == "m-rule"


# ===========================================================================
# Determinism
# ===========================================================================


class TestDeterminism:
    def test_same_inputs_same_outputs(self):
        args = {
            "run_id": "run-001",
            "drafts": [_draft()],
            "provenance": _provenance(),
            "policy": _default_policy(),
            "active_dispositions": {},
        }
        r1 = canonicalize(**args)
        r2 = canonicalize(**args)
        assert r1[0].observation_id == r2[0].observation_id
        assert r1[0].issue_fingerprint == r2[0].issue_fingerprint
        assert r1[0].escalation == r2[0].escalation

    def test_summary_change_does_not_affect_fingerprint(self):
        r1 = canonicalize(
            run_id="run-001",
            drafts=[_draft(summary="Version A")],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        r2 = canonicalize(
            run_id="run-001",
            drafts=[_draft(summary="Version B - reworded")],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert r1[0].issue_fingerprint == r2[0].issue_fingerprint

    def test_evidence_change_does_not_affect_fingerprint(self):
        e1 = Evidence(
            source="lint",
            kind="err",
            details="d",
            data={"line": 1},
        )
        e2 = Evidence(
            source="lint",
            kind="err",
            details="d",
            data={"line": 99},
        )
        r1 = canonicalize(
            run_id="run-001",
            drafts=[_draft(evidence=(e1,))],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        r2 = canonicalize(
            run_id="run-001",
            drafts=[_draft(evidence=(e2,))],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert r1[0].issue_fingerprint == r2[0].issue_fingerprint

    def test_evidence_change_can_change_observation_id(self):
        e1 = Evidence(
            source="lint",
            kind="err",
            details="d",
            data={"line": 1},
        )
        e2 = Evidence(
            source="lint",
            kind="err",
            details="d",
            data={"line": 99},
        )
        r1 = canonicalize(
            run_id="run-001",
            drafts=[_draft(evidence=(e1,))],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        r2 = canonicalize(
            run_id="run-001",
            drafts=[_draft(evidence=(e2,))],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert r1[0].observation_id != r2[0].observation_id

    def test_run_id_change_changes_observation_id(self):
        r1 = canonicalize(
            run_id="run-001",
            drafts=[_draft()],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        r2 = canonicalize(
            run_id="run-002",
            drafts=[_draft()],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert r1[0].observation_id != r2[0].observation_id
        assert r1[0].issue_fingerprint == r2[0].issue_fingerprint


# ===========================================================================
# Escalation
# ===========================================================================


class TestEscalation:
    def test_low_dims_get_l0(self):
        d = _draft(dimensions={"x": DimLevel.LOW})
        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].escalation == EscalationLevel.L0

    def test_high_dims_get_l3(self):
        d = _draft(dimensions={"x": DimLevel.HIGH})
        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].escalation == EscalationLevel.L3

    def test_blocking_dims_get_l4(self):
        d = _draft(dimensions={"x": DimLevel.BLOCKING})
        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].escalation == EscalationLevel.L4


# ===========================================================================
# Disposition application
# ===========================================================================


class TestDispositionApplication:
    def _fingerprint_for_draft(self, d: ObservationDraft) -> str:
        """Get the fingerprint a draft would produce."""
        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        return results[0].issue_fingerprint

    def test_accepted_risk_caps_at_l1(self):
        d = _draft(dimensions={"x": DimLevel.HIGH})
        fp = self._fingerprint_for_draft(d)
        disp = _disposition(fp, decision="accepted-risk")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={fp: disp},
        )
        obs = results[0]
        assert obs.escalation == EscalationLevel.L1
        assert obs.disposition is not None
        assert obs.disposition.effect == "capped"
        assert obs.disposition.original_escalation == EscalationLevel.L3
        assert obs.disposition.final_escalation == EscalationLevel.L1

    def test_false_positive_caps_at_l1(self):
        d = _draft(dimensions={"x": DimLevel.HIGH})
        fp = self._fingerprint_for_draft(d)
        disp = _disposition(fp, decision="false-positive")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={fp: disp},
        )
        assert results[0].escalation == EscalationLevel.L1
        assert results[0].disposition.effect == "capped"

    def test_deferred_retains_escalation(self):
        d = _draft(dimensions={"x": DimLevel.HIGH})
        fp = self._fingerprint_for_draft(d)
        disp = _disposition(fp, decision="deferred")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={fp: disp},
        )
        assert results[0].escalation == EscalationLevel.L3
        assert results[0].disposition.effect == "deferred"

    def test_context_dependent_retains_escalation(self):
        d = _draft(dimensions={"x": DimLevel.HIGH})
        fp = self._fingerprint_for_draft(d)
        disp = _disposition(fp, decision="context-dependent")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={fp: disp},
        )
        assert results[0].escalation == EscalationLevel.L3
        assert results[0].disposition.effect == "reevaluate"

    def test_disposition_cannot_reduce_below_floor(self):
        d = _draft(
            dimensions={"x": DimLevel.LOW},
            category="security",
        )
        fp_results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_policy_with_floor("security", EscalationLevel.L3),
            active_dispositions={},
        )
        fp = fp_results[0].issue_fingerprint
        disp = _disposition(fp, decision="accepted-risk")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_policy_with_floor("security", EscalationLevel.L3),
            active_dispositions={fp: disp},
        )
        assert results[0].escalation == EscalationLevel.L3

    def test_observation_still_created_with_disposition(self):
        """Dispositions never hide findings — only adjust escalation."""
        d = _draft(dimensions={"x": DimLevel.HIGH})
        fp = self._fingerprint_for_draft(d)
        disp = _disposition(fp, decision="accepted-risk")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={fp: disp},
        )
        assert len(results) == 1

    def test_unmatched_disposition_ignored(self):
        d = _draft(dimensions={"x": DimLevel.HIGH})
        disp = _disposition("nonexistent-fingerprint")

        results = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={"nonexistent-fingerprint": disp},
        )
        assert results[0].disposition is None
        assert results[0].escalation == EscalationLevel.L3


# ===========================================================================
# Independence handling
# ===========================================================================


class TestIndependenceHandling:
    def test_satisfied_preserves_confidence(self):
        prov = _provenance(
            independence=IndependenceRecord(
                producer_role="author",
                validator_role="reviewer",
                actor_kind="agent",
                relation="independent",
                policy_id="verdx-v1",
                result="satisfied",
            ),
        )
        results = canonicalize(
            run_id="r",
            drafts=[_draft(confidence=Confidence.CONFIRMED)],
            provenance=prov,
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].confidence == Confidence.CONFIRMED

    def test_unmet_caps_confidence_to_possible(self):
        prov = _provenance(
            independence=IndependenceRecord(
                producer_role="author",
                validator_role="reviewer",
                actor_kind="agent",
                relation="self",
                policy_id="verdx-v1",
                result="unmet",
            ),
        )
        results = canonicalize(
            run_id="r",
            drafts=[_draft(confidence=Confidence.CONFIRMED)],
            provenance=prov,
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].confidence == Confidence.POSSIBLE

    def test_waived_preserves_confidence(self):
        prov = _provenance(
            independence=IndependenceRecord(
                producer_role="author",
                validator_role="reviewer",
                actor_kind="agent",
                relation="same-team",
                policy_id="verdx-v1",
                result="waived",
            ),
        )
        results = canonicalize(
            run_id="r",
            drafts=[_draft(confidence=Confidence.CONFIRMED)],
            provenance=prov,
            policy=_default_policy(),
            active_dispositions={},
        )
        assert results[0].confidence == Confidence.CONFIRMED

    def test_independence_record_attached(self):
        results = canonicalize(
            run_id="r",
            drafts=[_draft()],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        ir = results[0].independence
        assert ir.producer_role == "content-author"
        assert ir.validator_role == "qa-validator"
        assert ir.result == "satisfied"
