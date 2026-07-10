"""Tests for pure Four-Eyes independence evaluation."""

from __future__ import annotations

import pytest

from verdx.engine.validation.independence import ActorFacts, evaluate_independence


def _actor(**overrides: object) -> ActorFacts:
    defaults = {
        "opaque_id": "actor-1",
        "role": "author",
        "kind": "agent",
        "team": "alpha",
    }
    return ActorFacts(**(defaults | overrides))  # type: ignore[arg-type]


class TestActorFacts:
    def test_rejects_empty_role(self) -> None:
        with pytest.raises(ValueError, match="role"):
            _actor(role="")

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            _actor(kind="robot")


class TestEvaluateIndependence:
    def test_role_fallback_preserves_independent_roles(self) -> None:
        record = evaluate_independence(
            producer_role="content-author",
            validator_role="training-content",
        )
        assert record.relation == "independent"
        assert record.result == "satisfied"

    def test_role_fallback_marks_self_validation_unmet(self) -> None:
        record = evaluate_independence(
            producer_role="training-content",
            validator_role="training-content",
        )
        assert record.relation == "self"
        assert record.result == "unmet"

    def test_different_actor_teams_satisfy_independence(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(opaque_id="author-1", team="alpha"),
            validator_actor=_actor(opaque_id="reviewer-1", role="reviewer", team="beta"),
        )
        assert record.relation == "independent"
        assert record.result == "satisfied"

    def test_same_team_is_unmet(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(opaque_id="author-1", team="alpha"),
            validator_actor=_actor(opaque_id="reviewer-1", role="reviewer", team="alpha"),
        )
        assert record.relation == "same-team"
        assert record.result == "unmet"

    def test_same_actor_is_self_unmet(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(opaque_id="same", team="alpha"),
            validator_actor=_actor(opaque_id="same", role="reviewer", team="beta"),
        )
        assert record.relation == "self"
        assert record.result == "unmet"

    def test_missing_counterpart_metadata_is_unknown_unmet(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(),
            validator_actor=None,
        )
        assert record.relation == "unknown"
        assert record.result == "unmet"

    def test_missing_team_metadata_is_unknown_unmet(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(team=None),
            validator_actor=_actor(opaque_id="reviewer-1", role="reviewer", team="beta"),
        )
        assert record.relation == "unknown"
        assert record.result == "unmet"

    def test_both_actors_none_independent_roles(self) -> None:
        record = evaluate_independence(
            producer_role="content-author",
            validator_role="training-structure",
        )
        assert record.relation == "independent"
        assert record.result == "satisfied"
        assert record.policy_id == "verdx-v1"

    def test_both_actors_none_same_role(self) -> None:
        record = evaluate_independence(
            producer_role="reviewer",
            validator_role="reviewer",
        )
        assert record.relation == "self"
        assert record.result == "unmet"

    def test_same_role_different_teams_is_self(self) -> None:
        record = evaluate_independence(
            producer_role="reviewer",
            validator_role="reviewer",
            producer_actor=_actor(opaque_id="p1", role="reviewer", team="alpha"),
            validator_actor=_actor(opaque_id="v1", role="reviewer", team="beta"),
        )
        assert record.relation == "self"
        assert record.result == "unmet"

    def test_same_opaque_id_different_roles_is_self(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(opaque_id="shared-id", role="author", team="alpha"),
            validator_actor=_actor(opaque_id="shared-id", role="reviewer", team="beta"),
        )
        assert record.relation == "self"
        assert record.result == "unmet"

    def test_custom_policy_id(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            policy_id="custom-policy-v2",
        )
        assert record.policy_id == "custom-policy-v2"

    def test_whitespace_producer_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="producer_role"):
            evaluate_independence(
                producer_role="   ",
                validator_role="reviewer",
            )

    def test_whitespace_validator_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="validator_role"):
            evaluate_independence(
                producer_role="author",
                validator_role=" ",
            )

    def test_whitespace_policy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="policy_id"):
            evaluate_independence(
                producer_role="author",
                validator_role="reviewer",
                policy_id="  ",
            )

    def test_actor_whitespace_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            _actor(role="  ")

    def test_actor_whitespace_team_rejected(self) -> None:
        with pytest.raises(ValueError, match="team"):
            _actor(team="  ")

    def test_actor_whitespace_opaque_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="opaque_id"):
            _actor(opaque_id="  ")

    def test_validator_team_none_producer_team_present(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(opaque_id="p1", team="alpha"),
            validator_actor=_actor(opaque_id="v1", role="reviewer", team=None),
        )
        assert record.relation == "unknown"
        assert record.result == "unmet"

    def test_actor_kind_propagated(self) -> None:
        record = evaluate_independence(
            producer_role="author",
            validator_role="reviewer",
            producer_actor=_actor(kind="human"),
            validator_actor=_actor(opaque_id="v1", role="reviewer", team="beta"),
        )
        assert record.actor_kind == "human"
