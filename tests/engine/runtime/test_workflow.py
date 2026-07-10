"""Tests for run_gate_workflow() — new pipeline orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.engine.runtime._workflow_check_fns import failing_check, passing_check
from verdx.contracts.check import check_from_validator
from verdx.contracts.config import EvidenceLimits
from verdx.contracts.context import CheckContext
from verdx.contracts.dimensions import DimLevel, EscalationLevel
from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.config import DomainConfig
from verdx.engine.runtime.engine import RunOutcome
from verdx.engine.runtime.workflow import run_gate_workflow
from verdx.engine.validation.escalation import EscalationPolicy, EscalationRule, RuleMatch

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _minimal_policy() -> EscalationPolicy:
    return EscalationPolicy(
        version=1,
        floors=(),
        rules=(
            EscalationRule(
                name="default",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.LOW),
                level=EscalationLevel.L1,
            ),
        ),
    )


def _minimal_config() -> DomainConfig:
    return DomainConfig(
        version=1,
        policy=_minimal_policy(),
        critical_validators=frozenset(),
        dimensions=(),
        evidence_limits=EvidenceLimits(
            max_evidence_bytes=65536, max_string_length=4096, max_data_entries=50
        ),
        timeout_seconds=300,
        dimension_map=None,
        scoring_config=None,
    )


def _make_domain(
    name: str = "testing",
    validators: list[tuple[Any, dict[str, Any]]] | None = None,
    live_validators: list[tuple[Any, dict[str, Any]]] | None = None,
    subject_discovery: Any = None,
) -> DomainSpec:
    if validators is None:
        validators = [(passing_check, {})]

    checks = [check_from_validator(v, cfg, name) for v, cfg in validators]
    if live_validators is not None:
        checks.extend(
            check_from_validator(v, cfg, name, capabilities=frozenset({"live"}))
            for v, cfg in live_validators
        )

    if subject_discovery is not None:
        discovery = subject_discovery
    else:
        discovery = lambda _p, _k: SubjectDiscoveryResult()  # noqa: E731

    return DomainSpec(
        name=name,
        markers=("marker.yml",),
        load_default_config=_minimal_config,
        load_checks=lambda: tuple(checks),
        load_profiles=lambda: (),
        discover_subjects=discovery,
    )


# ===========================================================================
# RunOutcome shape
# ===========================================================================


class TestWorkflowBasics:
    def test_returns_run_outcome(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert isinstance(result, RunOutcome)

    def test_produces_assessment_json(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert result.assessment_json
        import json

        data = json.loads(result.assessment_json)
        assert "score" in data
        assert data["target_key"] == str(target)

    def test_produces_report_md(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert result.report_md
        assert "Assessment" in result.report_md

    def test_assessment_has_score(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert result.assessment.score.overall == 100

    def test_custom_run_id_and_timestamp(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
            run_id="run-custom123",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        assert result.assessment.run_id == "run-custom123"
        assert result.assessment.timestamp == "2026-01-01T00:00:00+00:00"


# ===========================================================================
# Observations
# ===========================================================================


class TestWorkflowObservations:
    def test_failing_validator_produces_observations(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain(
            validators=[(failing_check, {})],
        )

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert len(result.observations) >= 1
        assert result.assessment.score.overall < 100

    def test_validator_statuses_populated(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert len(result.validator_statuses) >= 1
        assert result.validator_statuses[0].status == "completed"


# ===========================================================================
# Pipeline options
# ===========================================================================


class TestWorkflowOptions:
    def test_no_cache_flag_honored(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        r1 = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
            no_cache=True,
        )
        r2 = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
            no_cache=True,
        )

        assert r1.assessment.score.overall == r2.assessment.score.overall

    def test_static_only_skips_live_checks(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        live_called = []

        def live_check(ctx: Any) -> ValidatorResult:
            live_called.append(True)
            return ValidatorResult(
                validator_id="live-check",
                validator_version="1.0.0",
                drafts=[],
                checks_run=1,
                checks_passed=1,
                duration_ms=10,
                status="completed",
            )

        live_check.validator_id = "live-check"  # type: ignore[attr-defined]
        live_check.validator_version = "1.0.0"  # type: ignore[attr-defined]
        live_check.__module__ = "test_workflow"
        live_check.__qualname__ = "live_check"

        domain = _make_domain(
            live_validators=[(live_check, {})],
        )

        run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert not live_called

    def test_previous_score_flows_to_assessment(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain()

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
            previous_score=50,
        )

        assert result.assessment.score.comparison is not None
        assert result.assessment.score.comparison.previous_score == 50


# ===========================================================================
# Subject discovery
# ===========================================================================


class TestWorkflowSubjectDiscovery:
    def test_no_subject_discovery_ok(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()
        domain = _make_domain(subject_discovery=None)

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert result.assessment.subject_coverage == ()

    def test_discovery_exception_returns_empty_coverage(self, tmp_path: Path) -> None:
        target = tmp_path / "course"
        target.mkdir()

        def bad_discovery(p: Path, k: str) -> Any:
            raise RuntimeError("discovery failed")

        domain = _make_domain(subject_discovery=bad_discovery)

        result = run_gate_workflow(
            domain=domain,
            target=target,
            target_key=str(target),
            state_dir=tmp_path / "state",
            policy=_minimal_policy(),
            static_only=True,
        )

        assert result.assessment.subject_coverage == ()


# ===========================================================================
# CheckContext compatibility
# ===========================================================================


class TestCheckContextCompat:
    def test_has_previous_observations_property(self) -> None:
        ctx = CheckContext(
            run_id="run-test",
            target_key="target",
            target=Path("/tmp/test"),
            target_content_hash="abc123",
            domain="testing",
            check_id="test-check",
            check_version="1.0.0",
            config={},
            timeout_seconds=60,
        )

        assert ctx.previous_observations == []
        assert isinstance(ctx.previous_observations, list)
