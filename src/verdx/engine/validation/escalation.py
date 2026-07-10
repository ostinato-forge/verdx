"""Escalation policy: structured decision table evaluation.

Pure functions over immutable data. No I/O, no eval, no dynamic execution.
Policy is validated at load time; evaluation is a closed pattern match.

Data types (EscalationPolicy, EscalationRule, etc.) live in
contracts/escalation.py — this module handles evaluation only.
"""

from __future__ import annotations

from verdx.contracts.dimensions import DimLevel, EscalationLevel
from verdx.contracts.escalation import (
    VALID_QUANTIFIERS,
    EscalationFloor,
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

__all__ = [
    "VALID_QUANTIFIERS",
    "EscalationFloor",
    "EscalationPolicy",
    "EscalationRule",
    "RuleMatch",
    "evaluate",
]

_ESCALATION_ORDER = {level: i for i, level in enumerate(EscalationLevel)}


def _max_escalation(a: EscalationLevel, b: EscalationLevel) -> EscalationLevel:
    """Return the higher of two escalation levels."""
    if _ESCALATION_ORDER[a] >= _ESCALATION_ORDER[b]:
        return a
    return b


def _match_applies(match: RuleMatch, dimensions: dict[str, DimLevel]) -> bool:
    """Evaluate whether a rule match clause applies to given dimensions."""
    if match.dimension == "*":
        targets = list(dimensions.values())
    else:
        val = dimensions.get(match.dimension)
        targets = [val] if val is not None else []

    dim_order = {level: i for i, level in enumerate(DimLevel)}
    match_threshold = dim_order[match.level]

    matching = sum(1 for v in targets if dim_order[v] >= match_threshold)
    total = len(targets)

    if match.quantifier == "any":
        return matching >= 1
    if match.quantifier == "all":
        return total > 0 and matching == total
    if match.threshold is None:
        msg = "count quantifier requires threshold"
        raise ValueError(msg)
    return matching >= match.threshold


def evaluate(
    policy: EscalationPolicy,
    category: str,
    dimensions: dict[str, DimLevel],
) -> EscalationLevel:
    """Evaluate escalation level for an observation's dimensions.

    Pure function. Evaluation order:
    1. Walk rules in order; first match produces base escalation.
    2. If no rule matches, raise ValueError (policy should have a fallback).
    3. Apply category floor (can only raise, never lower).
    4. Return final escalation level.
    """
    base: EscalationLevel | None = None
    for rule in policy.rules:
        if _match_applies(rule.match, dimensions):
            base = rule.level
            break

    if base is None:
        raise ValueError(
            f"no escalation rule matched dimensions {dimensions!r} -- "
            "policy should include a low-risk fallback rule"
        )

    for floor in policy.floors:
        if floor.category == category:
            base = _max_escalation(base, floor.minimum)

    return base
