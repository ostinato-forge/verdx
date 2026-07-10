"""Stable public API for verdx integrators.

This module intentionally re-exports a small set of contracts needed by
downstream tools such as egon and agentic-sdlc. Internal modules may continue
to evolve; imports from this module should remain conservative and documented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.assessment import (
    RunAssessment,
    Score,
    ValidatorStatus,
)

# --- contracts ---
from verdx.contracts.audit import (
    ApprovalRecord,
    ArtifactRef,
    ComplianceSummary,
    TimelineEntry,
)
from verdx.contracts.config import ScoringConfig
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.disposition import Disposition
from verdx.contracts.independence import IndependenceRecord
from verdx.contracts.observation import Evidence, Observation, ObservationDraft
from verdx.contracts.validator import ValidationContext, Validator, ValidatorResult

# --- lifecycle ---
from verdx.engine.lifecycle.audit import (
    approval_chain,
    compliance_summary,
    evidence_at_gate,
    format_compliance_report,
    format_trace,
    timeline,
    who_approved,
)
from verdx.engine.lifecycle.authority import allowed_roles, is_role_permitted
from verdx.engine.lifecycle.fast_path import ChangeDescription, EligibilityResult, classify
from verdx.engine.lifecycle.gates import required_artifact
from verdx.engine.lifecycle.reducer import IntentState, ReducerResult, apply, reduce
from verdx.engine.lifecycle.replay import IntentReplayResult, replay
from verdx.engine.lifecycle.transitions import resolve_transition

# --- runtime (new pipeline) ---
from verdx.engine.runtime.assessor import AssessmentResult, derive_assessment

# --- runtime ---
from verdx.engine.runtime.config import (
    DomainConfig,
    load_config,
    load_domain_config,
    load_escalation_policy,
)
from verdx.engine.runtime.engine import RunConfig, RunOutcome, run_validation
from verdx.engine.runtime.executor import ExecutionResult, execute_plan
from verdx.engine.runtime.planner import resolve_gate_plan
from verdx.engine.runtime.projector import project_evidence

# --- runtime.store ---
from verdx.engine.runtime.store.actors import ActorDirectory, ActorEntry
from verdx.engine.runtime.store.dispositions import DispositionStore
from verdx.engine.runtime.store.history import HistoryStore, ObservationRecord, RunRecord
from verdx.engine.runtime.store.runs import RunStore
from verdx.engine.runtime.workflow import run_gate_workflow

# --- validation ---
from verdx.engine.validation.canonicalize import Provenance, canonicalize
from verdx.engine.validation.escalation import EscalationPolicy
from verdx.engine.validation.independence import (
    FOUR_EYES_POLICY_ID,
    ActorFacts,
    evaluate_independence,
)
from verdx.engine.validation.reporting import (
    render_json,
    render_junit,
    render_markdown,
    render_sarif,
)
from verdx.engine.validation.scoring import assemble_assessment

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "ActorDirectory",
    "ActorEntry",
    "ActorFacts",
    "ApprovalRecord",
    "ArtifactRef",
    "AssessmentResult",
    "ChangeDescription",
    "ComplianceSummary",
    "Confidence",
    "CoverageStatus",
    "DimLevel",
    "Disposition",
    "DispositionStore",
    "DomainConfig",
    "EligibilityResult",
    "EscalationLevel",
    "EscalationPolicy",
    "Evidence",
    "ExecutionResult",
    "HistoryStore",
    "IndependenceRecord",
    "IntentReplayResult",
    "IntentState",
    "Observation",
    "ObservationDraft",
    "ObservationRecord",
    "ReducerResult",
    "RunAssessment",
    "RunConfig",
    "RunOutcome",
    "RunRecord",
    "RunStore",
    "Score",
    "ScoringConfig",
    "Severity",
    "TimelineEntry",
    "ValidationContext",
    "Validator",
    "ValidatorResult",
    "ValidatorStatus",
    "allowed_roles",
    "apply",
    "approval_chain",
    "assemble_assessment",
    "assess_observation_drafts",
    "canonicalize",
    "classify",
    "compliance_summary",
    "derive_assessment",
    "evaluate_independence",
    "evidence_at_gate",
    "execute_plan",
    "format_compliance_report",
    "format_trace",
    "is_role_permitted",
    "load_config",
    "load_domain_config",
    "load_escalation_policy",
    "project_evidence",
    "reduce",
    "render_json",
    "render_junit",
    "render_markdown",
    "render_sarif",
    "replay",
    "required_artifact",
    "resolve_gate_plan",
    "resolve_transition",
    "run_gate_workflow",
    "run_validation",
    "timeline",
    "who_approved",
]


def assess_observation_drafts(
    drafts: Iterable[ObservationDraft],
    *,
    run_id: str,
    target_key: str,
    target_content_hash: str,
    timestamp: str,
    policy: EscalationPolicy,
    producer_role: str,
    validator_id: str,
    validator_version: str,
    producer_id: str | None = None,
    validator_role: str | None = None,
    producer_actor: ActorFacts | None = None,
    validator_actor: ActorFacts | None = None,
    independence: IndependenceRecord | None = None,
    active_dispositions: dict[str, Disposition] | None = None,
    previous_fingerprints: set[str] | None = None,
    previous_score: int | None = None,
    checks_run: int | None = None,
    checks_passed: int = 0,
    validator_status: str = "skipped",
    error: str | None = None,
    skip_reason: str | None = None,
    duration_ms: int = 0,
    scoring_config: ScoringConfig | None = None,
) -> RunAssessment:
    """Canonicalize existing drafts into a RunAssessment.

    This helper is for integrators that already have structured findings from
    another engine and need a canonical verdx assessment without running
    validators. It keeps all policy, provenance, and timestamp inputs explicit.
    By default, imported drafts are marked skipped and Four-Eyes independence
    is unknown, so the assessment stays partial/low-confidence until the caller
    supplies completed validator status, real positive coverage counts, and
    actor facts or an explicit independence record.
    """
    draft_list = list(drafts)
    effective_checks_run = 0 if checks_run is None else checks_run
    effective_skip_reason = skip_reason
    if validator_status == "skipped" and effective_skip_reason is None:
        effective_skip_reason = "draft import does not prove full validation coverage"
    status = ValidatorStatus(
        validator_id=validator_id,
        validator_version=validator_version,
        status=validator_status,
        checks_run=effective_checks_run,
        checks_passed=checks_passed,
        observation_count=len(draft_list),
        duration_ms=duration_ms,
        error=error,
        skip_reason=effective_skip_reason,
    )
    effective_validator_role = validator_role or validator_id
    effective_independence = _resolve_import_independence(
        producer_role=producer_role,
        validator_role=effective_validator_role,
        producer_actor=producer_actor,
        validator_actor=validator_actor,
        independence=independence,
    )
    provenance = Provenance(
        producer_role=producer_role,
        producer_id=producer_id,
        validator_id=validator_id,
        validator_version=validator_version,
        independence=effective_independence,
    )
    observations = canonicalize(
        run_id=run_id,
        drafts=draft_list,
        provenance=provenance,
        policy=policy,
        active_dispositions=active_dispositions or {},
    )
    return assemble_assessment(
        run_id=run_id,
        target_key=target_key,
        target_content_hash=target_content_hash,
        timestamp=timestamp,
        observations=observations,
        validator_statuses=[status],
        critical_validator_ids=frozenset(),
        recovery_items=[],
        previous_fingerprints=previous_fingerprints,
        previous_score=previous_score,
        scoring_config=scoring_config,
    )


def _resolve_import_independence(
    *,
    producer_role: str,
    validator_role: str,
    producer_actor: ActorFacts | None,
    validator_actor: ActorFacts | None,
    independence: IndependenceRecord | None,
) -> IndependenceRecord:
    if independence is not None:
        if (
            independence.producer_role != producer_role
            or independence.validator_role != validator_role
        ):
            raise ValueError("independence must match producer_role and validator_role")
        return independence

    if producer_actor is not None or validator_actor is not None:
        return evaluate_independence(
            producer_role=producer_role,
            validator_role=validator_role,
            producer_actor=producer_actor,
            validator_actor=validator_actor,
        )

    return IndependenceRecord(
        producer_role=producer_role,
        validator_role=validator_role,
        actor_kind="system",
        relation="unknown",
        policy_id=FOUR_EYES_POLICY_ID,
        result="unmet",
    )
