"""Pure Four-Eyes independence policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from verdx.contracts.independence import IndependenceRecord

FOUR_EYES_POLICY_ID = "verdx-v1"


@dataclass(frozen=True, slots=True)
class ActorFacts:
    """Role-level actor metadata resolved by the runner/store layer."""

    role: str
    kind: str
    team: str | None = None
    opaque_id: str | None = None

    def __post_init__(self) -> None:
        _check_non_empty(self.role, "role")
        _check_value(self.kind, ("human", "agent", "system"), "kind")
        if self.team is not None:
            _check_non_empty(self.team, "team")
        if self.opaque_id is not None:
            _check_non_empty(self.opaque_id, "opaque_id")


def evaluate_independence(
    *,
    producer_role: str,
    validator_role: str,
    producer_actor: ActorFacts | None = None,
    validator_actor: ActorFacts | None = None,
    policy_id: str = FOUR_EYES_POLICY_ID,
) -> IndependenceRecord:
    """Evaluate Four-Eyes independence from explicit role and actor facts.

    When no actor facts are provided, v1 preserves the original role-only
    behavior for backward compatibility. Once either side provides actor facts,
    missing counterpart metadata is treated as unknown rather than satisfied.
    """
    _check_non_empty(producer_role, "producer_role")
    _check_non_empty(validator_role, "validator_role")
    _check_non_empty(policy_id, "policy_id")

    actor_kind = producer_actor.kind if producer_actor is not None else "agent"

    if producer_actor is None and validator_actor is None:
        relation = "independent" if producer_role != validator_role else "self"
        result = "satisfied" if relation == "independent" else "unmet"
        return IndependenceRecord(
            producer_role=producer_role,
            validator_role=validator_role,
            actor_kind=actor_kind,
            relation=relation,
            policy_id=policy_id,
            result=result,
        )

    if producer_actor is None or validator_actor is None:
        return _record(
            producer_role,
            validator_role,
            actor_kind,
            "unknown",
            "unmet",
            policy_id,
        )

    if _same_actor(producer_actor, validator_actor) or producer_role == validator_role:
        return _record(
            producer_role,
            validator_role,
            actor_kind,
            "self",
            "unmet",
            policy_id,
        )

    if producer_actor.team is None or validator_actor.team is None:
        return _record(
            producer_role,
            validator_role,
            actor_kind,
            "unknown",
            "unmet",
            policy_id,
        )

    if producer_actor.team == validator_actor.team:
        return _record(
            producer_role,
            validator_role,
            actor_kind,
            "same-team",
            "unmet",
            policy_id,
        )

    return _record(
        producer_role,
        validator_role,
        actor_kind,
        "independent",
        "satisfied",
        policy_id,
    )


def _same_actor(producer_actor: ActorFacts, validator_actor: ActorFacts) -> bool:
    if producer_actor.opaque_id is not None and validator_actor.opaque_id is not None:
        return producer_actor.opaque_id == validator_actor.opaque_id
    return producer_actor.role == validator_actor.role


def _record(
    producer_role: str,
    validator_role: str,
    actor_kind: str,
    relation: str,
    result: str,
    policy_id: str,
) -> IndependenceRecord:
    return IndependenceRecord(
        producer_role=producer_role,
        validator_role=validator_role,
        actor_kind=actor_kind,
        relation=relation,
        policy_id=policy_id,
        result=result,
    )


def _check_non_empty(value: str, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _check_value(value: str, allowed: tuple[str, ...], field: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {allowed}, got {value!r}")
