"""The 16 canonical states of the intent-fulfillment cycle."""

from enum import StrEnum


class State(StrEnum):
    # Active states
    Clarifying = "Clarifying"
    SpecDrafting = "SpecDrafting"
    AwaitingApproval = "AwaitingApproval"
    Building = "Building"
    Validating = "Validating"
    AwaitingReview = "AwaitingReview"
    OverridePending = "OverridePending"
    NeedsRebase = "NeedsRebase"
    NeedsRevalidate = "NeedsRevalidate"
    NeedsRereview = "NeedsRereview"
    Integrating = "Integrating"
    Blocked = "Blocked"

    # Terminal states
    Integrated = "Integrated"
    Abandoned = "Abandoned"
    Superseded = "Superseded"
    Reverted = "Reverted"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES

    @property
    def is_human_gate(self) -> bool:
        return self in _HUMAN_GATE_STATES


_TERMINAL_STATES = frozenset(
    {
        State.Integrated,
        State.Abandoned,
        State.Superseded,
        State.Reverted,
    }
)

_HUMAN_GATE_STATES = frozenset(
    {
        State.AwaitingApproval,
        State.AwaitingReview,
        State.OverridePending,
        State.NeedsRereview,
    }
)
