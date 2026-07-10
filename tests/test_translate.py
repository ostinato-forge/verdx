"""Tests for the translation layer — engine RunAssessment → porcelain Assessment."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    EscalationSummary,
    RunAssessment,
    Score,
    ValidatorStatus,
)
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.independence import IndependenceRecord
from verdx.contracts.observation import OBSERVATION_SCHEMA_VERSION, Observation
from verdx.translate import translate_assessment


def _make_independence() -> IndependenceRecord:
    return IndependenceRecord(
        producer_role="author",
        validator_role="static-analysis",
        actor_kind="system",
        relation="independent",
        policy_id="default",
        result="satisfied",
    )


def _make_observation(
    *,
    severity: Severity = Severity.HIGH,
    category: str = "structure",
    summary: str = "Test finding",
    fingerprint: str | None = None,
    location: str | None = "file.adoc:10",
) -> Observation:
    return Observation(
        schema_version=OBSERVATION_SCHEMA_VERSION,
        observation_id=f"obs-{uuid.uuid4().hex[:8]}",
        issue_fingerprint=fingerprint or f"fp-{uuid.uuid4().hex[:8]}",
        target_key="test-target",
        location=location,
        rule_id="test-rule",
        producer_role="validator",
        producer_id=None,
        validator_id="test-validator",
        validator_version="1.0.0",
        independence=_make_independence(),
        category=category,
        severity=severity,
        confidence=Confidence.CONFIRMED,
        dimensions={"accuracy": DimLevel.HIGH},
        escalation=EscalationLevel.L3,
        summary=summary,
    )


def _make_validator_status(
    vid: str = "test-validator",
    status: str = "completed",
) -> ValidatorStatus:
    return ValidatorStatus(
        validator_id=vid,
        validator_version="1.0.0",
        status=status,
        checks_run=5,
        checks_passed=4,
        observation_count=1,
        duration_ms=100,
    )


def _make_assessment(
    observations: tuple[Observation, ...] = (),
    validator_statuses: tuple[ValidatorStatus, ...] | None = None,
    score_overall: int = 85,
) -> RunAssessment:
    if validator_statuses is None:
        validator_statuses = (_make_validator_status(),)
    return RunAssessment(
        schema_version=ASSESSMENT_SCHEMA_VERSION,
        run_id=f"run-{uuid.uuid4().hex[:8]}",
        target_key="test-target",
        target_content_hash="sha256:abc123",
        timestamp=datetime.now(tz=UTC).isoformat(),
        coverage_status=CoverageStatus.FULL,
        validator_statuses=validator_statuses,
        coverage_gaps=(),
        checks_run=5,
        checks_passed=4,
        validators_clean=1,
        observations=observations,
        recovery_items=(),
        confidence=AssessmentConfidence.STRONG,
        score=Score(
            overall=score_overall,
            dimension_scores={},
            observation_count=len(observations),
            escalation_distribution={},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={},
            highest=EscalationLevel.L0,
        ),
    )


class TestTranslateAssessment:
    def test_basic_translation(self) -> None:
        engine = _make_assessment(score_overall=90)
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert result.score == 90
        assert result.verdict == "pass"
        assert result.metadata["profile"] == "standard"

    def test_block_verdict(self) -> None:
        engine = _make_assessment(score_overall=70)
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert result.verdict == "block"

    def test_pass_at_threshold(self) -> None:
        engine = _make_assessment(score_overall=80)
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert result.verdict == "pass"

    def test_observation_translation(self) -> None:
        obs = _make_observation(
            severity=Severity.HIGH,
            category="structure",
            summary="Missing section",
            location="file.adoc:10",
        )
        engine = _make_assessment(observations=(obs,))
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert len(result.observations) == 1
        p_obs = result.observations[0]
        assert p_obs.severity == "high"
        assert p_obs.category == "structure"
        assert p_obs.message == "Missing section"
        assert p_obs.location == "file.adoc:10"
        assert p_obs.fixable is False
        assert p_obs.signature == obs.issue_fingerprint

    def test_info_severity_filtered(self) -> None:
        obs = _make_observation(severity=Severity.INFO)
        engine = _make_assessment(observations=(obs,))
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert len(result.observations) == 0

    def test_validators_run_only_completed(self) -> None:
        statuses = (
            _make_validator_status("v1", "completed"),
            _make_validator_status("v2", "failed"),
            _make_validator_status("v3", "completed"),
        )
        engine = _make_assessment(validator_statuses=statuses)
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert result.validators_run == ("v1", "v3")

    def test_decision_hash_present(self) -> None:
        engine = _make_assessment()
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert len(result.decision_hash) == 64

    def test_metadata_fields(self) -> None:
        engine = _make_assessment()
        result = translate_assessment(engine, threshold=80, profile="standard")
        assert result.metadata["target"] == "test-target"
        assert result.metadata["profile"] == "standard"
        assert "timestamp" in result.metadata
        assert "verdx_version" in result.metadata

    def test_fixable_true_when_fixer_matches(self) -> None:
        from dataclasses import dataclass
        from pathlib import Path

        from verdx.fixers import FixerSpec
        from verdx.types import FixAction
        from verdx.types import Observation as Obs

        @dataclass
        class _StubFixer:
            def can_fix(self, observation: Obs) -> bool:
                return "Missing section" in observation.message

            def fix(self, target: Path, observation: Obs) -> FixAction | None:
                return None

        spec = FixerSpec(
            fixer_id="stub-fixer",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_StubFixer(),
        )

        obs = _make_observation(
            severity=Severity.HIGH,
            category="structure",
            summary="Missing section",
        )
        engine = _make_assessment(observations=(obs,))
        result = translate_assessment(engine, threshold=80, profile="standard", fixers=[spec])
        assert len(result.observations) == 1
        assert result.observations[0].fixable is True

    def test_fixable_false_when_no_fixer_matches(self) -> None:
        from dataclasses import dataclass
        from pathlib import Path

        from verdx.fixers import FixerSpec
        from verdx.types import FixAction
        from verdx.types import Observation as Obs

        @dataclass
        class _StubFixer:
            def can_fix(self, observation: Obs) -> bool:
                return False

            def fix(self, target: Path, observation: Obs) -> FixAction | None:
                return None

        spec = FixerSpec(
            fixer_id="stub-fixer",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_StubFixer(),
        )

        obs = _make_observation(severity=Severity.HIGH, category="structure")
        engine = _make_assessment(observations=(obs,))
        result = translate_assessment(engine, threshold=80, profile="standard", fixers=[spec])
        assert len(result.observations) == 1
        assert result.observations[0].fixable is False
