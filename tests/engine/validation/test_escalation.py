"""Tests for escalation policy models and evaluation.

Covers the implementation plan's Phase 2 test matrix:
- Dimension-based escalation (all LOW→L0 through BLOCKING→L4)
- Category floors (raise only, never lower)
- Policy validation (malformed configs caught at load time)
- No eval or string parsing path
"""

from __future__ import annotations

import pytest

from verdx.contracts.dimensions import DimLevel, EscalationLevel
from verdx.engine.validation.escalation import (
    EscalationFloor,
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
    evaluate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_policy(**overrides) -> EscalationPolicy:
    """The reference policy from the design spec."""
    defaults = {
        "version": 1,
        "floors": (),
        "rules": (
            EscalationRule(
                name="blocking",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.BLOCKING),
                level=EscalationLevel.L4,
            ),
            EscalationRule(
                name="high-risk",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
                level=EscalationLevel.L3,
            ),
            EscalationRule(
                name="multi-medium",
                match=RuleMatch(
                    quantifier="count",
                    dimension="*",
                    level=DimLevel.MEDIUM,
                    threshold=2,
                ),
                level=EscalationLevel.L2,
            ),
            EscalationRule(
                name="medium-risk",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.MEDIUM),
                level=EscalationLevel.L1,
            ),
            EscalationRule(
                name="low-risk",
                match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                level=EscalationLevel.L0,
            ),
        ),
    }
    return EscalationPolicy(**(defaults | overrides))


def _policy_with_floor(category: str, minimum: EscalationLevel) -> EscalationPolicy:
    return _default_policy(floors=(EscalationFloor(category=category, minimum=minimum),))


# ===========================================================================
# RuleMatch validation
# ===========================================================================


class TestRuleMatch:
    def test_valid_any(self):
        m = RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH)
        assert m.quantifier == "any"
        assert m.threshold is None

    def test_valid_all(self):
        m = RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW)
        assert m.quantifier == "all"

    def test_valid_count_with_threshold(self):
        m = RuleMatch(quantifier="count", dimension="*", level=DimLevel.MEDIUM, threshold=2)
        assert m.threshold == 2

    def test_valid_specific_dimension(self):
        m = RuleMatch(quantifier="any", dimension="correctness", level=DimLevel.HIGH)
        assert m.dimension == "correctness"

    def test_rejects_invalid_quantifier(self):
        with pytest.raises(ValueError, match="quantifier"):
            RuleMatch(quantifier="some", dimension="*", level=DimLevel.HIGH)

    def test_rejects_empty_dimension(self):
        with pytest.raises(ValueError, match="dimension"):
            RuleMatch(quantifier="any", dimension="", level=DimLevel.HIGH)

    def test_count_requires_threshold(self):
        with pytest.raises(ValueError, match="threshold is required"):
            RuleMatch(quantifier="count", dimension="*", level=DimLevel.MEDIUM)

    def test_count_rejects_zero_threshold(self):
        with pytest.raises(ValueError, match="threshold must be >= 1"):
            RuleMatch(quantifier="count", dimension="*", level=DimLevel.MEDIUM, threshold=0)

    def test_any_rejects_threshold(self):
        with pytest.raises(ValueError, match="threshold is invalid"):
            RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH, threshold=2)

    def test_all_rejects_threshold(self):
        with pytest.raises(ValueError, match="threshold is invalid"):
            RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW, threshold=1)

    def test_frozen(self):
        m = RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH)
        with pytest.raises(AttributeError):
            m.quantifier = "all"  # type: ignore[misc]

    def test_round_trip(self):
        m = RuleMatch(quantifier="count", dimension="*", level=DimLevel.MEDIUM, threshold=3)
        restored = RuleMatch.from_dict(m.to_dict())
        assert restored == m

    def test_round_trip_no_threshold(self):
        m = RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH)
        d = m.to_dict()
        assert "threshold" not in d
        restored = RuleMatch.from_dict(d)
        assert restored == m


# ===========================================================================
# EscalationRule validation
# ===========================================================================


class TestEscalationRule:
    def test_construction(self):
        r = EscalationRule(
            name="test",
            match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
            level=EscalationLevel.L3,
        )
        assert r.name == "test"
        assert r.level == EscalationLevel.L3

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name"):
            EscalationRule(
                name="",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
                level=EscalationLevel.L3,
            )

    def test_round_trip(self):
        r = EscalationRule(
            name="high-risk",
            match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
            level=EscalationLevel.L3,
        )
        restored = EscalationRule.from_dict(r.to_dict())
        assert restored == r


# ===========================================================================
# EscalationFloor validation
# ===========================================================================


