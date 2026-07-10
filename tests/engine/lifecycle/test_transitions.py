"""Tests for state transition rules."""

import pytest

from verdx.contracts.events import EventType
from verdx.contracts.states import State
from verdx.engine.lifecycle.transitions import is_valid_transition, resolve_transition


class TestHappyPath:
    def test_intent_created(self):
        assert resolve_transition(None, EventType.intent_created) == State.Clarifying

    def test_clarify_completed(self):
        assert (
            resolve_transition(State.Clarifying, EventType.clarify_completed) == State.SpecDrafting
        )

    def test_spec_completed(self):
        assert (
            resolve_transition(State.SpecDrafting, EventType.spec_completed)
            == State.AwaitingApproval
        )

    def test_approval_granted(self):
        assert (
            resolve_transition(State.AwaitingApproval, EventType.approval_granted) == State.Building
        )

    def test_build_completed(self):
        assert resolve_transition(State.Building, EventType.build_completed) == State.Validating

    def test_validation_completed(self):
        assert (
            resolve_transition(State.Validating, EventType.validation_completed)
            == State.AwaitingReview
        )

    def test_review_submitted(self):
        assert (
            resolve_transition(State.AwaitingReview, EventType.review_submitted)
            == State.Integrating
        )

    def test_integration_completed(self):
        assert (
            resolve_transition(State.Integrating, EventType.integration_completed)
            == State.Integrated
        )


class TestRejectionPaths:
    def test_approval_rejected(self):
        assert (
            resolve_transition(State.AwaitingApproval, EventType.approval_rejected)
            == State.SpecDrafting
        )

    def test_review_rejection_to_build(self):
        assert (
            resolve_transition(State.AwaitingReview, EventType.approval_rejected) == State.Building
        )

    def test_validation_failure_to_build(self):
        assert resolve_transition(State.Validating, EventType.validation_failed) == State.Building

    def test_override_rejected_to_build(self):
        assert (
            resolve_transition(State.OverridePending, EventType.override_rejected) == State.Building
        )

    def test_integration_failure_to_build(self):
        assert resolve_transition(State.Integrating, EventType.integration_failed) == State.Building


class TestOverridePath:
    def test_override_requested(self):
        assert (
            resolve_transition(State.Validating, EventType.override_requested)
            == State.OverridePending
        )

    def test_override_granted(self):
        assert (
            resolve_transition(State.OverridePending, EventType.override_granted)
            == State.AwaitingReview
        )


class TestRebasePath:
    def test_rebase_required_from_integrating(self):
        assert resolve_transition(State.Integrating, EventType.rebase_required) == State.NeedsRebase

    def test_rebase_required_from_awaiting_review(self):
        assert (
            resolve_transition(State.AwaitingReview, EventType.rebase_required) == State.NeedsRebase
        )

    def test_rebase_completed(self):
        assert (
            resolve_transition(State.NeedsRebase, EventType.rebase_completed)
            == State.NeedsRevalidate
        )

    def test_revalidation_completed(self):
        assert (
            resolve_transition(State.NeedsRevalidate, EventType.revalidation_completed)
            == State.NeedsRereview
        )

    def test_carry_forward_granted(self):
        assert (
            resolve_transition(State.NeedsRereview, EventType.carry_forward_granted)
            == State.Integrating
        )

    def test_rereview_required(self):
        assert (
            resolve_transition(State.NeedsRereview, EventType.rereview_required)
            == State.AwaitingReview
        )


class TestUniversalEvents:
    @pytest.mark.parametrize(
        "state",
        [s for s in State if not s.is_terminal],
    )
    def test_any_active_state_can_be_blocked(self, state: State):
        assert resolve_transition(state, EventType.blocked) == State.Blocked

    @pytest.mark.parametrize(
        "state",
        [s for s in State if not s.is_terminal],
    )
    def test_any_active_state_can_be_abandoned(self, state: State):
        assert resolve_transition(state, EventType.abandoned) == State.Abandoned

    def test_terminal_state_cannot_be_blocked(self):
        assert resolve_transition(State.Integrated, EventType.blocked) is None

    def test_terminal_state_cannot_be_abandoned(self):
        assert resolve_transition(State.Integrated, EventType.abandoned) is None


class TestInvalidTransitions:
    def test_cannot_build_from_clarifying(self):
        assert not is_valid_transition(State.Clarifying, EventType.build_completed)

    def test_cannot_review_from_building(self):
        assert not is_valid_transition(State.Building, EventType.review_submitted)

    def test_cannot_create_intent_from_building(self):
        assert not is_valid_transition(State.Building, EventType.intent_created)

    def test_none_only_allows_intent_created(self):
        assert is_valid_transition(None, EventType.intent_created)
        assert not is_valid_transition(None, EventType.build_completed)


class TestSelfTransitions:
    def test_clarify_started_stays_in_clarifying(self):
        assert resolve_transition(State.Clarifying, EventType.clarify_started) == State.Clarifying

    def test_build_failed_stays_in_building(self):
        assert resolve_transition(State.Building, EventType.build_failed) == State.Building
