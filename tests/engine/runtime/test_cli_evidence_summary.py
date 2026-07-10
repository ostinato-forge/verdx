"""Tests for compact release evidence summaries."""

from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from tests.contracts.test_models import (
    _make_escalation_summary,
    _make_observation,
    _make_run_assessment,
    _make_score,
)
from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.engine.runtime.cli import _pipeline_evidence_summary_to_dict, main
from verdx.engine.runtime.pipeline import GateDecision, StageAction, StageResult


def _payload(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "domain": "training",
        "pipeline": "section-review",
        "target_key": "course/ch01",
        "target_content_hash": "sha256:abc123",
        "from_stage": "self-qa",
        "started_at": "2026-05-23T12:00:00+00:00",
        "completed_at": "2026-05-23T12:00:01+00:00",
        "status": "completed",
        "stop_stage": None,
        "stop_reason": "pipeline completed",
    }
    return defaults | overrides


def _assessment(**overrides: Any):
    observations = overrides.pop("observations", ())
    counts: dict[EscalationLevel, int] = {}
    for observation in observations:
        counts[observation.escalation] = counts.get(observation.escalation, 0) + 1
    highest = max(counts, key=lambda level: level.value) if counts else EscalationLevel.L0
    score = overrides.pop(
        "score",
        _make_score(
            overall=95,
            observation_count=len(observations),
            escalation_distribution=counts,
            coverage_penalty=0,
        ),
    )
    return _make_run_assessment(
        observations=observations,
        score=score,
        escalation_summary=_make_escalation_summary(counts=counts, highest=highest),
        checks_passed=10,
        validators_clean=1,
        confidence=AssessmentConfidence.STRONG,
        **overrides,
    )


def _stage_result(
    *,
    assessment=None,
    passed: bool = True,
    reason: str = "score 95 >= threshold 80",
    threshold: int | None = 80,
) -> StageResult:
    resolved_assessment = assessment or _assessment()
    return StageResult(
        stage="self-qa",
        gate_decision=GateDecision(
            passed=passed,
            reason=reason,
            score=resolved_assessment.score.overall,
            threshold=threshold,
        ),
        assessment=resolved_assessment,
        next_stage=None,
        actions=StageAction(labels=("qa-passed",)),
    )


def test_completed_pipeline_is_release_ready() -> None:
    summary = _pipeline_evidence_summary_to_dict(_payload(), [_stage_result()])

    assert summary["schema_version"] == "verdx.evidence-summary.v1"
    assert summary["release_ready"] is True
    assert summary["requires_human"] is False
    assert summary["blocked"] is False
    assert summary["contradictions"] == []
    assert summary["stages"][0]["evidence_gate_passed"] is True
    assert summary["next_actions"] == ["Release gate passed; proceed with the release workflow."]


def test_score_pass_with_degraded_coverage_is_contradiction() -> None:
    assessment = _assessment(coverage_status=CoverageStatus.DEGRADED)
    summary = _pipeline_evidence_summary_to_dict(_payload(), [_stage_result(assessment=assessment)])

    assert summary["release_ready"] is False
    assert summary["stages"][0]["gate_passed"] is True
    assert summary["stages"][0]["evidence_gate_passed"] is False
    assert summary["contradictions"][0]["code"] == "stage-gate-passed-with-failed-evidence"


def test_blocked_pipeline_counts_blocking_findings() -> None:
    observation = _make_observation(escalation=EscalationLevel.L3)
    assessment = _assessment(
        observations=(observation,),
        score=_make_score(
            overall=60,
            observation_count=1,
            escalation_distribution={EscalationLevel.L3: 1},
            coverage_penalty=0,
        ),
    )
    summary = _pipeline_evidence_summary_to_dict(
        _payload(status="blocked", stop_stage="self-qa", stop_reason="score 60 < threshold 80"),
        [
            _stage_result(
                assessment=assessment,
                passed=False,
                reason="score 60 < threshold 80",
            )
        ],
    )

    assert summary["release_ready"] is False
    assert summary["blocked"] is True
    assert summary["totals"]["blocking_findings"] == 1
    assert summary["stages"][0]["result"] == "blocked"


def test_requires_human_summary_can_have_no_stage_results() -> None:
    summary = _pipeline_evidence_summary_to_dict(
        _payload(
            status="requires-human",
            stop_stage="peer-review",
            stop_reason="human approval required at stage 'peer-review'",
        ),
        [],
    )

    assert summary["release_ready"] is False
    assert summary["requires_human"] is True
    assert summary["stage_count"] == 0
    assert summary["next_actions"] == [
        "Route the evidence summary to a human reviewer for peer-review."
    ]


def test_evidence_summary_command_is_registered() -> None:
    result = CliRunner().invoke(main, ["evidence-summary", "--help"])

    assert result.exit_code == 0
    assert "Run release pipeline and emit compact deterministic evidence." in result.output