class TestEscalationFloor:
    def test_construction(self):
        f = EscalationFloor(category="security", minimum=EscalationLevel.L3)
        assert f.category == "security"
        assert f.minimum == EscalationLevel.L3

    def test_rejects_empty_category(self):
        with pytest.raises(ValueError, match="category"):
            EscalationFloor(category="", minimum=EscalationLevel.L3)

    def test_round_trip(self):
        f = EscalationFloor(category="security", minimum=EscalationLevel.L4)
        restored = EscalationFloor.from_dict(f.to_dict())
        assert restored == f


# ===========================================================================
# EscalationPolicy validation
# ===========================================================================


class TestEscalationPolicy:
    def test_construction(self):
        p = _default_policy()
        assert p.version == 1
        assert len(p.rules) == 5

    def test_rejects_zero_version(self):
        with pytest.raises(ValueError, match="version"):
            _default_policy(version=0)

    def test_rejects_no_rules(self):
        with pytest.raises(ValueError, match="at least one rule"):
            EscalationPolicy(version=1, floors=(), rules=())

    def test_round_trip(self):
        p = _default_policy(
            floors=(EscalationFloor(category="security", minimum=EscalationLevel.L3),)
        )
        restored = EscalationPolicy.from_dict(p.to_dict())
        assert restored == p

    def test_frozen(self):
        p = _default_policy()
        with pytest.raises(AttributeError):
            p.version = 2  # type: ignore[misc]


# ===========================================================================
# Escalation evaluation — dimension rules
# ===========================================================================


class TestEscalationEvaluation:
    """Core dimension-based escalation with the default policy."""

    def test_all_low_gives_l0(self):
        dims = {"correctness": DimLevel.LOW, "completeness": DimLevel.LOW}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L0

    def test_single_low_gives_l0(self):
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L0

    def test_one_medium_gives_l1(self):
        dims = {"correctness": DimLevel.MEDIUM}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L1

    def test_medium_among_lows_gives_l1(self):
        dims = {
            "correctness": DimLevel.LOW,
            "completeness": DimLevel.MEDIUM,
            "style": DimLevel.LOW,
        }
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L1

    def test_two_medium_gives_l2(self):
        dims = {"correctness": DimLevel.MEDIUM, "completeness": DimLevel.MEDIUM}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L2

    def test_three_medium_gives_l2(self):
        dims = {
            "correctness": DimLevel.MEDIUM,
            "completeness": DimLevel.MEDIUM,
            "style": DimLevel.MEDIUM,
        }
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L2

    def test_one_high_gives_l3(self):
        dims = {"correctness": DimLevel.HIGH}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L3

    def test_high_among_mediums_gives_l3(self):
        dims = {
            "correctness": DimLevel.HIGH,
            "completeness": DimLevel.MEDIUM,
            "style": DimLevel.MEDIUM,
        }
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L3

    def test_one_blocking_gives_l4(self):
        dims = {"correctness": DimLevel.BLOCKING}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L4

    def test_blocking_among_highs_gives_l4(self):
        dims = {"correctness": DimLevel.BLOCKING, "completeness": DimLevel.HIGH}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L4

    def test_high_counts_as_at_least_medium_for_count(self):
        """HIGH >= MEDIUM, so two HIGHs should also trigger multi-medium (L2).
        But HIGH also triggers high-risk (L3), which comes first."""
        dims = {"correctness": DimLevel.HIGH, "completeness": DimLevel.HIGH}
        assert evaluate(_default_policy(), "general", dims) == EscalationLevel.L3


# ===========================================================================
# Escalation evaluation — category floors
# ===========================================================================


class TestEscalationFloors:
    """Category floors can only raise escalation, never lower it."""

    def test_floor_raises_l0_to_l3(self):
        policy = _policy_with_floor("security", EscalationLevel.L3)
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "security", dims) == EscalationLevel.L3

    def test_floor_raises_l1_to_l4(self):
        policy = _policy_with_floor("compliance", EscalationLevel.L4)
        dims = {"correctness": DimLevel.MEDIUM}
        assert evaluate(policy, "compliance", dims) == EscalationLevel.L4

    def test_floor_raises_l1_to_l3(self):
        policy = _policy_with_floor("security", EscalationLevel.L3)
        dims = {"correctness": DimLevel.MEDIUM}
        assert evaluate(policy, "security", dims) == EscalationLevel.L3

    def test_floor_does_not_lower_l4(self):
        policy = _policy_with_floor("general", EscalationLevel.L2)
        dims = {"correctness": DimLevel.BLOCKING}
        assert evaluate(policy, "general", dims) == EscalationLevel.L4

    def test_floor_does_not_lower_l3(self):
        policy = _policy_with_floor("general", EscalationLevel.L1)
        dims = {"correctness": DimLevel.HIGH}
        assert evaluate(policy, "general", dims) == EscalationLevel.L3

    def test_floor_only_applies_to_matching_category(self):
        policy = _policy_with_floor("security", EscalationLevel.L3)
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "general", dims) == EscalationLevel.L0

    def test_multiple_floors(self):
        policy = _default_policy(
            floors=(
                EscalationFloor(category="security", minimum=EscalationLevel.L3),
                EscalationFloor(category="compliance", minimum=EscalationLevel.L4),
            )
        )
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "security", dims) == EscalationLevel.L3
        assert evaluate(policy, "compliance", dims) == EscalationLevel.L4
        assert evaluate(policy, "general", dims) == EscalationLevel.L0


