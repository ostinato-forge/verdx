"""Tests for scoring, coverage, confidence, and improvement computation."""

from __future__ import annotations

from tests.contracts.test_models import (
    _make_applied_disposition,
    _make_coverage_gap,
    _make_independence,
    _make_observation,
    _make_recovery_item,
    _make_validator_status,
)
from verdx.contracts.assessment import (
    AssessmentConfidence,
    Trend,
)
from verdx.contracts.config import ScoringConfig
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.engine.validation.scoring import (
    compute_confidence,
    compute_coverage_status,
    compute_escalation_summary,
    compute_improvement,
    compute_positive_coverage,
    compute_score,
    coverage_gaps_from_statuses,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obs(**overrides):
    return _make_observation(**overrides)


def _vs(**overrides):
    return _make_validator_status(**overrides)


# ===========================================================================
# Coverage status
# ===========================================================================


class TestCoverageStatus:
    def test_all_completed_is_full(self):
        statuses = [_vs(status="completed"), _vs(status="completed")]
        assert compute_coverage_status(statuses, frozenset()) == CoverageStatus.FULL

    def test_failed_critical_is_degraded(self):
        statuses = [
            _vs(validator_id="critical-v", status="failed", error="crash"),
        ]
        assert (
            compute_coverage_status(statuses, frozenset({"critical-v"})) == CoverageStatus.DEGRADED
        )

    def test_skipped_critical_is_degraded(self):
        statuses = [
            _vs(
                validator_id="critical-v",
                status="skipped",
                skip_reason="not applicable",
                checks_run=0,
                checks_passed=0,
                observation_count=0,
                duration_ms=0,
            ),
        ]
        assert (
            compute_coverage_status(statuses, frozenset({"critical-v"})) == CoverageStatus.DEGRADED
        )

    def test_failed_non_critical_is_partial(self):
        statuses = [
            _vs(validator_id="optional-v", status="failed", error="crash"),
        ]
        assert compute_coverage_status(statuses, frozenset()) == CoverageStatus.PARTIAL

    def test_skipped_non_critical_is_partial(self):
        statuses = [
            _vs(
                validator_id="optional-v",
                status="skipped",
                skip_reason="not needed",
                checks_run=0,
                checks_passed=0,
                observation_count=0,
                duration_ms=0,
            ),
        ]
        assert compute_coverage_status(statuses, frozenset()) == CoverageStatus.PARTIAL

    def test_critical_failure_overrides_non_critical(self):
        statuses = [
            _vs(validator_id="critical-v", status="failed", error="crash"),
            _vs(validator_id="optional-v", status="failed", error="crash"),
        ]
        assert (
            compute_coverage_status(statuses, frozenset({"critical-v"})) == CoverageStatus.DEGRADED
        )

    def test_empty_statuses_is_full(self):
        assert compute_coverage_status([], frozenset()) == CoverageStatus.FULL


# ===========================================================================
# Confidence
# ===========================================================================


class TestConfidence:
    def test_full_no_observations_no_gaps_is_strong(self):
        assert compute_confidence(CoverageStatus.FULL, [], [], []) == AssessmentConfidence.STRONG

    def test_full_with_observations_is_guarded(self):
        assert (
            compute_confidence(CoverageStatus.FULL, [_obs()], [], [])
            == AssessmentConfidence.GUARDED
        )

    def test_full_with_gaps_is_guarded(self):
        gap = _make_coverage_gap()
        assert (
            compute_confidence(CoverageStatus.FULL, [], [gap], []) == AssessmentConfidence.GUARDED
        )

    def test_degraded_is_low(self):
        assert compute_confidence(CoverageStatus.DEGRADED, [], [], []) == AssessmentConfidence.LOW

    def test_partial_is_low(self):
        assert compute_confidence(CoverageStatus.PARTIAL, [], [], []) == AssessmentConfidence.LOW

    def test_blocking_recovery_item_is_low(self):
        blocker = _make_recovery_item(kind="blocked")
        assert (
            compute_confidence(CoverageStatus.FULL, [], [], [blocker]) == AssessmentConfidence.LOW
        )

    def test_retryable_recovery_item_not_low(self):
        retryable = _make_recovery_item(kind="retryable")
        assert (
            compute_confidence(CoverageStatus.FULL, [], [], [retryable])
            == AssessmentConfidence.STRONG
        )

    def test_partial_zero_observations_is_not_clean(self):
        """A partial run with zero observations is NOT clean."""
        result = compute_confidence(CoverageStatus.PARTIAL, [], [], [])
        assert result != AssessmentConfidence.STRONG


# ===========================================================================
# Positive coverage
# ===========================================================================


class TestPositiveCoverage:
    def test_sums_checks_run(self):
        statuses = [
            _vs(checks_run=10, checks_passed=8),
            _vs(checks_run=5, checks_passed=5),
        ]
        checks_run, checks_passed, _validators_clean = compute_positive_coverage(statuses)
        assert checks_run == 15
        assert checks_passed == 13

    def test_validators_clean_counts_zero_observation_completed(self):
        statuses = [
            _vs(status="completed", observation_count=0),
            _vs(status="completed", observation_count=2),
            _vs(status="failed", observation_count=0, error="crash"),
        ]
        _, _, validators_clean = compute_positive_coverage(statuses)
        assert validators_clean == 1

    def test_empty_statuses(self):
        checks_run, checks_passed, validators_clean = compute_positive_coverage([])
        assert checks_run == 0
        assert checks_passed == 0
        assert validators_clean == 0

    def test_clean_not_inferred_from_missing_statuses(self):
        """validators_clean is computed from actual statuses, not absence."""
        statuses = [_vs(status="completed", observation_count=3)]
        _, _, validators_clean = compute_positive_coverage(statuses)
        assert validators_clean == 0


# ===========================================================================
# Scoring
# ===========================================================================


class TestScoring:
    def test_zero_observations_full_coverage_is_100(self):
        score = compute_score([], [_vs()], frozenset())
        assert score.overall == 100
        assert score.coverage_penalty == 0
        assert score.observation_count == 0

    def test_single_high_confirmed_deducts_30(self):
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 70

    def test_single_critical_confirmed_deducts_40(self):
        obs = _obs(severity=Severity.CRITICAL, confidence=Confidence.CONFIRMED)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 60

    def test_single_info_possible_deducts_1(self):
        obs = _obs(severity=Severity.INFO, confidence=Confidence.POSSIBLE)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 99

    def test_different_rules_accumulate_fully(self):
        obs1 = _obs(rule_id="rule-a", severity=Severity.HIGH, confidence=Confidence.CONFIRMED)
        obs2 = _obs(rule_id="rule-b", severity=Severity.MEDIUM, confidence=Confidence.PROBABLE)
        score = compute_score([obs1, obs2], [_vs()], frozenset())
        assert score.overall == 55

    def test_same_rule_diminishing_returns(self):
        obs1 = _obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED)
        obs2 = _obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED)
        obs3 = _obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED)
        score = compute_score([obs1, obs2, obs3], [_vs()], frozenset())
        assert score.overall == 83

    def test_per_rule_cap_limits_noisy_rules(self):
        obs_list = [_obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED) for _ in range(30)]
        score = compute_score(obs_list, [_vs()], frozenset())
        assert score.overall == 75

    def test_per_rule_cap_does_not_affect_few_observations(self):
        obs_list = [_obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED) for _ in range(3)]
        score = compute_score(obs_list, [_vs()], frozenset())
        assert score.overall == 83

    def test_clamps_to_zero(self):
        obs_list = [
            _obs(rule_id=f"rule-{i}", severity=Severity.CRITICAL, confidence=Confidence.CONFIRMED)
            for i in range(5)
        ]
        score = compute_score(obs_list, [_vs()], frozenset())
        assert score.overall == 0

    def test_failed_critical_validator_penalizes_30(self):
        vs = _vs(
            validator_id="critical-v",
            status="failed",
            error="crash",
        )
        score = compute_score([], [vs], frozenset({"critical-v"}))
        assert score.coverage_penalty == 30
        assert score.overall == 70

    def test_skipped_non_critical_validator_penalizes_5(self):
        vs = _vs(
            validator_id="opt-v",
            status="skipped",
            skip_reason="not needed",
            checks_run=0,
            checks_passed=0,
            observation_count=0,
            duration_ms=0,
        )
        score = compute_score([], [vs], frozenset())
        assert score.coverage_penalty == 5
        assert score.overall == 95

    def test_score_never_100_with_coverage_gaps(self):
        vs = _vs(
            validator_id="opt-v",
            status="failed",
            error="crash",
        )
        score = compute_score([], [vs], frozenset())
        assert score.overall < 100
        assert score.coverage_penalty > 0

    def test_escalation_distribution(self):
        obs1 = _obs(escalation=EscalationLevel.L3)
        obs2 = _obs(escalation=EscalationLevel.L3)
        obs3 = _obs(escalation=EscalationLevel.L1)
        score = compute_score([obs1, obs2, obs3], [_vs()], frozenset())
        assert score.escalation_distribution[EscalationLevel.L3] == 2
        assert score.escalation_distribution[EscalationLevel.L1] == 1

    def test_dimension_scores_computed(self):
        obs = _obs(
            severity=Severity.MEDIUM,
            confidence=Confidence.CONFIRMED,
            dimensions={"correctness": DimLevel.HIGH, "style": DimLevel.LOW},
        )
        score = compute_score([obs], [_vs()], frozenset())
        assert "correctness" in score.dimension_scores
        assert "style" in score.dimension_scores
        assert score.dimension_scores["correctness"].observation_count == 1

    def test_comparison_computed_when_previous_score(self):
        score = compute_score([], [_vs()], frozenset(), previous_score=85)
        assert score.comparison is not None
        assert score.comparison.previous_score == 85
        assert score.comparison.delta == 15

    def test_no_comparison_without_previous_score(self):
        score = compute_score([], [_vs()], frozenset())
        assert score.comparison is None


