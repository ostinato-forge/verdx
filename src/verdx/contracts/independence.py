"""Independence record for Four-Eyes Principle provenance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndependenceRecord:
    producer_role: str
    validator_role: str
    actor_kind: str
    relation: str
    policy_id: str
    result: str

    def __post_init__(self) -> None:
        _check_non_empty(self.producer_role, "producer_role")
        _check_non_empty(self.validator_role, "validator_role")
        _check_value(self.actor_kind, ("human", "agent", "system"), "actor_kind")
        _check_value(self.relation, ("independent", "same-team", "self", "unknown"), "relation")
        _check_non_empty(self.policy_id, "policy_id")
        _check_value(self.result, ("satisfied", "waived", "unmet"), "result")

    def to_dict(self) -> dict[str, str]:
        return {
            "producer_role": self.producer_role,
            "validator_role": self.validator_role,
            "actor_kind": self.actor_kind,
            "relation": self.relation,
            "policy_id": self.policy_id,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> IndependenceRecord:
        return cls(
            producer_role=data["producer_role"],
            validator_role=data["validator_role"],
            actor_kind=data["actor_kind"],
            relation=data["relation"],
            policy_id=data["policy_id"],
            result=data["result"],
        )


def _check_non_empty(value: str, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _check_value(value: str, allowed: tuple[str, ...], field: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {allowed}, got {value!r}")
