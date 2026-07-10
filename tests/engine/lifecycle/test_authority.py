"""Tests for authority role enforcement."""

import pytest

from verdx.contracts.roles import Role
from verdx.contracts.states import State
from verdx.engine.lifecycle.authority import allowed_roles, is_role_permitted


def test_has_7_roles():
    assert len(Role) == 7


def test_intent_creation_requires_requester_or_scope_owner():
    roles = allowed_roles(None, State.Clarifying)
    assert roles == {Role.requester, Role.scope_owner}


def test_implementer_cannot_create_intent():
    assert not is_role_permitted(Role.implementer, None, State.Clarifying)


def test_implementer_can_build():
    assert is_role_permitted(Role.implementer, State.Building, State.Validating)


def test_reviewer_cannot_build():
    assert not is_role_permitted(Role.reviewer, State.Building, State.Validating)


def test_scope_owner_can_approve():
    assert is_role_permitted(Role.scope_owner, State.AwaitingApproval, State.Building)


def test_implementer_cannot_approve():
    assert not is_role_permitted(Role.implementer, State.AwaitingApproval, State.Building)


def test_reviewer_can_review():
    assert is_role_permitted(Role.reviewer, State.AwaitingReview, State.Integrating)


def test_implementer_cannot_review():
    assert not is_role_permitted(Role.implementer, State.AwaitingReview, State.Integrating)


def test_tech_lead_can_override():
    assert is_role_permitted(Role.tech_lead, State.OverridePending, State.AwaitingReview)


def test_reviewer_cannot_override():
    assert not is_role_permitted(Role.reviewer, State.OverridePending, State.AwaitingReview)


def test_merge_actor_can_integrate():
    assert is_role_permitted(Role.merge_actor, State.Integrating, State.Integrated)


def test_reviewer_cannot_integrate():
    assert not is_role_permitted(Role.reviewer, State.Integrating, State.Integrated)


def test_any_role_can_block():
    for role in Role:
        assert is_role_permitted(role, State.Building, State.Blocked)


@pytest.mark.parametrize("terminal", [State.Abandoned, State.Superseded])
def test_abandonment_requires_authority(terminal: State):
    assert is_role_permitted(Role.scope_owner, State.Building, terminal)
    assert not is_role_permitted(Role.implementer, State.Building, terminal)


def test_revert_requires_scope_owner_or_tech_lead_or_merge_actor():
    permitted = allowed_roles(State.Integrated, State.Reverted)
    assert Role.scope_owner in permitted
    assert Role.tech_lead in permitted
    assert Role.merge_actor in permitted
    assert Role.implementer not in permitted
