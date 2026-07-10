"""Workflow orchestration — chains plan → execute → assess → render.

``run_gate_workflow()`` is the single entry point for the new pipeline.
It accepts a ``DomainSpec`` (from ``contracts/domain.py``), and drives
the full pipeline to produce a ``RunOutcome`` compatible with existing
CLI and store code.

Coexists with ``engine.py``; ``assess_cmd`` and ``run_cmd`` still use
the old engine for stage-based validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from verdx.engine.runtime.assessor import derive_assessment
from verdx.engine.runtime.engine import RunOutcome
from verdx.engine.runtime.executor import execute_plan
from verdx.engine.runtime.planner import resolve_gate_plan
from verdx.engine.runtime.store.check_cache import CheckCacheStore
from verdx.engine.validation.coverage import derive_subject_coverage
from verdx.engine.validation.reporting import render_json, render_markdown

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from verdx.contracts.config import ScoringConfig
    from verdx.contracts.disposition import Disposition
    from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
    from verdx.contracts.subjects import SubjectCoverageRecord
    from verdx.engine.runtime.executor import ExecutionResult
    from verdx.engine.validation.escalation import EscalationPolicy
    from verdx.engine.validation.independence import ActorFacts


def run_gate_workflow(
    *,
    domain: DomainSpec | None = None,
    target: Path,
    target_key: str,
    state_dir: Path,
    policy: EscalationPolicy,
    threshold: int = 80,
    static_only: bool = False,
    no_cache: bool = False,
    active_dispositions: dict[str, Disposition] | None = None,
    producer_role: str = "content-developer",
    producer_id: str | None = None,
    producer_actor: ActorFacts | None = None,
    validator_actors: dict[str, ActorFacts] | None = None,
    previous_fingerprints: set[str] | None = None,
    previous_score: int | None = None,
    scoring_config: ScoringConfig | None = None,
    dimension_map: dict[str, list[str]] | None = None,
    critical_validator_ids: frozenset[str] = frozenset(),
    cli_overrides: Mapping[str, Any] | None = None,
    profiles: tuple[str, ...] = (),
    run_id: str | None = None,
    timestamp: str | None = None,
    changed_files: frozenset[str] | None = None,
) -> RunOutcome:
    """Execute the full verdx pipeline and return a RunOutcome.

    Chains: plan → execute → discover subjects → assess → render.
    """
    if domain is None:
        raise ValueError("domain is required")

    effective_run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    effective_timestamp = timestamp or datetime.now(tz=UTC).isoformat()

    plan = resolve_gate_plan(
        target=target,
        available_domains={domain.name: domain},
        requested_domains=(domain.name,),
        state_dir=state_dir,
        static_only=static_only,
        no_cache=no_cache,
        profiles=profiles,
        threshold=threshold,
        run_id=effective_run_id,
        timestamp=effective_timestamp,
        cli_overrides=cli_overrides or {},
        target_key=target_key,
        changed_files=changed_files,
    )

    cache_store = CheckCacheStore(state_dir) if not no_cache else None
    execution_result = execute_plan(plan, cache_store=cache_store)

    assess_kwargs: dict[str, Any] = dict(
        policy=policy,
        active_dispositions=active_dispositions,
        producer_role=producer_role,
        producer_id=producer_id,
        producer_actor=producer_actor,
        validator_actors=validator_actors,
        previous_fingerprints=previous_fingerprints,
        previous_score=previous_score,
        scoring_config=scoring_config,
        critical_validator_ids=critical_validator_ids or None,
        dimension_map=dimension_map,
    )

    subject_coverage = _run_subject_discovery(
        domain, execution_result, plan, target, target_key, assess_kwargs
    )

    assessment_result = derive_assessment(
        execution_result,
        plan,
        **assess_kwargs,
        subject_coverage=subject_coverage,
    )

    assessment = assessment_result.assessment
    assessment_json = render_json(assessment)
    report_md = render_markdown(assessment)

    return RunOutcome(
        assessment=assessment,
        assessment_json=assessment_json,
        report_md=report_md,
        observations=list(assessment_result.observations),
        validator_statuses=list(assessment.validator_statuses),
        recovery_items=list(assessment.recovery_items),
    )


def _run_subject_discovery(
    domain: DomainSpec,
    execution_result: ExecutionResult,
    plan: Any,
    target: Path,
    target_key: str,
    assess_kwargs: dict[str, Any],
) -> tuple[SubjectCoverageRecord, ...]:
    """Discover subjects and derive coverage records."""
    from verdx.contracts.domain import SubjectDiscoveryResult

    try:
        discovery: SubjectDiscoveryResult = domain.discover_subjects(target, target_key)
    except Exception:
        return ()

    if not isinstance(discovery, SubjectDiscoveryResult) or not discovery.subjects:
        return ()

    preliminary = derive_assessment(
        execution_result,
        plan,
        **assess_kwargs,
        subject_coverage=(),
    )

    derivation = derive_subject_coverage(
        run_id=plan.run_id,
        target_key=target_key,
        target_content_hash=plan.target_content_hash,
        subjects=discovery.subjects,
        expected_subjects=discovery.expected_subjects,
        validator_statuses=tuple(preliminary.assessment.validator_statuses),
        observations=preliminary.observations,
        validator_subject_keys_checked={
            r.validator_id: frozenset(r.subject_keys_checked)
            for r in execution_result.results
            if r.subject_keys_checked
        },
        validator_subject_keys_skipped={
            r.validator_id: frozenset(r.subject_keys_skipped)
            for r in execution_result.results
            if r.subject_keys_skipped
        },
        validator_subject_selectors=discovery.validator_subject_selectors,
        model_health_by_subject_key=discovery.model_health_by_subject_key,
    )

    return derivation.records
