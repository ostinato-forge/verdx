"""Tests for derive_assessment() — ExecutionResult → RunAssessment orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from verdx.contracts.assessment import RecoveryItem
from verdx.contracts.check import CheckSpec, PythonAdapter
from verdx.contracts.dimensions import (
    Confidence,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.observation import ObservationDraft
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.assessor import (
    AssessmentResult,
    _build_provenance,
    _build_validator_statuses,
    _derive_critical_ids,
    derive_assessment,
)
from verdx.engine.runtime.executor import ExecutionResult
from verdx.engine.validation.escalation import (
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

if TYPE_CHECKING:
    from verdx.contracts.plan import ResolvedCheck, ResolvedGatePlan

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _policy() -> EscalationPolicy:
    return EscalationPolicy(
        version=1,
        floors=(),
        rules=(
            EscalationRule(
                name="high-risk",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
                level=EscalationLevel.L3,
            ),
            EscalationRule(
                name="low-risk",
                match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                level=EscalationLevel.L0,
            ),
        ),
    )


def _draft(**overrides: Any) -> ObservationDraft:
    defaults: dict[str, Any] = {
        "target_key": "test-target",
        "location": "file.py:10",
        "rule_id": "test-rule",
        "category": "correctness",
        "severity": Severity.HIGH,
        "confidence": Confidence.CONFIRMED,
        "dimensions": {"correctness": DimLevel.HIGH},
        "summary": "Test finding",
    }
    return ObservationDraft(**(defaults | overrides))


def _validator_result(**overrides: Any) -> ValidatorResult:
    defaults: dict[str, Any] = {
        "validator_id": "test-check",
        "validator_version": "1.0.0",
        "drafts": [],
        "checks_run": 3,
        "checks_passed": 3,
        "duration_ms": 50,
        "status": "completed",
    }
    return ValidatorResult(**(defaults | overrides))


def _check_spec(**overrides: Any) -> CheckSpec:
    defaults: dict[str, Any] = {
        "check_id": "test-check",
        "domain": "training",
        "title": "Test Check",
        "category": "correctness",
        "version": "1.0.0",
        "adapter": PythonAdapter(import_path="mod:fn"),
        "dimensions": {"correctness": DimLevel.HIGH},
    }
    return CheckSpec(**(defaults | overrides))


def _resolved_check(**overrides: Any) -> ResolvedCheck:
    from verdx.contracts.plan import ResolvedCheck

    defaults: dict[str, Any] = {
        "domain": "training",
        "check_id": "test-check",
        "check_version": "1.0.0",
        "spec": _check_spec(),
        "effective_config": {},
        "config_hash": "sha256:abc",
        "capabilities": frozenset(),
        "critical": False,
        "cacheable": True,
    }
    return ResolvedCheck(**(defaults | overrides))


def _plan(**overrides: Any) -> ResolvedGatePlan:
    from verdx.contracts.plan import ResolvedGatePlan

    defaults: dict[str, Any] = {
        "run_id": "run-test",
        "target_key": "test-target",
        "target_content_hash": "sha256:content",
        "timestamp": "2026-05-21T00:00:00Z",
        "domains": ("training",),
        "checks": (_resolved_check(),),
    }
    return ResolvedGatePlan(**(defaults | overrides))


def _execution_result(**overrides: Any) -> ExecutionResult:
    defaults: dict[str, Any] = {
        "results": (),
        "recovery_items": (),
        "skipped_checks": (),
        "cache_hits": 0,
        "cache_misses": 0,
        "duration_ms": 100,
    }
    return ExecutionResult(**(defaults | overrides))


# ===========================================================================
# Validator status building
# ===========================================================================


class TestBuildValidatorStatuses:
    def test_completed_result(self) -> None:
        result = _validator_result(drafts=[_draft()])
        statuses = _build_validator_statuses((result,))
        assert len(statuses) == 1
        assert statuses[0].validator_id == "test-check"
        assert statuses[0].status == "completed"
        assert statuses[0].observation_count == 1

    def test_failed_result(self) -> None:
        result = _validator_result(status="failed", error="crash", checks_run=0, checks_passed=0)
        statuses = _build_validator_statuses((result,))
        assert statuses[0].status == "failed"
        assert statuses[0].error == "crash"

    def test_skipped_result(self) -> None:
        result = _validator_result(status="skipped", skip_reason="no network")
        statuses = _build_validator_statuses((result,))
        assert statuses[0].status == "skipped"
        assert statuses[0].skip_reason == "no network"


# ===========================================================================
# Provenance building
# ===========================================================================


class TestBuildProvenance:
    def test_basic_provenance(self) -> None:
        result = _validator_result()
        prov = _build_provenance(
            result,
            producer_role="content-author",
            producer_id="user-1",
            producer_actor=None,
            validator_actors=None,
        )
        assert prov.producer_role == "content-author"
        assert prov.producer_id == "user-1"
        assert prov.validator_id == "test-check"
        assert prov.independence.result in ("satisfied", "waived", "unmet")


# ===========================================================================
# Critical ID derivation
# ===========================================================================


class TestDeriveCriticalIds:
    def test_no_critical(self) -> None:
        plan = _plan(checks=(_resolved_check(critical=False),))
        assert _derive_critical_ids(plan) == frozenset()

    def test_one_critical(self) -> None:
        plan = _plan(
            checks=(
                _resolved_check(check_id="a", critical=True),
                _resolved_check(check_id="b", critical=False),
            )
        )
        assert _derive_critical_ids(plan) == frozenset({"a"})


# ===========================================================================
# derive_assessment
# ===========================================================================


class TestDeriveAssessment:
    def test_empty_execution(self) -> None:
        result = derive_assessment(
            _execution_result(),
            _plan(checks=()),
            policy=_policy(),
        )
        assert isinstance(result, AssessmentResult)
        assert result.assessment.checks_run == 0
        assert result.observations == ()

    def test_completed_check_with_drafts(self) -> None:
        drafts = [_draft()]
        vr = _validator_result(drafts=drafts)
        exec_result = _execution_result(results=(vr,), cache_misses=1)
        result = derive_assessment(exec_result, _plan(), policy=_policy())
        assert len(result.observations) == 1
        assert result.observations[0].rule_id == "test-rule"
        assert result.assessment.score.overall <= 100

    def test_failed_check_no_observations(self) -> None:
        vr = _validator_result(status="failed", error="boom", checks_run=0, checks_passed=0)
        exec_result = _execution_result(results=(vr,))
        result = derive_assessment(exec_result, _plan(), policy=_policy())
        assert result.observations == ()
        assert result.assessment.validator_statuses[0].status == "failed"

    def test_recovery_items_passed_through(self) -> None:
        recovery = RecoveryItem(
            id="r-1",
            kind="retryable",
            description="check failed",
            validator_id="test-check",
            context={"error": "crash"},
        )
        exec_result = _execution_result(recovery_items=(recovery,))
        result = derive_assessment(exec_result, _plan(), policy=_policy())
        assert len(result.assessment.recovery_items) >= 1

    def test_multiple_checks_mixed_statuses(self) -> None:
        vr1 = _validator_result(
            validator_id="c1",
            drafts=[_draft(rule_id="r1")],
        )
        vr2 = _validator_result(
            validator_id="c2",
            status="failed",
            error="crash",
            checks_run=0,
            checks_passed=0,
        )
        vr3 = _validator_result(
            validator_id="c3",
            status="skipped",
            skip_reason="no capability",
        )
        exec_result = _execution_result(results=(vr1, vr2, vr3))
        plan = _plan(
            checks=(
                _resolved_check(check_id="c1"),
                _resolved_check(check_id="c2"),
                _resolved_check(check_id="c3"),
            )
        )
        result = derive_assessment(exec_result, plan, policy=_policy())
        assert len(result.observations) == 1
        assert len(result.assessment.validator_statuses) == 3

    def test_critical_check_affects_coverage(self) -> None:
        vr = _validator_result(
            validator_id="critical-check",
            status="failed",
            checks_run=0,
            checks_passed=0,
        )
        plan = _plan(checks=(_resolved_check(check_id="critical-check", critical=True),))
        exec_result = _execution_result(results=(vr,))
        result = derive_assessment(exec_result, plan, policy=_policy())
        assert len(result.assessment.coverage_gaps) >= 1

    def test_previous_score_tracked(self) -> None:
        drafts = [_draft()]
        vr = _validator_result(drafts=drafts)
        exec_result = _execution_result(results=(vr,))
        result = derive_assessment(
            exec_result,
            _plan(),
            policy=_policy(),
            previous_score=90,
        )
        assert result.assessment.score.comparison is not None

    def test_completed_no_drafts_is_clean(self) -> None:
        vr = _validator_result(drafts=[])
        exec_result = _execution_result(results=(vr,))
        result = derive_assessment(exec_result, _plan(), policy=_policy())
        assert result.observations == ()
        assert result.assessment.checks_run == 3
