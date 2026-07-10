"""Tests for project_evidence() — RunAssessment → GateEvidence projection."""

from __future__ import annotations

from typing import Any

from tests.contracts.test_models import _make_observation, _make_run_assessment
from verdx.contracts.assessment import (
    AssessmentConfidence,
    CoverageGap,
    EscalationSummary,
    RecoveryItem,
    Score,
)
from verdx.contracts.dimensions import (
    CoverageStatus,
    EscalationLevel,
)
from verdx.engine.runtime.projector import (
    _group_observations,
    _project_recovery,
    project_evidence,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _assessment(**overrides: Any):
    return _make_run_assessment(**overrides)


def _obs(**overrides: Any):
    return _make_observation(**overrides)


# ===========================================================================
# Observation grouping
# ===========================================================================


class TestGroupObservations:
    def test_l3_becomes_action(self) -> None:
        obs = (_obs(escalation=EscalationLevel.L3),)
        actions, awareness = _group_observations(obs)
        assert len(actions) == 1
        assert len(awareness) == 0
        assert actions[0].count == 1

    def test_l4_becomes_action(self) -> None:
        obs = (_obs(escalation=EscalationLevel.L4),)
        actions, _awareness = _group_observations(obs)
        assert len(actions) == 1

    def test_l0_becomes_awareness(self) -> None:
        obs = (_obs(escalation=EscalationLevel.L0),)
        actions, awareness = _group_observations(obs)
        assert len(actions) == 0
        assert len(awareness) == 1

    def test_l1_becomes_awareness(self) -> None:
        obs = (_obs(escalation=EscalationLevel.L1),)
        _actions, awareness = _group_observations(obs)
        assert len(awareness) == 1

    def test_l2_becomes_awareness(self) -> None:
        obs = (_obs(escalation=EscalationLevel.L2),)
        _actions, awareness = _group_observations(obs)
        assert len(awareness) == 1

    def test_same_rule_grouped(self) -> None:
        obs = (
            _obs(rule_id="r1", escalation=EscalationLevel.L3, observation_id="o1"),
            _obs(rule_id="r1", escalation=EscalationLevel.L3, observation_id="o2"),
        )
        actions, _ = _group_observations(obs)
        assert len(actions) == 1
        assert actions[0].count == 2

    def test_different_rules_separate(self) -> None:
        obs = (
            _obs(rule_id="r1", escalation=EscalationLevel.L3, observation_id="o1"),
            _obs(rule_id="r2", escalation=EscalationLevel.L3, observation_id="o2"),
        )
        actions, _ = _group_observations(obs)
        assert len(actions) == 2

    def test_empty_observations(self) -> None:
        actions, awareness = _group_observations(())
        assert actions == []
        assert awareness == []

    def test_action_carries_first_location(self) -> None:
        obs = (
            _obs(
                rule_id="r1",
                escalation=EscalationLevel.L3,
                location="file.py:10",
                observation_id="o1",
            ),
            _obs(
                rule_id="r1",
                escalation=EscalationLevel.L3,
                location="file.py:20",
                observation_id="o2",
            ),
        )
        actions, _ = _group_observations(obs)
        assert actions[0].location == "file.py:10"


# ===========================================================================
# Recovery projection
# ===========================================================================


class TestProjectRecovery:
    def test_strips_context(self) -> None:
        item = RecoveryItem(
            id="r-1",
            kind="retryable",
            description="check failed",
            validator_id="check-1",
            context={"secret_key": "s3cr3t", "error": "boom"},
        )
        projected = _project_recovery((item,))
        assert len(projected) == 1
        assert projected[0].id == "r-1"
        assert projected[0].kind == "retryable"
        assert projected[0].validator_id == "check-1"
        assert not hasattr(projected[0], "context") or "secret_key" not in str(projected[0])

    def test_preserves_code(self) -> None:
        item = RecoveryItem(
            id="r-2",
            kind="blocked",
            description="tool missing",
            validator_id="lint",
            context={},
            code="TOOL_NOT_FOUND",
        )
        projected = _project_recovery((item,))
        assert projected[0].code == "TOOL_NOT_FOUND"


# ===========================================================================
# project_evidence
# ===========================================================================


class TestProjectEvidence:
    def test_clean_assessment_passes(self) -> None:
        assessment = _assessment(
            observations=(),
            score=Score(
                overall=95,
                dimension_scores={},
                observation_count=0,
                escalation_distribution={},
                coverage_penalty=0,
            ),
            escalation_summary=EscalationSummary(counts={}, highest=EscalationLevel.L0),
        )
        evidence = project_evidence(assessment, threshold=80)
        assert evidence.passed is True
        assert evidence.score == 95
        assert evidence.threshold == 80
        assert evidence.coverage_ok is True
        assert evidence.confidence_ok is True
        assert len(evidence.action_required) == 0
        assert len(evidence.awareness) == 0

    def test_below_threshold_fails(self) -> None:
        assessment = _assessment(
            score=Score(
                overall=60,
                dimension_scores={},
                observation_count=1,
                escalation_distribution={EscalationLevel.L3: 1},
                coverage_penalty=0,
            ),
        )
        evidence = project_evidence(assessment, threshold=80)
        assert evidence.passed is False
        assert evidence.score == 60

    def test_at_threshold_passes(self) -> None:
        assessment = _assessment(
            score=Score(
                overall=80,
                dimension_scores={},
                observation_count=0,
                escalation_distribution={},
                coverage_penalty=0,
            ),
            escalation_summary=EscalationSummary(counts={}, highest=EscalationLevel.L0),
        )
        evidence = project_evidence(assessment, threshold=80)
        assert evidence.passed is True

    def test_l3_observation_in_action_required(self) -> None:
        obs = _obs(escalation=EscalationLevel.L3)
        assessment = _assessment(observations=(obs,))
        evidence = project_evidence(assessment)
        assert len(evidence.action_required) == 1
        assert evidence.action_required[0].rule == obs.rule_id

    def test_l1_observation_in_awareness(self) -> None:
        obs = _obs(escalation=EscalationLevel.L1)
        assessment = _assessment(
            observations=(obs,),
            escalation_summary=EscalationSummary(
                counts={EscalationLevel.L1: 1},
                highest=EscalationLevel.L1,
            ),
        )
        evidence = project_evidence(assessment)
        assert len(evidence.awareness) == 1

    def test_recovery_items_projected(self) -> None:
        recovery = RecoveryItem(
            id="r-1",
            kind="retryable",
            description="check timed out",
            validator_id="slow-check",
            context={"timeout": 300},
        )
        assessment = _assessment(recovery_items=(recovery,))
        evidence = project_evidence(assessment)
        assert len(evidence.recovery) == 1
        assert evidence.recovery[0].validator_id == "slow-check"

    def test_domain_propagated(self) -> None:
        assessment = _assessment()
        evidence = project_evidence(assessment, domain="training")
        assert evidence.domain == "training"

    def test_coverage_gaps_passed_through(self) -> None:
        gap = CoverageGap(
            validator_id="lint",
            reason="crashed",
            impact="lint not run",
            dimensions_affected=("correctness",),
        )
        assessment = _assessment(coverage_gaps=(gap,))
        evidence = project_evidence(assessment)
        assert len(evidence.coverage_gaps) == 1

    def test_summary_counts(self) -> None:
        obs_action = _obs(
            rule_id="action-rule",
            escalation=EscalationLevel.L3,
            observation_id="o1",
        )
        obs_aware = _obs(
            rule_id="aware-rule",
            escalation=EscalationLevel.L1,
            observation_id="o2",
        )
        assessment = _assessment(
            observations=(obs_action, obs_aware),
            checks_run=10,
            checks_passed=8,
            validators_clean=1,
            escalation_summary=EscalationSummary(
                counts={EscalationLevel.L3: 1, EscalationLevel.L1: 1},
                highest=EscalationLevel.L3,
            ),
        )
        evidence = project_evidence(assessment)
        assert evidence.summary.checks_run == 10
        assert evidence.summary.checks_passed == 8
        assert evidence.summary.blocking_findings == 1
        assert evidence.summary.awareness_items == 1

    def test_coverage_status_propagated(self) -> None:
        assessment = _assessment(coverage_status=CoverageStatus.DEGRADED)
        evidence = project_evidence(assessment)
        assert evidence.coverage == CoverageStatus.DEGRADED

    def test_coverage_ok_allows_full_and_partial_only(self) -> None:
        for status in (CoverageStatus.FULL, CoverageStatus.PARTIAL):
            evidence = project_evidence(_assessment(coverage_status=status))
            assert evidence.coverage_ok is True

        evidence = project_evidence(_assessment(coverage_status=CoverageStatus.DEGRADED))
        assert evidence.coverage_ok is False

    def test_confidence_ok_rejects_low_only(self) -> None:
        for confidence in (AssessmentConfidence.STRONG, AssessmentConfidence.GUARDED):
            evidence = project_evidence(_assessment(confidence=confidence))
            assert evidence.confidence_ok is True

        evidence = project_evidence(_assessment(confidence=AssessmentConfidence.LOW))
        assert evidence.confidence_ok is False

    def test_passed_remains_score_only_when_coverage_is_degraded(self) -> None:
        assessment = _assessment(
            coverage_status=CoverageStatus.DEGRADED,
            confidence=AssessmentConfidence.LOW,
            score=Score(
                overall=95,
                dimension_scores={},
                observation_count=0,
                escalation_distribution={},
                coverage_penalty=0,
            ),
            escalation_summary=EscalationSummary(counts={}, highest=EscalationLevel.L0),
        )
        evidence = project_evidence(assessment, threshold=80)
        assert evidence.passed is True
        assert evidence.coverage_ok is False
        assert evidence.confidence_ok is False

    def test_schema_version_set(self) -> None:
        assessment = _assessment()
        evidence = project_evidence(assessment)
        assert evidence.schema_version == "verdx.evidence.v1"

    def test_truncation_under_budget_is_none(self) -> None:
        assessment = _assessment(observations=())
        evidence = project_evidence(assessment, hard_max_tokens=10000)
        assert evidence.truncation is None