# ===========================================================================
# Escalation summary
# ===========================================================================


class TestEscalationSummary:
    def test_empty_observations(self):
        summary = compute_escalation_summary([])
        assert summary.counts == {}
        assert summary.highest == EscalationLevel.L0

    def test_single_observation(self):
        summary = compute_escalation_summary([_obs(escalation=EscalationLevel.L3)])
        assert summary.counts[EscalationLevel.L3] == 1
        assert summary.highest == EscalationLevel.L3

    def test_multiple_observations(self):
        observations = [
            _obs(escalation=EscalationLevel.L1),
            _obs(escalation=EscalationLevel.L3),
            _obs(escalation=EscalationLevel.L4),
            _obs(escalation=EscalationLevel.L3),
        ]
        summary = compute_escalation_summary(observations)
        assert summary.highest == EscalationLevel.L4
        assert summary.counts[EscalationLevel.L3] == 2
        assert summary.counts[EscalationLevel.L4] == 1
        assert summary.counts[EscalationLevel.L1] == 1

    def test_l3_l4_appear_in_summary(self):
        """Observations with L3/L4 must appear in escalation summary."""
        observations = [
            _obs(escalation=EscalationLevel.L3),
            _obs(escalation=EscalationLevel.L4),
        ]
        summary = compute_escalation_summary(observations)
        assert EscalationLevel.L3 in summary.counts
        assert EscalationLevel.L4 in summary.counts