# ===========================================================================
# Escalation evaluation — specific dimension matching
# ===========================================================================


class TestSpecificDimensionMatching:
    """Rules can target a specific dimension by name, not just '*'."""

    def test_specific_dimension_matches(self):
        policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="security-high",
                    match=RuleMatch(quantifier="any", dimension="security", level=DimLevel.HIGH),
                    level=EscalationLevel.L4,
                ),
                EscalationRule(
                    name="fallback",
                    match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
            ),
        )
        dims = {"security": DimLevel.HIGH, "correctness": DimLevel.LOW}
        assert evaluate(policy, "general", dims) == EscalationLevel.L4

    def test_specific_dimension_does_not_match_other_dimensions(self):
        policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="security-high",
                    match=RuleMatch(quantifier="any", dimension="security", level=DimLevel.HIGH),
                    level=EscalationLevel.L4,
                ),
                EscalationRule(
                    name="fallback",
                    match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
            ),
        )
        dims = {"correctness": DimLevel.HIGH}
        assert evaluate(policy, "general", dims) == EscalationLevel.L0

    def test_specific_dimension_missing_does_not_match(self):
        policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="security-high",
                    match=RuleMatch(quantifier="any", dimension="security", level=DimLevel.HIGH),
                    level=EscalationLevel.L4,
                ),
                EscalationRule(
                    name="fallback",
                    match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
            ),
        )
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "general", dims) == EscalationLevel.L0


# ===========================================================================
# Escalation evaluation — edge cases
# ===========================================================================


class TestEscalationEdgeCases:
    def test_no_matching_rule_raises(self):
        policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="only-blocking",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.BLOCKING),
                    level=EscalationLevel.L4,
                ),
            ),
        )
        dims = {"correctness": DimLevel.LOW}
        with pytest.raises(ValueError, match="no escalation rule matched"):
            evaluate(policy, "general", dims)

    def test_first_matching_rule_wins(self):
        """Rules are evaluated in order; the first match determines base level."""
        policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="conservative",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L3,
                ),
                EscalationRule(
                    name="permissive",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
            ),
        )
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "general", dims) == EscalationLevel.L3

    def test_evaluate_is_deterministic(self):
        """Same inputs always produce same output."""
        policy = _default_policy()
        dims = {"a": DimLevel.MEDIUM, "b": DimLevel.HIGH}
        results = [evaluate(policy, "general", dims) for _ in range(100)]
        assert all(r == EscalationLevel.L3 for r in results)

    def test_all_quantifier_with_empty_targets_does_not_match(self):
        """'all' with dimension=specific and no matching dimension → no match."""
        policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="all-security-low",
                    match=RuleMatch(quantifier="all", dimension="security", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
                EscalationRule(
                    name="fallback",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L1,
                ),
            ),
        )
        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "general", dims) == EscalationLevel.L1


# ===========================================================================
# No eval / no dynamic execution
# ===========================================================================


class TestNoEval:
    """Structural proof that no eval or dynamic code path exists."""

    def test_rule_match_has_no_string_condition(self):
        """RuleMatch uses typed fields, not a condition string."""
        fields = {f.name for f in RuleMatch.__dataclass_fields__.values()}
        assert "condition" not in fields
        assert "expression" not in fields
        assert "code" not in fields

    def test_escalation_rule_has_no_callback(self):
        fields = {f.name for f in EscalationRule.__dataclass_fields__.values()}
        assert "callback" not in fields
        assert "handler" not in fields
        assert "func" not in fields

    def test_quantifiers_are_closed_set(self):
        from verdx.engine.validation.escalation import VALID_QUANTIFIERS

        assert {"any", "all", "count"} == VALID_QUANTIFIERS
