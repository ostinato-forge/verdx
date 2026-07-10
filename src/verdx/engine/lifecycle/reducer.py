"""Pure state reducer: (current_state, event) → ReducerResult.

No I/O, no side effects. Given the same inputs, always produces the same output.
The reducer validates three things:
  1. The transition is legal (from_state + event_type → to_state exists)
  2. The authority role is permitted for this transition
  3. The gate artifact is referenced if required
"""

from dataclasses import dataclass, field

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.events import EventType, StateEvent
from verdx.contracts.states import State
from verdx.engine.lifecycle.authority import is_role_permitted
from verdx.engine.lifecycle.gates import required_artifact
from verdx.engine.lifecycle.transitions import resolve_transition


@dataclass(frozen=True)
class ReducerResult:
    new_state: State
    valid: bool
    violations: tuple[str, ...] = ()
    gate_artifact: ArtifactType | None = None
    gate_satisfied: bool = True


@dataclass
class IntentState:
    """Mutable accumulator for replaying events against an intent."""

    intent_id: str
    current_state: State | None = None
    base_commit: str | None = None
    branch: str | None = None
    pre_block_state: State | None = None
    validated_at_commit: str | None = None
    reviewed_at_commit: str | None = None
    started_as_fast_path: bool = False
    eligible_at_commit: str | None = None
    artifacts_created: set[str] = field(default_factory=set)
    event_count: int = 0


def reduce(intent: IntentState, event: StateEvent) -> ReducerResult:
    violations: list[str] = []

    from_state = intent.current_state
    to_state = event.next_state

    # 1. Validate the declared transition matches what the event claims
    if event.previous_state != from_state:
        violations.append(
            f"Event previous_state '{event.previous_state}' does not match "
            f"current state '{from_state}'"
        )

    # 2. Validate the transition is legal and next_state matches truth table
    expected = resolve_transition(from_state, event.event_type)
    if expected is None and event.event_type != EventType.unblocked:
        violations.append(f"No valid transition from '{from_state}' via '{event.event_type}'")
    elif expected is not None and to_state != expected:
        violations.append(
            f"Event declares next_state '{to_state}' but truth table says '{expected}'"
        )

    # 3. Handle unblocked: restore pre-block state
    if event.event_type == EventType.unblocked:
        if from_state != State.Blocked:
            violations.append("'unblocked' event requires current state 'Blocked'")
        if intent.pre_block_state is None:
            violations.append("No pre-block state recorded to restore")
        elif to_state != intent.pre_block_state:
            violations.append(
                f"Unblock should restore to '{intent.pre_block_state}', not '{to_state}'"
            )

    # 4. Handle blocked: record pre-block state
    if event.event_type == EventType.blocked and from_state is not None and from_state.is_terminal:
        violations.append(f"Cannot block a terminal state '{from_state}'")

    # 5. Validate authority role
    if not is_role_permitted(event.authority_role, from_state, to_state):
        violations.append(
            f"Role '{event.authority_role}' not permitted for "
            f"transition '{from_state}' → '{to_state}'"
        )

    # 6. Check gate artifact requirement
    gate = required_artifact(from_state, to_state, event.event_type)
    gate_satisfied = True
    if gate is not None:
        created = set(event.artifact_refs.created)
        has_gate = any(ref.startswith(gate.value + ":") for ref in created)
        if not has_gate:
            gate_satisfied = False
            violations.append(f"Gate artifact '{gate}' not found in created refs: {created}")

    # 7. Staleness check for integration events
    if event.event_type in (EventType.integration_started, EventType.integration_completed):
        if intent.validated_at_commit is None:
            violations.append("No validation base commit recorded — cannot verify freshness")
        elif intent.validated_at_commit != event.base_commit:
            violations.append(
                f"Validation was against commit '{intent.validated_at_commit}', "
                f"current is '{event.base_commit}' — rebase required"
            )
        if intent.reviewed_at_commit is None:
            violations.append("No review base commit recorded — cannot verify freshness")
        elif intent.reviewed_at_commit != event.base_commit:
            violations.append(
                f"Review was against commit '{intent.reviewed_at_commit}', "
                f"current is '{event.base_commit}' — rebase required"
            )

    # 8. Fast-path authorization requires current eligibility
    if event.event_type == EventType.fast_path_authorized:
        if not intent.started_as_fast_path:
            violations.append("Intent was never classified as fast-path eligible")
        elif intent.eligible_at_commit is None:
            violations.append(
                "Fast-path eligibility was revoked (rework or rebase occurred) — use normal review"
            )
        elif intent.eligible_at_commit != event.base_commit:
            violations.append(
                f"Eligibility was assessed against commit "
                f"'{intent.eligible_at_commit}', current is "
                f"'{event.base_commit}' — reclassification required"
            )

    return ReducerResult(
        new_state=to_state,
        valid=len(violations) == 0,
        violations=tuple(violations),
        gate_artifact=gate,
        gate_satisfied=gate_satisfied,
    )


def apply(intent: IntentState, event: StateEvent) -> ReducerResult:
    """Reduce and apply: updates intent state if valid, returns result either way."""
    result = reduce(intent, event)

    if result.valid:
        if event.event_type == EventType.blocked:
            intent.pre_block_state = intent.current_state
        elif event.event_type == EventType.unblocked:
            intent.pre_block_state = None

        # Track gate-commit freshness — set BEFORE clear so that transitions
        # like NeedsRevalidate→NeedsRereview can set validated_at_commit while
        # the clearing block below only clears reviewed_at_commit.
        if intent.current_state == State.Validating and result.new_state in (
            State.AwaitingReview,
            State.OverridePending,
        ):
            intent.validated_at_commit = event.base_commit
        if intent.current_state == State.AwaitingReview and result.new_state == State.Integrating:
            intent.reviewed_at_commit = event.base_commit
        if (
            intent.current_state == State.NeedsRevalidate
            and result.new_state == State.NeedsRereview
        ):
            intent.validated_at_commit = event.base_commit
        if intent.current_state == State.NeedsRereview and result.new_state == State.Integrating:
            intent.reviewed_at_commit = event.base_commit

        # Track fast-path eligibility
        if event.event_type == EventType.fast_path_eligible:
            intent.started_as_fast_path = True
            intent.eligible_at_commit = event.base_commit

        # Revoke eligibility on rework (re-entering Building from any non-Clarifying state)
        # Excludes build_started which is a progress self-transition, not rework
        if (
            result.new_state == State.Building
            and intent.current_state is not None
            and intent.current_state != State.Clarifying
            and event.event_type != EventType.build_started
        ):
            intent.eligible_at_commit = None

        # Clear stale commits on rebase/revalidate entry
        if result.new_state == State.NeedsRebase:
            intent.validated_at_commit = None
            intent.reviewed_at_commit = None
            intent.eligible_at_commit = None
        elif result.new_state in (State.NeedsRevalidate, State.NeedsRereview):
            intent.reviewed_at_commit = None

        intent.current_state = result.new_state
        intent.base_commit = event.base_commit
        intent.branch = event.branch
        intent.artifacts_created.update(event.artifact_refs.created)
        intent.event_count += 1

    return result