# ===========================================================================
# Improvement
# ===========================================================================


class TestImprovement:
    def test_no_previous_returns_none(self):
        assert compute_improvement({"fp-1"}, None) is None

    def test_all_resolved(self):
        imp = compute_improvement(set(), {"fp-1", "fp-2"})
        assert imp is not None
        assert imp.resolved_count == 2
        assert imp.new_count == 0
        assert imp.persistent_count == 0
        assert imp.trend == Trend.IMPROVING

    def test_all_new(self):
        imp = compute_improvement({"fp-1", "fp-2"}, set())
        assert imp is not None
        assert imp.resolved_count == 0
        assert imp.new_count == 2
        assert imp.persistent_count == 0
        assert imp.new_fingerprints == ("fp-1", "fp-2")
        assert imp.persistent_fingerprints == ()
        assert imp.trend == Trend.REGRESSING

    def test_all_persistent(self):
        fps = {"fp-1", "fp-2"}
        imp = compute_improvement(fps, fps)
        assert imp is not None
        assert imp.resolved_count == 0
        assert imp.new_count == 0
        assert imp.persistent_count == 2
        assert imp.new_fingerprints == ()
        assert imp.persistent_fingerprints == ("fp-1", "fp-2")
        assert imp.trend == Trend.STABLE

    def test_mixed_improving(self):
        imp = compute_improvement(
            {"fp-2", "fp-3"},
            {"fp-1", "fp-2", "fp-4"},
        )
        assert imp is not None
        assert imp.resolved_count == 2
        assert imp.new_count == 1
        assert imp.persistent_count == 1
        assert imp.trend == Trend.IMPROVING

    def test_mixed_regressing(self):
        imp = compute_improvement(
            {"fp-1", "fp-2", "fp-3"},
            {"fp-1"},
        )
        assert imp is not None
        assert imp.resolved_count == 0
        assert imp.new_count == 2
        assert imp.persistent_count == 1
        assert imp.trend == Trend.REGRESSING

    def test_equal_counts_is_stable(self):
        imp = compute_improvement(
            {"fp-1", "fp-new"},
            {"fp-1", "fp-old"},
        )
        assert imp is not None
        assert imp.resolved_count == 1
        assert imp.new_count == 1
        assert imp.trend == Trend.STABLE

    def test_resolved_fingerprints_sorted(self):
        imp = compute_improvement(set(), {"fp-c", "fp-a", "fp-b"})
        assert imp is not None
        assert imp.resolved_fingerprints == ("fp-a", "fp-b", "fp-c")

    def test_new_and_persistent_fingerprints_sorted(self):
        imp = compute_improvement(
            {"fp-new-b", "fp-new-a", "fp-same"},
            {"fp-same", "fp-old"},
        )
        assert imp is not None
        assert imp.new_fingerprints == ("fp-new-a", "fp-new-b")
        assert imp.persistent_fingerprints == ("fp-same",)

    def test_both_empty_is_stable(self):
        imp = compute_improvement(set(), set())
        assert imp is not None
        assert imp.trend == Trend.STABLE


