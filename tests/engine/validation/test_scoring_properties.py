"""Property-based tests for scoring determinism.

Proves that scoring is deterministic and satisfies basic invariants.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings
from hypothesis import strategies as st

from verdx.contracts.assessment import ValidatorStatus
from verdx.contracts.dimensions import (
    Confidence,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.independence import IndependenceRecord
from verdx.contracts.observation import Evidence, Observation
from verdx.engine.validation.scoring import compute_score

_SCHEMA_VERSION = "verdx.observation.v1"

nonempty = st.text(
    alphabet=st.characters(categories=("L", "N")),
    min_size=1,
    max_size=15,
)


@st.composite
def observations(draw):
    return Observation(
        schema_version=_SCHEMA_VERSION,
        observation_id=draw(nonempty),
        issue_fingerprint=draw(nonempty),
        target_key="target",
        location=draw(st.one_of(st.none(), nonempty)),
        rule_id=draw(nonempty),
        producer_role="author",
        producer_id=None,
        validator_id="test-v",
        validator_version="1.0.0",
        independence=IndependenceRecord(
            producer_role="author",
            validator_role="reviewer",
            actor_kind="agent",
            relation="independent",
            policy_id="verdx-v1",
            result="satisfied",
        ),
        category=draw(nonempty),
        severity=draw(st.sampled_from(list(Severity))),
        confidence=draw(st.sampled_from(list(Confidence))),
        dimensions={draw(nonempty): draw(st.sampled_from(list(DimLevel)))},
        escalation=draw(st.sampled_from(list(EscalationLevel))),
        summary="test observation",
        evidence=(Evidence(source="test", kind="text", details="detail", data={}),),
    )


class TestScoringDeterminism:
    @given(obs_list=st.lists(observations(), min_size=0, max_size=5))
    @settings(max_examples=50)
    def test_same_inputs_same_score(self, obs_list):
        statuses = [
            ValidatorStatus(
                validator_id="test-v",
                validator_version="1.0.0",
                status="completed",
                checks_run=max(len(obs_list), 1),
                checks_passed=0,
                observation_count=len(obs_list),
                duration_ms=10,
            )
        ]
        s1 = compute_score(obs_list, statuses, frozenset())
        s2 = compute_score(obs_list, statuses, frozenset())
        assert s1.overall == s2.overall
        assert s1.coverage_penalty == s2.coverage_penalty

    @given(obs_list=st.lists(observations(), min_size=0, max_size=5))
    @settings(max_examples=50)
    def test_score_in_valid_range(self, obs_list):
        statuses = [
            ValidatorStatus(
                validator_id="test-v",
                validator_version="1.0.0",
                status="completed",
                checks_run=max(len(obs_list), 1),
                checks_passed=0,
                observation_count=len(obs_list),
                duration_ms=10,
            )
        ]
        score = compute_score(obs_list, statuses, frozenset())
        assert 0 <= score.overall <= 100

    def test_no_observations_yields_perfect_score(self):
        statuses = [
            ValidatorStatus(
                validator_id="test-v",
                validator_version="1.0.0",
                status="completed",
                checks_run=1,
                checks_passed=1,
                observation_count=0,
                duration_ms=10,
            )
        ]
        score = compute_score([], statuses, frozenset())
        assert score.overall == 100

    @given(obs=observations())
    @settings(max_examples=30)
    def test_more_observations_never_increase_score(self, obs):
        statuses = [
            ValidatorStatus(
                validator_id="test-v",
                validator_version="1.0.0",
                status="completed",
                checks_run=5,
                checks_passed=0,
                observation_count=2,
                duration_ms=10,
            )
        ]
        score_one = compute_score([obs], statuses, frozenset())
        score_two = compute_score([obs, obs], statuses, frozenset())
        assert score_two.overall <= score_one.overall
