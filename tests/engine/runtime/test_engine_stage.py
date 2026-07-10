"""Tests for assess_stage() — pipeline-aware validation."""

from __future__ import annotations

from verdx.contracts.dimensions import (
    Confidence,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.observation import ObservationDraft
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.engine import (
    RunConfig,
    assess_stage,
)
from verdx.engine.runtime.pipeline import (
    GateConfig,
    PipelineConfig,
    PipelineStage,
    StageAction,
)
from verdx.engine.validation.escalation import (
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)


def _default_policy() -> EscalationPolicy:
    return EscalationPolicy(
        version=1,
        floors=(),
        rules=(
            EscalationRule(
                name="fallback",
                match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                level=EscalationLevel.L0,
            ),
        ),
    )


def _clean_validator(vid: str = "test-validator"):
    """A validator that produces no drafts (clean pass)."""

    def validate(ctx):
        return ValidatorResult(
            validator_id=vid,
            validator_version="1.0.0",
            drafts=[],
            checks_run=5,
            checks_passed=5,
            duration_ms=10,
            status="completed",
        )

    validate.validator_id = vid
    validate.validator_version = "1.0.0"
    return validate


def _noisy_validator(vid: str = "test-validator"):
    """A validator that produces drafts (issues found)."""

    def validate(ctx):
        drafts = [
            ObservationDraft(
                target_key=ctx.target_key,
                location="file.adoc:1",
                rule_id="test-rule",
                category="test",
                severity=Severity.HIGH,
                confidence=Confidence.CONFIRMED,
                dimensions={"correctness": DimLevel.HIGH},
                summary="Test issue",
            )
            for _ in range(5)
        ]
        return ValidatorResult(
            validator_id=vid,
            validator_version="1.0.0",
            drafts=drafts,
            checks_run=10,
            checks_passed=5,
            duration_ms=20,
            status="completed",
        )

    validate.validator_id = vid
    validate.validator_version = "1.0.0"
    return validate


def _run_config(validators):
    return RunConfig(
        run_id="run-test",
        target_key="test/target",
        target_path="/tmp/test-target",
        target_content_hash="sha256:abc",
        timestamp="2026-05-01T00:00:00Z",
        producer_role="content-developer",
        producer_id=None,
        policy=_default_policy(),
        validators=[(v, {}) for v in validators],
        critical_validator_ids=frozenset(),
        active_dispositions={},
        previous_fingerprints=None,
        previous_score=None,
        timeout_seconds=300,
    )


def _pipeline(*stages: PipelineStage) -> PipelineConfig:
    return PipelineConfig(version=1, name="test-pipeline", stages=stages)


# ===========================================================================
# assess_stage — score gate
# ===========================================================================


class TestAssessStageScoreGate:
    def test_clean_validators_pass_gate(self):
        v1 = _clean_validator("v1")
        v2 = _clean_validator("v2")
        stage = PipelineStage(
            name="qa",
            description="",
            gate=GateConfig(type="score", threshold=80),
            validator_ids=("v1", "v2"),
            on_pass=StageAction(labels=("passed",), advance="next"),
            on_fail=StageAction(labels=("failed",)),
        )
        config = _run_config([v1, v2])
        result = assess_stage("qa", _pipeline(stage), config)

        assert result.gate_decision.passed is True
        assert result.gate_decision.score == 100
        assert result.next_stage == "next"
        assert result.actions.labels == ("passed",)

    def test_noisy_validators_can_fail_gate(self):
        v = _noisy_validator("v1")
        stage = PipelineStage(
            name="qa",
            description="",
            gate=GateConfig(type="score", threshold=95),
            validator_ids=("v1",),
            on_pass=StageAction(advance="next"),
            on_fail=StageAction(labels=("needs-work",), comment="report"),
        )
        config = _run_config([v])
        result = assess_stage("qa", _pipeline(stage), config)

        assert result.gate_decision.passed is False
        assert result.gate_decision.score is not None
        assert result.gate_decision.score < 95
        assert result.next_stage is None
        assert result.actions.labels == ("needs-work",)
        assert result.actions.comment == "report"


# ===========================================================================
# assess_stage — human gate
# ===========================================================================


class TestAssessStageHumanGate:
    def test_human_gate_always_blocks(self):
        stage = PipelineStage(
            name="review",
            description="",
            gate=GateConfig(type="human", reviewers_from=("peer",)),
            on_pass=StageAction(advance="next"),
        )
        config = _run_config([])
        result = assess_stage("review", _pipeline(stage), config)

        assert result.gate_decision.passed is False
        assert "human approval" in result.gate_decision.reason
        assert result.next_stage is None


# ===========================================================================
# assess_stage — validator filtering
# ===========================================================================


class TestAssessStageFiltering:
    def test_only_stage_validators_run(self):
        v1 = _clean_validator("included")
        v2 = _noisy_validator("excluded")
        stage = PipelineStage(
            name="qa",
            description="",
            gate=GateConfig(type="score", threshold=80),
            validator_ids=("included",),
            on_pass=StageAction(advance="next"),
        )
        config = _run_config([v1, v2])
        result = assess_stage("qa", _pipeline(stage), config)

        assert result.gate_decision.passed is True
        assert result.gate_decision.score == 100
        validator_ids = [vs.validator_id for vs in result.assessment.validator_statuses]
        assert "included" in validator_ids
        assert "excluded" not in validator_ids

    def test_no_validators_in_stage(self):
        """Human-only stages have no validators — should still produce an assessment."""
        stage = PipelineStage(
            name="review",
            description="",
            gate=GateConfig(type="human"),
            validator_ids=(),
        )
        config = _run_config([_clean_validator("v1")])
        result = assess_stage("review", _pipeline(stage), config)

        assert result.assessment.checks_run == 0
        assert len(result.assessment.validator_statuses) == 0


# ===========================================================================
# assess_stage — serialization round-trip
# ===========================================================================


class TestAssessStageSerialize:
    def test_to_dict_is_valid_json_shape(self):
        v = _clean_validator("v1")
        stage = PipelineStage(
            name="qa",
            description="",
            gate=GateConfig(type="score", threshold=80),
            validator_ids=("v1",),
            on_pass=StageAction(labels=("passed",), advance="next"),
        )
        config = _run_config([v])
        result = assess_stage("qa", _pipeline(stage), config)
        d = result.to_dict()

        assert d["stage"] == "qa"
        assert isinstance(d["gate_decision"], dict)
        assert isinstance(d["actions"], dict)
        assert isinstance(d["assessment"], dict)
        assert d["assessment"]["schema_version"] == "verdx.assessment.v1"