# ===========================================================================
# Coverage gaps from statuses
# ===========================================================================


class TestCoverageGapsFromStatuses:
    def test_completed_produces_no_gaps(self):
        statuses = [_vs(status="completed")]
        assert coverage_gaps_from_statuses(statuses) == []

    def test_failed_produces_gap(self):
        statuses = [
            _vs(
                validator_id="my-lint",
                status="failed",
                error="crash in parser",
            ),
        ]
        gaps = coverage_gaps_from_statuses(statuses)
        assert len(gaps) == 1
        assert gaps[0].validator_id == "my-lint"
        assert gaps[0].reason == "crash in parser"

    def test_skipped_produces_gap(self):
        statuses = [
            _vs(
                validator_id="my-lint",
                status="skipped",
                skip_reason="not applicable",
                checks_run=0,
                checks_passed=0,
                observation_count=0,
                duration_ms=0,
            ),
        ]
        gaps = coverage_gaps_from_statuses(statuses)
        assert len(gaps) == 1
        assert gaps[0].reason == "not applicable"

    def test_dimension_map_attached(self):
        statuses = [
            _vs(validator_id="my-lint", status="failed", error="crash"),
        ]
        dim_map = {"my-lint": ["correctness", "style"]}
        gaps = coverage_gaps_from_statuses(statuses, dim_map)
        assert gaps[0].dimensions_affected == ("correctness", "style")

    def test_no_dimension_map_empty_dims(self):
        statuses = [
            _vs(validator_id="my-lint", status="failed", error="crash"),
        ]
        gaps = coverage_gaps_from_statuses(statuses)
        assert gaps[0].dimensions_affected == ()


