"""Authority roles and per-transition enforcement from the design.md authority matrix."""

from verdx.contracts.roles import Role
from verdx.contracts.states import State

_ROLES_FOR_TRANSITION: dict[tuple[State | None, State], frozenset[Role]] = {
    # Clarify stage: requester or scope_owner create/sign off intent
    (None, State.Clarifying): frozenset({Role.requester, Role.scope_owner}),
    (State.Clarifying, State.Clarifying): frozenset(
        {Role.requester, Role.scope_owner, Role.implementer}
    ),
    (State.Clarifying, State.SpecDrafting): frozenset({Role.requester, Role.scope_owner}),
    (State.Clarifying, State.Building): frozenset(
        {Role.requester, Role.scope_owner, Role.implementer}
    ),
    # Spec stage: self-transition and completion
    (State.SpecDrafting, State.SpecDrafting): frozenset(
        {
            Role.implementer,
            Role.requester,
            Role.scope_owner,
        }
    ),
    (State.SpecDrafting, State.AwaitingApproval): frozenset(
        {
            Role.implementer,
            Role.requester,
            Role.scope_owner,
        }
    ),
    # Approve: human-only gate
    (State.AwaitingApproval, State.Building): frozenset(
        {
            Role.requester,
            Role.scope_owner,
        }
    ),
    # Build stage: self-transitions and completion
    (State.Building, State.Building): frozenset({Role.implementer}),
    (State.Building, State.Validating): frozenset({Role.implementer}),
    # Validate stage: self-transition, completion, and failure
    (State.Validating, State.Validating): frozenset(
        {
            Role.implementer,
            Role.reviewer,
            Role.tech_lead,
        }
    ),
    (State.Validating, State.Building): frozenset(
        {Role.implementer, Role.reviewer, Role.tech_lead}
    ),
    (State.Validating, State.AwaitingReview): frozenset(
        {
            Role.implementer,
            Role.reviewer,
            Role.tech_lead,
        }
    ),
    (State.Validating, State.OverridePending): frozenset(
        {
            Role.implementer,
            Role.reviewer,
            Role.tech_lead,
        }
    ),
    # Override: tech_lead or override_approver
    (State.OverridePending, State.AwaitingReview): frozenset(
        {
            Role.tech_lead,
            Role.override_approver,
        }
    ),
    (State.OverridePending, State.Building): frozenset(
        {
            Role.tech_lead,
            Role.override_approver,
        }
    ),
    # Review: reviewer or tech_lead decide
    (State.AwaitingReview, State.Integrating): frozenset({Role.reviewer, Role.tech_lead}),
    (State.AwaitingReview, State.Building): frozenset({Role.reviewer, Role.tech_lead}),
    (State.AwaitingReview, State.SpecDrafting): frozenset({Role.reviewer, Role.tech_lead}),
    (State.AwaitingReview, State.Clarifying): frozenset({Role.reviewer, Role.tech_lead}),
    # Integration: merge_actor
    (State.Integrating, State.Integrating): frozenset({Role.merge_actor}),
    (State.Integrating, State.Integrated): frozenset({Role.merge_actor}),
    (State.Integrating, State.NeedsRebase): frozenset({Role.merge_actor}),
    # Rebase detection (from any state where base can go stale)
    (State.AwaitingReview, State.NeedsRebase): frozenset({Role.merge_actor}),
    (State.Validating, State.NeedsRebase): frozenset({Role.merge_actor}),
    # Rebase resolution
    (State.NeedsRebase, State.NeedsRevalidate): frozenset({Role.implementer}),
    # Revalidation (success, failure, or skip-to-review)
    (State.NeedsRevalidate, State.Building): frozenset(
        {
            Role.implementer,
            Role.reviewer,
            Role.tech_lead,
        }
    ),
    (State.NeedsRevalidate, State.NeedsRereview): frozenset(
        {
            Role.implementer,
            Role.reviewer,
            Role.tech_lead,
        }
    ),
    (State.NeedsRevalidate, State.AwaitingReview): frozenset(
        {
            Role.implementer,
            Role.reviewer,
            Role.tech_lead,
        }
    ),
    # Re-review (carry-forward or full re-review)
    (State.NeedsRereview, State.Integrating): frozenset({Role.reviewer, Role.tech_lead}),
    (State.NeedsRereview, State.AwaitingReview): frozenset({Role.reviewer, Role.tech_lead}),
    # Build failures: implementer escalates
    (State.Building, State.AwaitingReview): frozenset({Role.implementer}),
    # Rejection from approval back to spec or clarify
    (State.AwaitingApproval, State.SpecDrafting): frozenset(
        {
            Role.requester,
            Role.scope_owner,
        }
    ),
    (State.AwaitingApproval, State.Clarifying): frozenset(
        {
            Role.requester,
            Role.scope_owner,
        }
    ),
    # Integration failure
    (State.Integrating, State.Building): frozenset({Role.merge_actor}),
}

# Terminal transitions can come from any active state, any role with authority
_TERMINAL_ROLES: dict[State, frozenset[Role]] = {
    State.Abandoned: frozenset({Role.requester, Role.scope_owner, Role.tech_lead}),
    State.Superseded: frozenset({Role.requester, Role.scope_owner, Role.tech_lead}),
    State.Reverted: frozenset({Role.scope_owner, Role.tech_lead, Role.merge_actor}),
}

# Blocked/unblocked can involve any role
_BLOCK_ROLES = frozenset(Role)


def allowed_roles(from_state: State | None, to_state: State) -> frozenset[Role]:
    if to_state == State.Blocked:
        return _BLOCK_ROLES
    if from_state == State.Blocked:
        return _BLOCK_ROLES
    if to_state in _TERMINAL_ROLES and to_state != State.Integrated:
        return _TERMINAL_ROLES[to_state]
    key = (from_state, to_state)
    return _ROLES_FOR_TRANSITION.get(key, frozenset())


def is_role_permitted(role: Role, from_state: State | None, to_state: State) -> bool:
    return role in allowed_roles(from_state, to_state)
