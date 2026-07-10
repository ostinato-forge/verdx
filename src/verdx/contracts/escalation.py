"""Escalation policy data types.

Pure frozen dataclasses defining escalation rules and policies.
Evaluation logic lives in validation/escalation.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from verdx.contracts.dimensions import DimLevel, EscalationLevel

VALID_QUANTIFIERS = frozenset({"any", "all", "count"})


@dataclass(frozen=True, slots=True)
class RuleMatch:
    quantifier: str
    dimension: str
    level: DimLevel
    threshold: int | None = None

    def __post_init__(self) -> None:
        if self.quantifier not in VALID_QUANTIFIERS:
            raise ValueError(
                f"quantifier must be one of {sorted(VALID_QUANTIFIERS)}, got {self.quantifier!r}"
            )
        if not self.dimension:
            raise ValueError("dimension must be '*' or a non-empty string")
        if self.quantifier == "count":
            if self.threshold is None:
                raise ValueError("threshold is required when quantifier is 'count'")
            if self.threshold < 1:
                raise ValueError("threshold must be >= 1")
        else:
            if self.threshold is not None:
                raise ValueError(f"threshold is invalid for quantifier {self.quantifier!r}")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "quantifier": self.quantifier,
            "dimension": self.dimension,
            "level": self.level.value,
        }
        if self.threshold is not None:
            result["threshold"] = self.threshold
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleMatch:
        return cls(
            quantifier=data["quantifier"],
            dimension=data["dimension"],
            level=DimLevel(data["level"]),
            threshold=data.get("threshold"),
        )


@dataclass(frozen=True, slots=True)
class EscalationRule:
    name: str
    match: RuleMatch
    level: EscalationLevel

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("rule name must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "match": self.match.to_dict(),
            "level": self.level.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationRule:
        return cls(
            name=data["name"],
            match=RuleMatch.from_dict(data["match"]),
            level=EscalationLevel(data["level"]),
        )


@dataclass(frozen=True, slots=True)
class EscalationFloor:
    category: str
    minimum: EscalationLevel

    def __post_init__(self) -> None:
        if not self.category:
            raise ValueError("floor category must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "minimum": self.minimum.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationFloor:
        return cls(
            category=data["category"],
            minimum=EscalationLevel(data["minimum"]),
        )


@dataclass(frozen=True, slots=True)
class EscalationPolicy:
    version: int
    floors: tuple[EscalationFloor, ...]
    rules: tuple[EscalationRule, ...]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("policy version must be >= 1")
        if not self.rules:
            raise ValueError("policy must include at least one rule")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "floors": [f.to_dict() for f in self.floors],
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationPolicy:
        return cls(
            version=data["version"],
            floors=tuple(EscalationFloor.from_dict(f) for f in data.get("floors", [])),
            rules=tuple(EscalationRule.from_dict(r) for r in data["rules"]),
        )
