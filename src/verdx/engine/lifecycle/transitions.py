"""Valid state transitions — the truth table of the intent-fulfillment cycle.

Each entry maps (from_state, event_type) → to_state. If a transition is not
in this table, the reducer rejects it. The table derives from design.md's
stage definitions, failure recovery paths, and exception flows.
"""

from verdx.contracts.events import EventType
from verdx.contracts.states import State

Transition = tuple[State | None, EventType]

TRANSITIONS: dict[Transition, State] = {
    # === Happy path: Clarify → Spec → Approve → Build → Validate → Review → Integrate ===
    # Intent creation (previous_state is None)
    (None, EventType.intent_created): State.Clarifying,
    # Clarify stage
    (State.Clarifying, EventType.clarify_started): State.Clarifying,
    (State.Clarifying, EventType.clarify_completed): State.SpecDrafting,
    (State.Clarifying, EventType.fast_path_eligible): State.Building,
    # Spec stage
    (State.SpecDrafting, EventType.spec_started): State.SpecDrafting,
    (State.SpecDrafting, EventType.spec_completed): State.AwaitingApproval,
    # Approve gate (human-only)
    (State.AwaitingApproval, EventType.approval_granted): State.Building,
    (State.AwaitingApproval, EventType.approval_rejected): State.SpecDrafting,
    # Build stage
    (State.Building, EventType.build_started): State.Building,
    (State.Building, EventType.build_completed): State.Validating,
    (State.Building, EventType.build_failed): State.Building,
    (State.Building, EventType.build_escalated): State.AwaitingReview,
    # Validate stage
    (State.Validating, EventType.validation_started): State.Validating,
    (State.Validating, EventType.validation_completed): State.AwaitingReview,
    (State.Validating, EventType.validation_failed): State.Building,
    (State.Validating, EventType.override_requested): State.OverridePending,
    # Override path
    (State.OverridePending, EventType.override_granted): State.AwaitingReview,
    (State.OverridePending, EventType.override_rejected): State.Building,
    # Review stage
    (State.AwaitingReview, EventType.review_submitted): State.Integrating,
    (State.AwaitingReview, EventType.fast_path_authorized): State.Integrating,
    # Integration
    (State.Integrating, EventType.integration_started): State.Integrating,
    (State.Integrating, EventType.integration_completed): State.Integrated,
    (State.Integrating, EventType.integration_failed): State.Building,
    # === Exception paths ===
    # Rebase (base commit goes stale)
    (State.Integrating, EventType.rebase_required): State.NeedsRebase,
    (State.AwaitingReview, EventType.rebase_required): State.NeedsRebase,
    (State.Validating, EventType.rebase_required): State.NeedsRebase,
    # Rebase resolution
    (State.NeedsRebase, EventType.rebase_completed): State.NeedsRevalidate,
    # Revalidation
    (State.NeedsRevalidate, EventType.revalidation_completed): State.NeedsRereview,
    (State.NeedsRevalidate, EventType.validation_failed): State.Building,
    # Re-review (carry-forward or full re-review)
    (State.NeedsRereview, EventType.carry_forward_granted): State.Integrating,
    (State.NeedsRereview, EventType.rereview_required): State.AwaitingReview,
    # === Review rejections (backward transitions) ===
    (State.AwaitingReview, EventType.approval_rejected): State.Building,
    # === Blocked/unblocked (from any active state) ===
    # Handled specially in the reducer — any active state can transition to Blocked
    # === Terminal states (from any active state) ===
    # Handled specially in the reducer — abandoned/superseded/reverted
}

# Events that can occur from any non-terminal state
UNIVERSAL_EVENTS: dict[EventType, State] = {
    EventType.blocked: State.Blocked,
    EventType.abandoned: State.Abandoned,
    EventType.superseded: State.Superseded,
    EventType.reverted: State.Reverted,
}


def resolve_transition(from_state: State | None, event_type: EventType) -> State | None:
    explicit = TRANSITIONS.get((from_state, event_type))
    if explicit is not None:
        return explicit

    if from_state is not None and not from_state.is_terminal:
        # Unblocked returns to the state recorded before blocking
        if event_type == EventType.unblocked:
            return None  # Caller must supply the pre-block state

        universal = UNIVERSAL_EVENTS.get(event_type)
        if universal is not None:
            return universal

    return None


def is_valid_transition(from_state: State | None, event_type: EventType) -> bool:
    if (from_state, event_type) in TRANSITIONS:
        return True
    return (
        from_state is not None
        and not from_state.is_terminal
        and (event_type in UNIVERSAL_EVENTS or event_type == EventType.unblocked)
    )