# ===========================================================================
# Configurable scoring
# ===========================================================================


class TestScoringConfig:
    def test_none_config_uses_defaults(self):
        """When no config is provided, behavior is identical to hardcoded defaults."""
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED)
        score_default = compute_score([obs], [_vs()], frozenset())
        score_with_none = compute_score([obs], [_vs()], frozenset(), scoring_config=None)
        assert score_default.overall == score_with_none.overall == 70

    def test_custom_severity_penalties(self):
        """Custom severity penalties change the score."""
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED)

        custom_penalties = {
            Severity.HIGH: {
                Confidence.CONFIRMED: 50,
                Confidence.PROBABLE: 22,
                Confidence.POSSIBLE: 15,
            },
            Severity.MEDIUM: {
                Confidence.CONFIRMED: 20,
                Confidence.PROBABLE: 15,
                Confidence.POSSIBLE: 10,
            },
            Severity.LOW: {
                Confidence.CONFIRMED: 10,
                Confidence.PROBABLE: 8,
                Confidence.POSSIBLE: 5,
            },
            Severity.CRITICAL: {
                Confidence.CONFIRMED: 40,
                Confidence.PROBABLE: 30,
                Confidence.POSSIBLE: 20,
            },
            Severity.INFO: {
                Confidence.CONFIRMED: 3,
                Confidence.PROBABLE: 2,
                Confidence.POSSIBLE: 1,
            },
        }

        config = ScoringConfig(severity_penalties=custom_penalties)
        score = compute_score([obs], [_vs()], frozenset(), scoring_config=config)
        assert score.overall == 50

    def test_custom_coverage_penalties(self):
        """Custom coverage penalties change the score."""
        vs = _vs(
            validator_id="critical-v",
            status="failed",
            error="crash",
        )

        custom_coverage = {
            "failed_critical": 50,
            "skipped_critical": 25,
            "failed_non_critical": 10,
            "skipped_non_critical": 5,
            "truncated_high_critical": 3,
        }

        config = ScoringConfig(coverage_penalties=custom_coverage)
        score = compute_score([], [vs], frozenset({"critical-v"}), scoring_config=config)
        assert score.coverage_penalty == 50
        assert score.overall == 50

    def test_custom_decay_factor(self):
        """Custom decay factor changes diminishing returns behavior."""
        obs_list = [_obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED) for _ in range(3)]

        config = ScoringConfig(decay_factor=0.3)
        score = compute_score(obs_list, [_vs()], frozenset(), scoring_config=config)
        assert score.overall > 83

    def test_custom_min_fraction(self):
        """Custom min_fraction changes penalty floor."""
        obs_list = [_obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED) for _ in range(10)]

        config = ScoringConfig(min_fraction=0.5)
        score = compute_score(obs_list, [_vs()], frozenset(), scoring_config=config)
        assert score.overall <= 75

    def test_custom_cap_multiplier(self):
        """Custom cap multiplier changes per-rule cap."""
        obs_list = [_obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED) for _ in range(30)]

        config = ScoringConfig(cap_multiplier=5.0)
        score = compute_score(obs_list, [_vs()], frozenset(), scoring_config=config)
        assert score.overall < 75

    def test_partial_config_falls_back_to_defaults(self):
        """Only some fields set uses defaults for unset fields."""
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED)

        config = ScoringConfig(decay_factor=0.3)
        score = compute_score([obs], [_vs()], frozenset(), scoring_config=config)
        assert score.overall == 70

    def test_partial_severity_penalties_merged_with_defaults(self):
        """Partial severity override only changes specified levels."""
        obs_high = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED)
        obs_low = _obs(severity=Severity.LOW, confidence=Confidence.CONFIRMED)

        partial = {Severity.HIGH: {Confidence.CONFIRMED: 50}}
        config = ScoringConfig(severity_penalties=partial)

        score = compute_score([obs_high], [_vs()], frozenset(), scoring_config=config)
        assert score.overall == 50

        score_low = compute_score([obs_low], [_vs()], frozenset(), scoring_config=config)
        assert score_low.overall == 90

    def test_partial_coverage_penalties_merged_with_defaults(self):
        """Partial coverage override only changes specified keys."""
        vs = _vs(validator_id="crit", status="failed", error="crash")

        config = ScoringConfig(coverage_penalties={"failed_critical": 50})
        score = compute_score([], [vs], frozenset({"crit"}), scoring_config=config)
        assert score.coverage_penalty == 50

    def test_multiple_custom_params_together(self):
        """Multiple custom parameters work together."""
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED)

        custom_penalties = {
            Severity.HIGH: {
                Confidence.CONFIRMED: 25,
                Confidence.PROBABLE: 22,
                Confidence.POSSIBLE: 15,
            },
            Severity.MEDIUM: {
                Confidence.CONFIRMED: 20,
                Confidence.PROBABLE: 15,
                Confidence.POSSIBLE: 10,
            },
            Severity.LOW: {
                Confidence.CONFIRMED: 10,
                Confidence.PROBABLE: 8,
                Confidence.POSSIBLE: 5,
            },
            Severity.CRITICAL: {
                Confidence.CONFIRMED: 40,
                Confidence.PROBABLE: 30,
                Confidence.POSSIBLE: 20,
            },
            Severity.INFO: {
                Confidence.CONFIRMED: 3,
                Confidence.PROBABLE: 2,
                Confidence.POSSIBLE: 1,
            },
        }

        custom_coverage = {
            "failed_critical": 15,
            "skipped_critical": 25,
            "failed_non_critical": 10,
            "skipped_non_critical": 5,
            "truncated_high_critical": 3,
        }

        config = ScoringConfig(
            severity_penalties=custom_penalties,
            coverage_penalties=custom_coverage,
            decay_factor=0.3,
        )

        vs = _vs(validator_id="critical-v", status="failed", error="crash")
        score = compute_score([obs], [vs], frozenset({"critical-v"}), scoring_config=config)
        assert score.overall == 60
        assert score.coverage_penalty == 15


