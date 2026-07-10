"""Tests for handoff brief composition and rendering."""

from __future__ import annotations

from tests.contracts.test_models import (
    _make_independence,
    _make_observation,
)
from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    CoverageGap,
    EscalationSummary,
    ImprovementSummary,
    RecoveryItem,
    RunAssessment,
    Score,
    Trend,
)
from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.engine.validation.handoff import compose_handoff, render_handoff_markdown


def _assessment(**overrides):
    defaults = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "run_id": "run-abc",
        "target_key": "course/ch01",
        "target_content_hash": "sha256:test",
        "timestamp": "2026-05-01T12:00:00Z",
        "coverage_status": CoverageStatus.FULL,
        "validator_statuses": (),
        "coverage_gaps": (),
        "checks_run": 10,
        "checks_passed": 10,
        "validators_clean": 2,
        "observations": (),
        "recovery_items": (),
        "confidence": AssessmentConfidence.STRONG,
        "score": Score(
            overall=85,
            dimension_scores={},
            observation_count=0,
            escalation_distribution={},
            coverage_penalty=0,
        ),
        "escalation_summary": EscalationSummary(
            counts={EscalationLevel.L0: 0},
            highest=EscalationLevel.L0,
        ),
    }
    return RunAssessment(**(defaults | overrides))


class TestComposeHandoff:
    def test_clean_assessment_produces_brief(self):
        brief = compose_handoff(_assessment())
        assert brief.run_id == "run-abc"
        assert brief.target_key == "course/ch01"
        assert brief.score == 85
        assert brief.coverage_status == "full"
        assert brief.confidence == "strong"
        assert brief.observation_count == 0
        assert brief.top_observations == ()
        assert brief.independence_summary == "n/a"

    def test_observations_sorted_by_escalation(self):
        obs_low = _make_observation(
            observation_id="obs-1",
            escalation=EscalationLevel.L1,
            summary="minor issue",
        )
        obs_high = _make_observation(
            observation_id="obs-2",
            escalation=EscalationLevel.L4,
            summary="critical issue",
        )
        brief = compose_handoff(_assessment(observations=(obs_low, obs_high)))
        assert brief.top_observations[0].escalation == "L4"
        assert brief.top_observations[1].escalation == "L1"

    def test_coverage_gaps_listed(self):
        gap = CoverageGap(
            validator_id="training-structure",
            reason="validator failed",
            impact="structural checks skipped",
            dimensions_affected=("structural-integrity",),
        )
        brief = compose_handoff(_assessment(coverage_gaps=(gap,)))
        assert brief.coverage_gaps == ("training-structure",)

    def test_recovery_items_listed(self):
        ri = RecoveryItem(
            id="ri-1",
            kind="retryable",
            description="Fix the YAML syntax error",
            validator_id="yaml-lint",
            context={},
        )
        brief = compose_handoff(_assessment(recovery_items=(ri,)))
        assert brief.recovery_summaries == ("Fix the YAML syntax error",)

    def test_trend_from_improvement(self):
        imp = ImprovementSummary(
            resolved_count=3,
            new_count=1,
            persistent_count=2,
            resolved_fingerprints=(),
            trend=Trend.IMPROVING,
        )
        brief = compose_handoff(_assessment(improvement=imp))
        assert brief.trend_description == "3 resolved, 1 new, 2 persistent"

    def test_independence_unmet_listed(self):
        obs = _make_observation(
            independence=_make_independence(result="unmet", validator_role="v1"),
        )
        brief = compose_handoff(_assessment(observations=(obs,)))
        assert brief.independence_summary == "unmet: v1"

    def test_independence_satisfied(self):
        obs = _make_observation(
            independence=_make_independence(result="satisfied"),
        )
        brief = compose_handoff(_assessment(observations=(obs,)))
        assert brief.independence_summary == "satisfied"


class TestRenderHandoffMarkdown:
    def test_renders_header_and_stats(self):
        brief = compose_handoff(_assessment())
        md = render_handoff_markdown(brief)
        assert "# Handoff Brief: course/ch01" in md
        assert "**Score:** 85/100" in md
        assert "**Coverage:** full" in md

    def test_renders_observations_section(self):
        obs = _make_observation(
            escalation=EscalationLevel.L3,
            summary="broken link",
        )
        brief = compose_handoff(_assessment(observations=(obs,)))
        md = render_handoff_markdown(brief)
        assert "## Top Observations" in md
        assert "broken link" in md

    def test_renders_coverage_gaps(self):
        gap = CoverageGap(
            validator_id="training-content",
            reason="crashed",
            impact="content checks skipped",
            dimensions_affected=("content-accuracy",),
        )
        brief = compose_handoff(_assessment(coverage_gaps=(gap,)))
        md = render_handoff_markdown(brief)
        assert "## Coverage Gaps" in md
        assert "training-content" in md

    def test_renders_recovery_items(self):
        ri = RecoveryItem(
            id="ri-1",
            kind="retryable",
            description="Fix image references",
            validator_id="yaml-lint",
            context={},
        )
        brief = compose_handoff(_assessment(recovery_items=(ri,)))
        md = render_handoff_markdown(brief)
        assert "## Recovery Items" in md
        assert "Fix image references" in md

    def test_clean_assessment_has_no_sections(self):
        brief = compose_handoff(_assessment())
        md = render_handoff_markdown(brief)
        assert "## Top Observations" not in md
        assert "## Coverage Gaps" not in md
        assert "## Recovery Items" not in md
