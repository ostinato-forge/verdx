"""Property-based tests for canonicalization using Hypothesis.

Proves structural invariants that must hold for all valid inputs.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings
from hypothesis import strategies as st

from verdx.contracts.dimensions import (
    Confidence,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION, Disposition
from verdx.contracts.independence import IndependenceRecord
from verdx.contracts.observation import ObservationDraft
from verdx.engine.validation.canonicalize import Provenance, canonicalize
from verdx.engine.validation.escalation import (
    EscalationFloor,
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

dim_levels = st.sampled_from(list(DimLevel))
severities = st.sampled_from(list(Severity))
confidences = st.sampled_from(list(Confidence))
nonempty_text = st.text(
    alphabet=st.characters(categories=("L", "N", "P")),
    min_size=1,
    max_size=20,
)


@st.composite
def dimension_dicts(draw):
    keys = draw(st.lists(nonempty_text, min_size=1, max_size=5, unique=True))
    return {k: draw(dim_levels) for k in keys}


@st.composite
def drafts(draw):
    return ObservationDraft(
        target_key=draw(nonempty_text),
        location=draw(st.one_of(st.none(), nonempty_text)),
        rule_id=draw(nonempty_text),
        category=draw(nonempty_text),
        severity=draw(severities),
        confidence=draw(confidences),
        dimensions=draw(dimension_dicts()),
        summary=draw(nonempty_text),
    )


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
                name="high",
                match=RuleMatch(
                    quantifier="any",
                    dimension="*",
                    level=DimLevel.HIGH,
                ),
                level=EscalationLevel.L3,
            ),
            EscalationRule(
                name="medium",
                match=RuleMatch(
                    quantifier="any",
                    dimension="*",
                    level=DimLevel.MEDIUM,
                ),
                level=EscalationLevel.L1,
            ),
            EscalationRule(
                name="low",
                match=RuleMatch(
                    quantifier="all",
                    dimension="*",
                    level=DimLevel.LOW,
                ),
                level=EscalationLevel.L0,
            ),
        ),
    )


def _provenance() -> Provenance:
    return Provenance(
        producer_role="author",
        producer_id=None,
        validator_id="test-validator",
        validator_version="1.0.0",
        independence=IndependenceRecord(
            producer_role="author",
            validator_role="reviewer",
            actor_kind="agent",
            relation="independent",
            policy_id="verdx-v1",
            result="satisfied",
        ),
    )


# ===========================================================================
# Property tests
# ===========================================================================


class TestCanonicalizeProperties:
    @given(draft=drafts())
    @settings(max_examples=50)
    def test_same_inputs_same_outputs(self, draft):
        args = {
            "run_id": "run-prop",
            "drafts": [draft],
            "provenance": _provenance(),
            "policy": _default_policy(),
            "active_dispositions": {},
        }
        r1 = canonicalize(**args)
        r2 = canonicalize(**args)
        assert r1[0].observation_id == r2[0].observation_id
        assert r1[0].issue_fingerprint == r2[0].issue_fingerprint
        assert r1[0].escalation == r2[0].escalation

    @given(
        draft1=drafts(),
        draft2=drafts(),
    )
    @settings(max_examples=50)
    def test_draft_ordering_does_not_affect_sorted_output(
        self,
        draft1,
        draft2,
    ):
        args_base = {
            "run_id": "run-prop",
            "provenance": _provenance(),
            "policy": _default_policy(),
            "active_dispositions": {},
        }
        r1 = canonicalize(drafts=[draft1, draft2], **args_base)
        r2 = canonicalize(drafts=[draft2, draft1], **args_base)
        fps1 = [o.issue_fingerprint for o in r1]
        fps2 = [o.issue_fingerprint for o in r2]
        assert fps1 == fps2

    @given(
        draft=drafts(),
        summary1=nonempty_text,
        summary2=nonempty_text,
    )
    @settings(max_examples=50)
    def test_summary_does_not_affect_fingerprint(
        self,
        draft,
        summary1,
        summary2,
    ):
        d1 = ObservationDraft(
            target_key=draft.target_key,
            location=draft.location,
            rule_id=draft.rule_id,
            category=draft.category,
            severity=draft.severity,
            confidence=draft.confidence,
            dimensions=draft.dimensions,
            summary=summary1,
        )
        d2 = ObservationDraft(
            target_key=draft.target_key,
            location=draft.location,
            rule_id=draft.rule_id,
            category=draft.category,
            severity=draft.severity,
            confidence=draft.confidence,
            dimensions=draft.dimensions,
            summary=summary2,
        )
        args = {
            "run_id": "run-prop",
            "provenance": _provenance(),
            "policy": _default_policy(),
            "active_dispositions": {},
        }
        r1 = canonicalize(drafts=[d1], **args)
        r2 = canonicalize(drafts=[d2], **args)
        assert r1[0].issue_fingerprint == r2[0].issue_fingerprint

    @given(draft=drafts())
    @settings(max_examples=50)
    def test_all_observations_have_identity(self, draft):
        results = canonicalize(
            run_id="run-prop",
            drafts=[draft],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        for obs in results:
            assert obs.observation_id
            assert obs.issue_fingerprint
            assert obs.schema_version == "verdx.observation.v1"

    @given(draft=drafts())
    @settings(max_examples=50)
    def test_all_observations_have_escalation(self, draft):
        results = canonicalize(
            run_id="run-prop",
            drafts=[draft],
            provenance=_provenance(),
            policy=_default_policy(),
            active_dispositions={},
        )
        for obs in results:
            assert isinstance(obs.escalation, EscalationLevel)

    @given(draft=drafts())
    @settings(max_examples=30)
    def test_disposition_cannot_reduce_below_floor(self, draft):
        d = ObservationDraft(
            target_key=draft.target_key,
            location=draft.location,
            rule_id=draft.rule_id,
            category="secure-cat",
            severity=draft.severity,
            confidence=draft.confidence,
            dimensions=draft.dimensions,
            summary=draft.summary,
        )
        floor_level = EscalationLevel.L3
        policy = EscalationPolicy(
            version=1,
            floors=(
                EscalationFloor(
                    category="secure-cat",
                    minimum=floor_level,
                ),
            ),
            rules=_default_policy().rules,
        )

        r_no_disp = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=policy,
            active_dispositions={},
        )
        fp = r_no_disp[0].issue_fingerprint

        disp = Disposition(
            schema_version=DISPOSITION_SCHEMA_VERSION,
            issue_fingerprint=fp,
            decision="accepted-risk",
            reason="test",
            team="test-team",
            timestamp="2026-01-01T00:00:00Z",
        )
        r_with_disp = canonicalize(
            run_id="r",
            drafts=[d],
            provenance=_provenance(),
            policy=policy,
            active_dispositions={fp: disp},
        )

        escalation_order = list(EscalationLevel)
        floor_rank = escalation_order.index(floor_level)
        final_rank = escalation_order.index(
            r_with_disp[0].escalation,
        )
        assert final_rank >= floor_rank