# ===========================================================================
# Disposition scoring
# ===========================================================================


class TestDispositionScoring:
    def test_false_positive_zeroes_penalty(self):
        disp = _make_applied_disposition(decision="false-positive")
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED, disposition=disp)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 100

    def test_accepted_risk_reduces_penalty(self):
        disp = _make_applied_disposition(decision="accepted-risk")
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED, disposition=disp)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall > 70
        assert score.overall < 100

    def test_deferred_keeps_full_penalty(self):
        disp = _make_applied_disposition(decision="deferred", effect="deferred")
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED, disposition=disp)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 70

    def test_context_dependent_keeps_full_penalty(self):
        disp = _make_applied_disposition(decision="context-dependent", effect="reevaluate")
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED, disposition=disp)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 70

    def test_no_disposition_unchanged(self):
        obs = _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED, disposition=None)
        score = compute_score([obs], [_vs()], frozenset())
        assert score.overall == 70

    def test_mixed_dispositioned_and_normal(self):
        disp = _make_applied_disposition(decision="false-positive")
        obs_fp = _obs(
            rule_id="rule-a",
            severity=Severity.HIGH,
            confidence=Confidence.CONFIRMED,
            disposition=disp,
        )
        obs_real = _obs(
            rule_id="rule-b",
            severity=Severity.HIGH,
            confidence=Confidence.CONFIRMED,
            disposition=None,
        )
        score = compute_score([obs_fp, obs_real], [_vs()], frozenset())
        assert score.overall == 70

    def test_multiple_false_positives_all_zeroed(self):
        disp = _make_applied_disposition(decision="false-positive")
        obs_list = [
            _obs(severity=Severity.HIGH, confidence=Confidence.CONFIRMED, disposition=disp)
            for _ in range(5)
        ]
        score = compute_score(obs_list, [_vs()], frozenset())
        assert score.overall == 100


