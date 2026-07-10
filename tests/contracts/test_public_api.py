"""Public API import stability tests."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import verdx.engine
import verdx.engine.runtime.api as api


def _register_stub_domain(monkeypatch, tmp_path: Path, name: str = "stub") -> None:
    """Register a stub domain in the runtime registry whose config is real.

    The engine names no domain, so these API tests supply their own via
    the registry rather than depending on any installed domain package.
    """
    from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
    from verdx.engine.runtime import domains as domains_mod

    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text(
        textwrap.dedent("""\
            version: 1
            escalation_policy:
              version: 1
              floors:
                - category: exercise
                  minimum: L3
              rules:
                - name: high
                  match:
                    quantifier: any
                    dimension: "*"
                    level: HIGH
                  level: L3
                - name: fallback
                  match:
                    quantifier: all
                    dimension: "*"
                    level: LOW
                  level: L0
            critical_validators: []
            dimensions:
              - correctness
            evidence_limits:
              max_evidence_bytes: 65536
              max_string_length: 4096
              max_data_entries: 50
            timeout_seconds: 300
        """)
    )
    cfg = api.load_config(cfg_file)
    spec = DomainSpec(
        name=name,
        markers=(),
        load_default_config=lambda: cfg,
        load_checks=lambda: (),
        load_profiles=lambda: (),
        discover_subjects=lambda _path, _target: SubjectDiscoveryResult(),
    )
    monkeypatch.setattr(domains_mod, "_discovered", {name: spec})
    monkeypatch.setattr(domains_mod, "_load_errors", {})


def test_public_api_exports_expected_contracts() -> None:
    expected = {
        # contracts
        "ActorFacts",
        "ApprovalRecord",
        "ArtifactRef",
        "ChangeDescription",
        "ComplianceSummary",
        "Confidence",
        "CoverageStatus",
        "DimLevel",
        "Disposition",
        "DomainConfig",
        "EligibilityResult",
        "EscalationLevel",
        "EscalationPolicy",
        "Evidence",
        "IndependenceRecord",
        "Observation",
        "ObservationDraft",
        "RunAssessment",
        "Score",
        "ScoringConfig",
        "Severity",
        "TimelineEntry",
        "ValidationContext",
        "Validator",
        "ValidatorResult",
        "ValidatorStatus",
        # lifecycle
        "IntentReplayResult",
        "IntentState",
        "ReducerResult",
        "allowed_roles",
        "apply",
        "approval_chain",
        "classify",
        "compliance_summary",
        "evidence_at_gate",
        "format_compliance_report",
        "format_trace",
        "is_role_permitted",
        "reduce",
        "replay",
        "required_artifact",
        "resolve_transition",
        "timeline",
        "who_approved",
        # validation
        "assemble_assessment",
        "canonicalize",
        "evaluate_independence",
        # runtime
        "ActorDirectory",
        "ActorEntry",
        "DispositionStore",
        "HistoryStore",
        "ObservationRecord",
        "RunConfig",
        "RunOutcome",
        "RunRecord",
        "RunStore",
        "assess_observation_drafts",
        "load_config",
        "load_domain_config",
        "load_escalation_policy",
        "render_json",
        "render_junit",
        "render_markdown",
        "render_sarif",
        "run_validation",
        # runtime (new pipeline)
        "AssessmentResult",
        "ExecutionResult",
        "derive_assessment",
        "execute_plan",
        "project_evidence",
        "resolve_gate_plan",
        "run_gate_workflow",
    }

    assert set(api.__all__) == expected
    for name in expected:
        assert getattr(api, name) is not None


def test_public_api_imports_from_clean_python_process() -> None:
    code = (
        "from verdx.engine.runtime.api import RunConfig, ObservationDraft, run_validation; "
        "print(RunConfig.__name__, ObservationDraft.__name__, callable(run_validation))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "RunConfig ObservationDraft True"


def test_assess_observation_drafts_returns_canonical_assessment(monkeypatch, tmp_path) -> None:
    _register_stub_domain(monkeypatch, tmp_path)
    policy = api.load_domain_config("stub").policy
    draft = api.ObservationDraft(
        target_key="qa-gate/target-0022",
        location="control-troubleshoot",
        rule_id="qa-gate.tc-stepseq",
        category="content-accuracy",
        severity=api.Severity.MEDIUM,
        confidence=api.Confidence.CONFIRMED,
        dimensions={"structural-integrity": api.DimLevel.LOW},
        summary="Step numbering gaps make exercise instructions unreliable.",
        evidence=(
            api.Evidence(
                source="qa-gate",
                kind="precheck-stepseq",
                details="Static step sequence analysis detected a gap.",
                data={"finding_id": "runner:first"},
            ),
        ),
    )

    assessment = api.assess_observation_drafts(
        [draft],
        run_id="run-qa-gate-001",
        target_key="qa-gate/target-0022",
        target_content_hash="sha256:qa-gate",
        timestamp="2026-05-02T00:00:00Z",
        policy=policy,
        producer_role="course-content",
        validator_id="qa-gate",
        validator_version="0.1.0",
    )

    assert assessment.run_id == "run-qa-gate-001"
    assert assessment.target_key == "qa-gate/target-0022"
    assert assessment.score.observation_count == 1
    assert assessment.observations[0].validator_id == "qa-gate"
    assert assessment.observations[0].confidence.value == "possible"
    assert assessment.coverage_status.value == "partial"
    assert assessment.confidence.value == "low"
    assert assessment.validator_statuses[0].status == "skipped"
    assert "does not prove full validation coverage" in assessment.coverage_gaps[0].reason
    assert assessment.observations[0].issue_fingerprint.startswith("qu:v1:")


def test_assess_observation_drafts_accepts_actor_facts(monkeypatch, tmp_path) -> None:
    _register_stub_domain(monkeypatch, tmp_path)
    policy = api.load_domain_config("stub").policy
    draft = api.ObservationDraft(
        target_key="qa-gate/target-0022",
        location="control-troubleshoot",
        rule_id="qa-gate.tc-stepseq",
        category="content-accuracy",
        severity=api.Severity.MEDIUM,
        confidence=api.Confidence.CONFIRMED,
        dimensions={"structural-integrity": api.DimLevel.LOW},
        summary="Step numbering gaps make exercise instructions unreliable.",
    )

    assessment = api.assess_observation_drafts(
        [draft],
        run_id="run-qa-gate-actor-001",
        target_key="qa-gate/target-0022",
        target_content_hash="sha256:qa-gate",
        timestamp="2026-05-02T00:00:00Z",
        policy=policy,
        producer_role="course-content",
        validator_id="qa-gate",
        validator_version="0.1.0",
        producer_actor=api.ActorFacts(
            role="course-content",
            kind="human",
            team="curriculum",
        ),
        validator_actor=api.ActorFacts(
            role="qa-gate",
            kind="system",
            team="quality",
        ),
    )

    assert assessment.observations[0].confidence.value == "confirmed"
    assert assessment.observations[0].independence.relation == "independent"


def test_cli_version_matches_package_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "verdx.engine", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert verdx.engine.__version__ in result.stdout
