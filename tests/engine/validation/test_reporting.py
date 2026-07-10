"""Tests for JSON and Markdown reporting.

Proves Phase 5 acceptance criteria:
- Markdown snapshots are stable.
- Reports lead with coverage and positive signal.
- No human-readable report contains individual actor identity fields.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tests.contracts.test_models import (
    _make_applied_disposition,
    _make_evidence,
    _make_independence,
    _make_observation,
    _make_run_assessment,
    _make_validator_status,
)
from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    CoverageGap,
    DimensionScore,
    EscalationSummary,
    ImprovementSummary,
    RecoveryItem,
    Score,
    ScoreComparison,
    Trend,
)
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    SUBJECT_SCHEMA_VERSION,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
)
from verdx.engine.validation.reporting import (
    render_json,
    render_junit,
    render_markdown,
    render_sarif,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "reports"


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------


def _clean_run():
    """Full coverage, zero observations, high score."""
    vs = _make_validator_status(
        status="completed",
        checks_run=20,
        checks_passed=20,
        observation_count=0,
    )
    return _make_run_assessment(
        observations=(),
        validator_statuses=(vs,),
        coverage_gaps=(),
        checks_run=20,
        checks_passed=20,
        validators_clean=1,
        coverage_status=CoverageStatus.FULL,
        confidence=AssessmentConfidence.STRONG,
        score=Score(
            overall=100,
            dimension_scores={},
            observation_count=0,
            escalation_distribution={},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={},
            highest=EscalationLevel.L0,
        ),
    )


def _partial_zero_obs_run():
    """Partial coverage with zero observations — NOT clean."""
    vs_ok = _make_validator_status(
        status="completed",
        checks_run=10,
        checks_passed=10,
        observation_count=0,
    )
    vs_fail = _make_validator_status(
        validator_id="md-lint",
        status="failed",
        error="parser crash",
        checks_run=0,
        checks_passed=0,
        observation_count=0,
        duration_ms=0,
    )
    gap = CoverageGap(
        validator_id="md-lint",
        reason="parser crash",
        impact="md-lint checks not performed",
        dimensions_affected=("automation",),
    )
    return _make_run_assessment(
        observations=(),
        validator_statuses=(vs_ok, vs_fail),
        coverage_gaps=(gap,),
        checks_run=10,
        checks_passed=10,
        validators_clean=1,
        coverage_status=CoverageStatus.PARTIAL,
        confidence=AssessmentConfidence.LOW,
        score=Score(
            overall=90,
            dimension_scores={},
            observation_count=0,
            escalation_distribution={},
            coverage_penalty=10,
        ),
        escalation_summary=EscalationSummary(
            counts={},
            highest=EscalationLevel.L0,
        ),
    )


def _degraded_critical_run():
    """Critical validator failed — degraded coverage."""
    vs = _make_validator_status(
        validator_id="security-scan",
        status="failed",
        error="timeout after 30s",
        checks_run=0,
        checks_passed=0,
        observation_count=0,
        duration_ms=30000,
    )
    gap = CoverageGap(
        validator_id="security-scan",
        reason="timeout after 30s",
        impact="security-scan checks not performed",
        dimensions_affected=("security",),
    )
    return _make_run_assessment(
        observations=(),
        validator_statuses=(vs,),
        coverage_gaps=(gap,),
        checks_run=0,
        checks_passed=0,
        validators_clean=0,
        coverage_status=CoverageStatus.DEGRADED,
        confidence=AssessmentConfidence.LOW,
        score=Score(
            overall=70,
            dimension_scores={},
            observation_count=0,
            escalation_distribution={},
            coverage_penalty=30,
        ),
        escalation_summary=EscalationSummary(
            counts={},
            highest=EscalationLevel.L0,
        ),
    )


def _l3_human_decision_run():
    """Observation requiring human decision (L3)."""
    obs = _make_observation(
        escalation=EscalationLevel.L3,
        severity=Severity.HIGH,
        confidence=Confidence.CONFIRMED,
        summary="Lab solution references deprecated API endpoint",
        rule_id="api-deprecation",
        category="correctness",
        location="exercise:ge01/step:5",
        evidence=(
            _make_evidence(
                source="api-check",
                kind="deprecation",
                details="POST /v1/users is deprecated since v2.3",
                data={"endpoint": "/v1/users", "deprecated_since": "v2.3"},
            ),
        ),
        invalidating_conditions=("API endpoint is restored in a future version",),
    )
    return _make_run_assessment(
        observations=(obs,),
        checks_run=15,
        checks_passed=14,
        validators_clean=0,
        score=Score(
            overall=70,
            dimension_scores={
                "correctness": DimensionScore(
                    dimension="correctness",
                    score=70,
                    observation_count=1,
                ),
            },
            observation_count=1,
            escalation_distribution={EscalationLevel.L3: 1},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={EscalationLevel.L3: 1},
            highest=EscalationLevel.L3,
        ),
    )


def _accepted_risk_capped_run():
    """Disposition caps L3 → L1 (accepted risk)."""
    disp = _make_applied_disposition(
        decision="accepted-risk",
        reason="Known limitation, tracked upstream",
        team="training-content",
        effect="capped",
        original_escalation=EscalationLevel.L3,
        final_escalation=EscalationLevel.L1,
    )
    obs = _make_observation(
        escalation=EscalationLevel.L1,
        severity=Severity.HIGH,
        confidence=Confidence.CONFIRMED,
        summary="Lab references deprecated CLI flag",
        rule_id="cli-deprecation",
        category="correctness",
        disposition=disp,
    )
    return _make_run_assessment(
        observations=(obs,),
        checks_run=20,
        checks_passed=19,
        validators_clean=0,
        score=Score(
            overall=70,
            dimension_scores={
                "correctness": DimensionScore(
                    dimension="correctness",
                    score=70,
                    observation_count=1,
                ),
            },
            observation_count=1,
            escalation_distribution={EscalationLevel.L1: 1},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={EscalationLevel.L1: 1},
            highest=EscalationLevel.L1,
        ),
    )


def _floor_blocked_disposition_run():
    """Disposition blocked by L4 floor — escalation stays at L4."""
    disp = _make_applied_disposition(
        decision="accepted-risk",
        reason="Attempted to accept, but floor prevents",
        team="security",
        effect="capped",
        original_escalation=EscalationLevel.L4,
        final_escalation=EscalationLevel.L4,
    )
    obs = _make_observation(
        escalation=EscalationLevel.L4,
        severity=Severity.CRITICAL,
        confidence=Confidence.CONFIRMED,
        summary="Credentials exposed in lab solution",
        rule_id="secret-exposure",
        category="security",
        dimensions={"security": DimLevel.BLOCKING},
        disposition=disp,
    )
    return _make_run_assessment(
        observations=(obs,),
        checks_run=25,
        checks_passed=24,
        validators_clean=0,
        score=Score(
            overall=60,
            dimension_scores={
                "security": DimensionScore(
                    dimension="security",
                    score=60,
                    observation_count=1,
                ),
            },
            observation_count=1,
            escalation_distribution={EscalationLevel.L4: 1},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={EscalationLevel.L4: 1},
            highest=EscalationLevel.L4,
        ),
    )


def _improvement_trend_run():
    """Run showing improvement with resolved findings."""
    obs = _make_observation(
        issue_fingerprint="fp-persistent-1",
        escalation=EscalationLevel.L1,
        severity=Severity.MEDIUM,
        confidence=Confidence.PROBABLE,
        summary="Minor formatting inconsistency",
        rule_id="format-check",
        category="style",
    )
    return _make_run_assessment(
        observations=(obs,),
        checks_run=30,
        checks_passed=29,
        validators_clean=0,
        score=Score(
            overall=85,
            dimension_scores={
                "style": DimensionScore(
                    dimension="style",
                    score=85,
                    observation_count=1,
                ),
            },
            observation_count=1,
            escalation_distribution={EscalationLevel.L1: 1},
            coverage_penalty=0,
            comparison=ScoreComparison(previous_score=65, delta=20),
        ),
        escalation_summary=EscalationSummary(
            counts={EscalationLevel.L1: 1},
            highest=EscalationLevel.L1,
        ),
        improvement=ImprovementSummary(
            resolved_count=3,
            new_count=0,
            persistent_count=1,
            resolved_fingerprints=(
                "fp-resolved-1",
                "fp-resolved-2",
                "fp-resolved-3",
            ),
            trend=Trend.IMPROVING,
        ),
    )


def _make_subject_coverage(
    *,
    state: SubjectCoverageState,
    criticality: str = "critical",
) -> SubjectCoverageRecord:
    subject = ValidationSubject(
        schema_version=SUBJECT_SCHEMA_VERSION,
        subject_key="outline:outline.yml",
        target_key="sample/ch04/ge01",
        kind="outline",
        label="outline.yml",
        selector=SubjectSelector(kind="subject", values=("outline:outline.yml",)),
        location="outline.yml",
    )
    return SubjectCoverageRecord(
        schema_version=SUBJECT_COVERAGE_SCHEMA_VERSION,
        run_id="run-001",
        target_key="sample/ch04/ge01",
        target_content_hash="sha256:abc123",
        subject=subject,
        state=state,
        criticality=criticality,
        required_validator_ids=("training-structure",),
        completed_validator_ids=(),
        failed_validator_ids=(),
        skipped_validator_ids=(),
        checks_run=0,
        checks_passed=0,
        observation_fingerprints=(),
        coverage_gap_validator_ids=(),
        blocking_reason=f"{state.value}: training-structure",
        model_health=SubjectModelHealth(),
    )


def _with_subject_coverage(
    assessment,
    *records: SubjectCoverageRecord,
):
    return replace(assessment, subject_coverage=records)


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

SCENARIOS = {
    "clean_run": _clean_run,
    "partial_zero_obs": _partial_zero_obs_run,
    "degraded_critical": _degraded_critical_run,
    "l3_human_decision": _l3_human_decision_run,
    "accepted_risk_capped": _accepted_risk_capped_run,
    "floor_blocked_disposition": _floor_blocked_disposition_run,
    "improvement_trend": _improvement_trend_run,
}


# ===========================================================================
# JSON report
# ===========================================================================


class TestJsonReport:
    def test_valid_json(self):
        ra = _clean_run()
        output = render_json(ra)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_round_trip(self):
        ra = _clean_run()
        output = render_json(ra)
        parsed = json.loads(output)
        assert parsed["schema_version"] == ASSESSMENT_SCHEMA_VERSION
        assert parsed["run_id"] == "run-001"
        assert parsed["score"]["overall"] == 100

    def test_deterministic(self):
        ra = _clean_run()
        assert render_json(ra) == render_json(ra)

    def test_contains_coverage_gaps(self):
        ra = _partial_zero_obs_run()
        parsed = json.loads(render_json(ra))
        assert len(parsed["coverage_gaps"]) == 1
        assert parsed["coverage_gaps"][0]["validator_id"] == "md-lint"

    def test_contains_observations(self):
        ra = _l3_human_decision_run()
        parsed = json.loads(render_json(ra))
        assert len(parsed["observations"]) == 1
        assert parsed["observations"][0]["escalation"] == "L3"

    def test_contains_improvement(self):
        ra = _improvement_trend_run()
        parsed = json.loads(render_json(ra))
        assert parsed["improvement"]["trend"] == "improving"
        assert parsed["improvement"]["resolved_count"] == 3

    def test_contains_recovery_items(self):
        ri = RecoveryItem(
            id="rec-1",
            kind="retryable",
            description="md-lint timed out",
            validator_id="md-lint",
            context={"timeout_ms": 30000},
        )
        ra = _clean_run()
        from dataclasses import replace

        # Must replace recovery_items on the frozen dataclass
        ra_with_recovery = replace(ra, recovery_items=(ri,))
        parsed = json.loads(render_json(ra_with_recovery))
        assert len(parsed["recovery_items"]) == 1
        assert parsed["recovery_items"][0]["kind"] == "retryable"

    def test_enum_values_are_strings(self):
        ra = _l3_human_decision_run()
        output = render_json(ra)
        parsed = json.loads(output)
        assert parsed["coverage_status"] == "full"
        assert parsed["confidence"] == "guarded"
        assert isinstance(parsed["observations"][0]["severity"], str)


# ===========================================================================
# JUnit report
# ===========================================================================


class TestJunitReport:
    def test_clean_run_has_single_passing_testcase(self):
        output = render_junit(_clean_run())
        assert output.startswith('<?xml version="1.0" encoding="utf-8"?>')
        assert '<testsuite name="verdx" tests="1" failures="0" errors="0"' in output
        assert '<testcase classname="verdx.assessment" name="assessment clean" />' in output

    def test_observation_is_failure(self):
        output = render_junit(_l3_human_decision_run())
        assert 'failures="1"' in output
        assert '<failure type="L3"' in output
        assert "api-deprecation" in output

    def test_coverage_gap_is_error(self):
        output = render_junit(_partial_zero_obs_run())
        assert 'failures="0"' in output
        assert 'errors="1"' in output
        assert '<error type="coverage-gap"' in output
        assert "md-lint checks not performed" in output

    def test_subject_coverage_debt_is_error(self):
        assessment = _with_subject_coverage(
            _clean_run(),
            _make_subject_coverage(state=SubjectCoverageState.MISSING),
        )
        output = render_junit(assessment)
        assert 'errors="1"' in output
        assert '<error type="subject-coverage"' in output
        assert "outline:outline.yml is missing" in output

    def test_recovery_context_values_are_redacted(self):
        recovery = RecoveryItem(
            id="rec-secret",
            kind="retryable",
            description="validator crashed",
            validator_id="training-secret",
            context={"token": "super-secret-value", "path": "/tmp/private"},
        )
        output = render_junit(replace(_clean_run(), recovery_items=(recovery,)))
        assert "super-secret-value" not in output
        assert "/tmp/private" not in output
        assert "Context keys: path, token" in output


# ===========================================================================
# SARIF report
# ===========================================================================


class TestSarifReport:
    def test_observation_result_shape(self):
        parsed = json.loads(render_sarif(_l3_human_decision_run()))
        assert parsed["version"] == "2.1.0"
        run = parsed["runs"][0]
        result = run["results"][0]
        assert result["ruleId"] == "api-deprecation"
        assert result["level"] == "error"
        assert result["message"]["text"] == "Lab solution references deprecated API endpoint"
        assert result["properties"]["escalation"] == "L3"
        assert "producer_id" not in json.dumps(result)

    def test_coverage_gap_is_result(self):
        parsed = json.loads(render_sarif(_partial_zero_obs_run()))
        run = parsed["runs"][0]
        result = run["results"][0]
        assert result["ruleId"] == "verdx.coverage-gap"
        assert result["level"] == "error"
        assert result["properties"]["validator_id"] == "md-lint"

    def test_subject_coverage_debt_is_result(self):
        assessment = _with_subject_coverage(
            _clean_run(),
            _make_subject_coverage(state=SubjectCoverageState.MISSING),
        )
        parsed = json.loads(render_sarif(assessment))
        result = parsed["runs"][0]["results"][0]
        assert result["ruleId"] == "verdx.subject-coverage"
        assert result["level"] == "error"
        assert result["properties"]["subject_key"] == "outline:outline.yml"

    def test_recovery_context_values_are_redacted(self):
        recovery = RecoveryItem(
            id="rec-secret",
            kind="retryable",
            description="validator crashed",
            validator_id="training-secret",
            context={"token": "super-secret-value", "path": "/tmp/private"},
        )
        output = render_sarif(replace(_clean_run(), recovery_items=(recovery,)))
        assert "super-secret-value" not in output
        assert "/tmp/private" not in output
        parsed = json.loads(output)
        result = parsed["runs"][0]["results"][0]
        assert result["properties"]["context_keys"] == ["path", "token"]

    def test_clean_run_has_no_results(self):
        parsed = json.loads(render_sarif(_clean_run()))
        assert parsed["runs"][0]["results"] == []


# ===========================================================================
# Markdown report — structure
# ===========================================================================


class TestMarkdownStructure:
    def test_starts_with_title(self):
        md = render_markdown(_clean_run())
        assert md.startswith("# Assessment:")

    def test_sections_in_order(self):
        """Coverage appears before score, positive signal first."""
        md = render_markdown(_l3_human_decision_run())
        coverage_pos = md.index("## Coverage and Confidence")
        clean_pos = md.index("## Clean Summary")
        decision_pos = md.index("## Requires Human Decision")
        score_pos = md.index("## Score")
        assert coverage_pos < clean_pos < decision_pos < score_pos

    def test_improvement_before_findings(self):
        md = render_markdown(_improvement_trend_run())
        improvement_pos = md.index("## Improvement")
        awareness_pos = md.index("## For Your Awareness")
        assert improvement_pos < awareness_pos

    def test_clean_run_no_findings_sections(self):
        md = render_markdown(_clean_run())
        assert "## Requires Human Decision" not in md
        assert "## For Your Awareness" not in md
        assert "## Improvement" not in md

    def test_ends_with_artifact_pointers(self):
        md = render_markdown(_clean_run())
        lines = md.strip().split("\n")
        last_lines = "\n".join(lines[-4:])
        assert "Assessment:" in last_lines
        assert "Run:" in last_lines

    def test_deterministic(self):
        ra = _l3_human_decision_run()
        assert render_markdown(ra) == render_markdown(ra)


# ===========================================================================
# Markdown report — coverage section
# ===========================================================================


class TestMarkdownCoverage:
    def test_full_coverage_shown(self):
        md = render_markdown(_clean_run())
        assert "**Coverage**: full" in md
        assert "**Confidence**: strong" in md

    def test_partial_coverage_shown(self):
        md = render_markdown(_partial_zero_obs_run())
        assert "**Coverage**: partial" in md
        assert "**Confidence**: low" in md

    def test_degraded_coverage_shown(self):
        md = render_markdown(_degraded_critical_run())
        assert "**Coverage**: degraded" in md

    def test_coverage_gaps_listed(self):
        md = render_markdown(_partial_zero_obs_run())
        assert "### Coverage Gaps" in md
        assert "**md-lint**" in md
        assert "parser crash" in md

    def test_gap_dimensions_shown(self):
        md = render_markdown(_partial_zero_obs_run())
        assert "affects: automation" in md


class TestMarkdownSubjectCoverage:
    def test_subject_coverage_section_renders_critical_debt(self):
        subject = ValidationSubject(
            schema_version=SUBJECT_SCHEMA_VERSION,
            subject_key="outline:outline.yml",
            target_key="sample/ch04",
            kind="outline",
            label="outline.yml",
            selector=SubjectSelector(kind="subject", values=("outline:outline.yml",)),
            location="outline.yml",
        )
        record = SubjectCoverageRecord(
            schema_version=SUBJECT_COVERAGE_SCHEMA_VERSION,
            run_id="run-001",
            target_key="sample/ch04",
            target_content_hash="sha256:abc123",
            subject=subject,
            state=SubjectCoverageState.MISSING,
            criticality="critical",
            required_validator_ids=("training-structure",),
            completed_validator_ids=(),
            failed_validator_ids=(),
            skipped_validator_ids=(),
            checks_run=0,
            checks_passed=0,
            observation_fingerprints=(),
            coverage_gap_validator_ids=(),
            blocking_reason="missing: training-structure",
            model_health=SubjectModelHealth(),
        )
        md = render_markdown(
            replace(
                _clean_run(),
                subject_coverage=(record,),
                coverage_status=CoverageStatus.DEGRADED,
            )
        )
        assert "## Subject Coverage" in md
        assert "Critical Subject Debt" in md
        assert "outline:outline.yml" in md

    def test_subject_coverage_section_renders_standard_debt_by_kind(self):
        subject = ValidationSubject(
            schema_version=SUBJECT_SCHEMA_VERSION,
            subject_key="file:content/ch01/setup/ge.adoc",
            target_key="sample/ch04",
            kind="file",
            label="content/ch01/setup/ge.adoc",
            selector=SubjectSelector(
                kind="subject",
                values=("file:content/ch01/setup/ge.adoc",),
            ),
            location="content/ch01/setup/ge.adoc",
        )
        record = SubjectCoverageRecord(
            schema_version=SUBJECT_COVERAGE_SCHEMA_VERSION,
            run_id="run-001",
            target_key="sample/ch04",
            target_content_hash="sha256:abc123",
            subject=subject,
            state=SubjectCoverageState.MODEL_LIMITED,
            criticality="standard",
            required_validator_ids=("training-content",),
            completed_validator_ids=("training-content",),
            failed_validator_ids=(),
            skipped_validator_ids=(),
            checks_run=1,
            checks_passed=1,
            observation_fingerprints=(),
            coverage_gap_validator_ids=(),
            blocking_reason="unmapped field",
            model_health=SubjectModelHealth(unmapped_fields=("unmapped field",)),
        )
        md = render_markdown(
            replace(
                _clean_run(),
                subject_coverage=(record,),
                coverage_status=CoverageStatus.PARTIAL,
            )
        )
        assert "Standard Subject Debt" in md
        assert "**file**" in md
        assert "file:content/ch01/setup/ge.adoc" in md


# ===========================================================================
# Markdown report — clean summary
# ===========================================================================


class TestMarkdownCleanSummary:
    def test_checks_shown(self):
        md = render_markdown(_clean_run())
        assert "**Checks run**: 20" in md
        assert "**Checks passed**: 20" in md
        assert "**Validators clean**: 1" in md


# ===========================================================================
# Markdown report — human decisions
# ===========================================================================


class TestMarkdownHumanDecisions:
    def test_l3_observation_in_decisions(self):
        md = render_markdown(_l3_human_decision_run())
        assert "## Requires Human Decision" in md
        assert "[Requires Human Decision]" in md
        assert "deprecated API endpoint" in md

    def test_l4_observation_in_decisions(self):
        md = render_markdown(_floor_blocked_disposition_run())
        assert "## Requires Human Decision" in md
        assert "[Blocking]" in md
        assert "Credentials exposed" in md

    def test_evidence_shown(self):
        md = render_markdown(_l3_human_decision_run())
        assert "**Evidence:**" in md
        assert "api-check/deprecation" in md

    def test_invalidating_conditions_shown(self):
        md = render_markdown(_l3_human_decision_run())
        assert "**Invalidating conditions:**" in md
        assert "API endpoint is restored" in md

    def test_l1_not_in_decisions(self):
        md = render_markdown(_accepted_risk_capped_run())
        assert "## Requires Human Decision" not in md


# ===========================================================================
# Markdown report — awareness
# ===========================================================================


class TestMarkdownAwareness:
    def test_l1_observation_in_awareness(self):
        md = render_markdown(_accepted_risk_capped_run())
        assert "## For Your Awareness" in md
        assert "`cli-deprecation`" in md

    def test_grouped_by_rule_id(self):
        md = render_markdown(_accepted_risk_capped_run())
        assert "`cli-deprecation` (1 finding)" in md

    def test_disposition_noted(self):
        md = render_markdown(_accepted_risk_capped_run())
        assert "[accepted-risk]" in md


# ===========================================================================
# Markdown report — score
# ===========================================================================


class TestMarkdownScore:
    def test_score_shown(self):
        md = render_markdown(_clean_run())
        assert "**100/100**" in md

    def test_coverage_penalty_shown(self):
        md = render_markdown(_degraded_critical_run())
        assert "Coverage penalty: -30" in md

    def test_comparison_shown(self):
        md = render_markdown(_improvement_trend_run())
        assert "+20" in md
        assert "was 65" in md

    def test_caveats_for_non_full_coverage(self):
        md = render_markdown(_partial_zero_obs_run())
        assert "**Caveats:**" in md
        assert "partial" in md

    def test_no_caveats_for_full_coverage(self):
        md = render_markdown(_clean_run())
        assert "**Caveats:**" not in md


# ===========================================================================
# Markdown report — improvement
# ===========================================================================


class TestMarkdownImprovement:
    def test_improvement_shown(self):
        md = render_markdown(_improvement_trend_run())
        assert "## Improvement" in md
        assert "trending better" in md
        assert "**Resolved**: 3" in md
        assert "**Persistent**: 1" in md

    def test_no_improvement_section_without_data(self):
        md = render_markdown(_clean_run())
        assert "## Improvement" not in md


# ===========================================================================
# Markdown report — recovery
# ===========================================================================


class TestMarkdownRecovery:
    def test_recovery_items_shown(self):
        from dataclasses import replace

        ri = RecoveryItem(
            id="rec-1",
            kind="blocked",
            description="Lock file held by another process",
            validator_id="file-lock",
            context={},
        )
        ra = replace(_clean_run(), recovery_items=(ri,))
        md = render_markdown(ra)
        assert "## Recovery Items" in md
        assert "[blocked]" in md
        assert "Lock file held" in md

    def test_no_recovery_section_when_empty(self):
        md = render_markdown(_clean_run())
        assert "## Recovery Items" not in md


# ===========================================================================
# Privacy enforcement
# ===========================================================================


class TestPrivacyEnforcement:
    """No human-readable report may contain individual actor identity."""

    def _assert_no_actor_fields(self, md: str) -> None:
        assert "producer_id" not in md
        assert "actor_kind" not in md
        assert "opaque-" not in md

    def test_clean_run_no_actor_fields(self):
        self._assert_no_actor_fields(render_markdown(_clean_run()))

    def test_l3_run_no_actor_fields(self):
        md = render_markdown(_l3_human_decision_run())
        assert "producer_id" not in md
        assert "actor_kind" not in md

    def test_disposition_run_no_actor_fields(self):
        md = render_markdown(_accepted_risk_capped_run())
        assert "producer_id" not in md
        assert "actor_kind" not in md

    def test_observation_with_producer_id_not_leaked(self):
        """Even if producer_id is set, Markdown must not include it."""
        obs = _make_observation(
            producer_id="opaque-secret-12345",
            escalation=EscalationLevel.L3,
            summary="Test finding",
        )
        ra = _make_run_assessment(observations=(obs,))
        md = render_markdown(ra)
        assert "opaque-secret-12345" not in md

    def test_independence_status_shown_without_machine_fields(self):
        """Markdown shows process status, not policy internals."""
        md = render_markdown(_l3_human_decision_run())
        assert "**Four-Eyes**: satisfied (independent)" in md
        assert "verdx-v1" not in md
        assert "policy_id" not in md
        assert "validator_role" not in md

    def test_unmet_independence_status_shown_without_actor_identity(self):
        obs = _make_observation(
            producer_id="opaque-secret-12345",
            independence=_make_independence(relation="same-team", result="unmet"),
            escalation=EscalationLevel.L3,
        )
        md = render_markdown(_make_run_assessment(observations=(obs,)))
        assert "**Four-Eyes**: unmet (same-team)" in md
        assert "opaque-secret-12345" not in md

    def test_json_does_contain_provenance(self):
        """JSON (machine-readable) DOES include full provenance."""
        ra = _l3_human_decision_run()
        parsed = json.loads(render_json(ra))
        obs = parsed["observations"][0]
        assert "producer_role" in obs
        assert "independence" in obs


# ===========================================================================
# Golden fixture snapshots
# ===========================================================================


class TestGoldenFixtures:
    """Snapshot tests — compare rendered output against stored fixtures.

    Run with --update-fixtures to regenerate (see conftest or pytest plugin).
    On first run or when fixtures are missing, generates them automatically.
    """

    def _fixture_path(self, name: str, ext: str) -> Path:
        return FIXTURES / f"{name}.{ext}"

    def _assert_or_create(self, path: Path, content: str) -> None:
        if path.exists():
            expected = path.read_text()
            assert content == expected, (
                f"Fixture mismatch: {path}\nRegenerate with: pytest --update-fixtures"
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def test_clean_run_md(self):
        self._assert_or_create(
            self._fixture_path("clean_run", "md"),
            render_markdown(_clean_run()),
        )

    def test_partial_zero_obs_md(self):
        self._assert_or_create(
            self._fixture_path("partial_zero_obs", "md"),
            render_markdown(_partial_zero_obs_run()),
        )

    def test_degraded_critical_md(self):
        self._assert_or_create(
            self._fixture_path("degraded_critical", "md"),
            render_markdown(_degraded_critical_run()),
        )

    def test_l3_human_decision_md(self):
        self._assert_or_create(
            self._fixture_path("l3_human_decision", "md"),
            render_markdown(_l3_human_decision_run()),
        )

    def test_accepted_risk_capped_md(self):
        self._assert_or_create(
            self._fixture_path("accepted_risk_capped", "md"),
            render_markdown(_accepted_risk_capped_run()),
        )

    def test_floor_blocked_disposition_md(self):
        self._assert_or_create(
            self._fixture_path("floor_blocked_disposition", "md"),
            render_markdown(_floor_blocked_disposition_run()),
        )

    def test_improvement_trend_md(self):
        self._assert_or_create(
            self._fixture_path("improvement_trend", "md"),
            render_markdown(_improvement_trend_run()),
        )

    def test_clean_run_json(self):
        self._assert_or_create(
            self._fixture_path("clean_run", "json"),
            render_json(_clean_run()),
        )

    def test_clean_run_junit(self):
        self._assert_or_create(
            self._fixture_path("clean_run", "xml"),
            render_junit(_clean_run()),
        )

    def test_partial_zero_obs_sarif(self):
        self._assert_or_create(
            self._fixture_path("partial_zero_obs", "sarif"),
            render_sarif(_partial_zero_obs_run()),
        )