# ===========================================================================
# Independence penalties
# ===========================================================================


class TestIndependencePenalty:
    def test_satisfied_independence_no_penalty(self):
        obs = _obs(independence=_make_independence(result="satisfied"))
        vs = _vs()
        score = compute_score([obs], [vs], frozenset())
        assert score.independence_penalty == 0

    def test_unmet_independence_applies_default_penalty(self):
        obs = _obs(independence=_make_independence(result="unmet", relation="same-team"))
        vs = _vs()
        score = compute_score([obs], [vs], frozenset())
        assert score.independence_penalty == 15

    def test_multiple_obs_same_validator_penalized_once(self):
        obs1 = _obs(
            observation_id="obs-1",
            independence=_make_independence(
                result="unmet",
                validator_role="qa-validator",
            ),
        )
        obs2 = _obs(
            observation_id="obs-2",
            independence=_make_independence(
                result="unmet",
                validator_role="qa-validator",
            ),
        )
        vs = _vs()
        score = compute_score([obs1, obs2], [vs], frozenset())
        assert score.independence_penalty == 15

    def test_two_unmet_validators_penalized_separately(self):
        obs1 = _obs(
            observation_id="obs-1",
            independence=_make_independence(
                result="unmet",
                validator_role="validator-a",
            ),
        )
        obs2 = _obs(
            observation_id="obs-2",
            independence=_make_independence(
                result="unmet",
                validator_role="validator-b",
            ),
        )
        vs = _vs()
        score = compute_score([obs1, obs2], [vs], frozenset())
        assert score.independence_penalty == 30

    def test_custom_independence_penalty_via_config(self):
        obs = _obs(independence=_make_independence(result="unmet"))
        vs = _vs()
        config = ScoringConfig(independence_penalty=25)
        score = compute_score([obs], [vs], frozenset(), scoring_config=config)
        assert score.independence_penalty == 25

    def test_zero_independence_penalty_disables(self):
        obs = _obs(independence=_make_independence(result="unmet"))
        vs = _vs()
        config = ScoringConfig(independence_penalty=0)
        score = compute_score([obs], [vs], frozenset(), scoring_config=config)
        assert score.independence_penalty == 0

    def test_waived_independence_no_penalty(self):
        obs = _obs(independence=_make_independence(result="waived"))
        vs = _vs()
        score = compute_score([obs], [vs], frozenset())
        assert score.independence_penalty == 0

    def test_independence_penalty_lowers_overall_score(self):
        obs = _obs(
            severity=Severity.INFO,
            confidence=Confidence.POSSIBLE,
            independence=_make_independence(result="unmet"),
        )
        vs = _vs()
        score_satisfied = compute_score(
            [_obs(severity=Severity.INFO, confidence=Confidence.POSSIBLE)],
            [vs],
            frozenset(),
        )
        score_unmet = compute_score([obs], [vs], frozenset())
        assert score_unmet.overall < score_satisfied.overall
        assert score_unmet.overall == score_satisfied.overall - 15
