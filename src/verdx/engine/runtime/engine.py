"""Runner engine: orchestrates validators, core, and store.

The engine is the I/O boundary. It:
1. Executes validators with timeout
2. Bounds evidence
3. Attaches provenance
4. Calls pure core functions
5. Persists artifacts via store
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from verdx.contracts.assessment import (
    RecoveryItem,
    ValidatorStatus,
)
from verdx.contracts.diagnostic import DiagnosticEvent
from verdx.contracts.validator import ValidationContext, ValidatorResult
from verdx.engine.runtime.bounding import bound_drafts
from verdx.engine.runtime.pipeline import (
    PipelineConfig,
    StageResult,
    evaluate_gate,
)
from verdx.engine.validation.canonicalize import Provenance, canonicalize
from verdx.engine.validation.coverage import derive_subject_coverage
from verdx.engine.validation.independence import ActorFacts, evaluate_independence
from verdx.engine.validation.reporting import render_json, render_markdown
from verdx.engine.validation.scoring import assemble_assessment

if TYPE_CHECKING:
    from collections.abc import Callable

    from verdx.contracts.assessment import RunAssessment
    from verdx.contracts.config import ScoringConfig
    from verdx.contracts.disposition import Disposition
    from verdx.contracts.observation import Observation
    from verdx.contracts.subjects import SubjectCoverageRecord
    from verdx.contracts.validator import Validator
    from verdx.engine.runtime.hooks import HookRegistry
    from verdx.engine.runtime.store.cache import CacheStore
    from verdx.engine.runtime.store.diagnostic import DiagnosticLog
    from verdx.engine.validation.escalation import EscalationPolicy


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Configuration for a single validation run."""

    run_id: str
    target_key: str
    target_path: str
    target_content_hash: str
    timestamp: str
    producer_role: str
    producer_id: str | None
    policy: EscalationPolicy
    validators: list[tuple[Validator, dict[str, Any]]]
    critical_validator_ids: frozenset[str]
    active_dispositions: dict[str, Disposition]
    previous_fingerprints: set[str] | None
    previous_score: int | None
    producer_actor: ActorFacts | None = None
    validator_actors: dict[str, ActorFacts] | None = None
    timeout_seconds: int = 300
    dimension_map: dict[str, list[str]] | None = None
    subject_discovery: Callable[[Path, str], Any] | None = None
    not_applicable_subject_keys: frozenset[str] = frozenset()
    scoring_config: ScoringConfig | None = None
    no_cache: bool = False
    pre_computed_results: list[ValidatorResult] | None = None


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Result of a complete validation run."""

    assessment: RunAssessment
    assessment_json: str
    report_md: str
    observations: list[Observation]
    validator_statuses: list[ValidatorStatus]
    recovery_items: list[RecoveryItem]


def _execute_with_cache(
    config: RunConfig,
    cache_store: CacheStore | None,
    diagnostic_log: DiagnosticLog | None,
    hook_registry: HookRegistry | None,
) -> tuple[list[ValidatorResult], list[RecoveryItem]]:
    """Execute validators, using per-validator cache when available.

    For each validator:
    1. Check if a cached ValidatorResult exists for this
       (content_hash, validator_id, validator_version).
    2. On hit: use the cached result (skip execution).
    3. On miss: execute the validator, then cache the result.

    This caches the expensive part (running validators) while ensuring
    that assessment derivation (scoring, escalation, dispositions) is
    always recomputed fresh from the results.
    """
    if config.pre_computed_results is not None:
        return config.pre_computed_results, []

    use_cache = cache_store is not None and not config.no_cache
    results: list[ValidatorResult] = []
    recovery_items: list[RecoveryItem] = []

    context = ValidationContext(
        run_id=config.run_id,
        target_key=config.target_key,
        target_path=config.target_path,
        target_content_hash=config.target_content_hash,
        config={},
        previous_observations=[],
        timeout_seconds=config.timeout_seconds,
    )

    for validator, validator_config in config.validators:
        vid = getattr(validator, "validator_id", "unknown")
        vver = getattr(validator, "validator_version", "0.0.0")

        # --- per-validator cache check ---
        if use_cache and cache_store is not None:
            cached = cache_store.get(config.target_content_hash, vid, vver)
            if cached is not None:
                if diagnostic_log is not None:
                    diagnostic_log.emit(
                        DiagnosticEvent(
                            timestamp=datetime.now(UTC).isoformat(),
                            run_id=config.run_id,
                            phase="validator-cache-hit",
                            validator_id=vid,
                            status="completed",
                            duration_ms=0,
                            message=f"Validator {vid} result from cache",
                            context={},
                        )
                    )
                results.append(cached)
                continue

        # --- execute validator ---
        ctx = ValidationContext(
            run_id=context.run_id,
            target_key=context.target_key,
            target_path=context.target_path,
            target_content_hash=context.target_content_hash,
            config=validator_config,
            previous_observations=context.previous_observations,
            timeout_seconds=context.timeout_seconds,
        )

        if diagnostic_log is not None:
            diagnostic_log.emit(
                DiagnosticEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    run_id=config.run_id,
                    phase="validator-start",
                    validator_id=vid,
                    status="started",
                    duration_ms=None,
                    message=f"Starting validator {vid}",
                    context={},
                )
            )

        start = time.monotonic()
        crashed = False
        try:
            result = validator(ctx)
        except Exception as exc:
            crashed = True
            result = ValidatorResult(
                validator_id=vid,
                validator_version=vver,
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=0,
                status="failed",
                error=str(exc),
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if crashed:
            result = ValidatorResult(
                validator_id=result.validator_id,
                validator_version=result.validator_version,
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=elapsed_ms,
                status="failed",
                error=result.error,
            )
            recovery_items.append(
                RecoveryItem(
                    id=f"recovery-{config.run_id}-{result.validator_id}",
                    kind="retryable",
                    description=f"Validator {result.validator_id} crashed: {result.error}",
                    validator_id=result.validator_id,
                    context={"error": result.error or ""},
                ),
            )
            if diagnostic_log is not None:
                diagnostic_log.emit(
                    DiagnosticEvent(
                        timestamp=datetime.now(UTC).isoformat(),
                        run_id=config.run_id,
                        phase="validator-failed",
                        validator_id=result.validator_id,
                        status="failed",
                        duration_ms=elapsed_ms,
                        message=f"Validator {result.validator_id} crashed",
                        context={"error": result.error or ""},
                    )
                )

        timeout_ms = config.timeout_seconds * 1000
        if not crashed and elapsed_ms > timeout_ms:
            result = ValidatorResult(
                validator_id=vid,
                validator_version=vver,
                drafts=[],
                checks_run=result.checks_run,
                checks_passed=0,
                duration_ms=elapsed_ms,
                status="failed",
                error=(f"validator exceeded soft timeout ({elapsed_ms}ms > {timeout_ms}ms)"),
            )
            recovery_items.append(
                RecoveryItem(
                    id=f"recovery-{config.run_id}-{vid}-timeout",
                    kind="blocked",
                    description=(
                        f"Validator {vid} exceeded soft timeout ({elapsed_ms}ms > {timeout_ms}ms)"
                    ),
                    validator_id=vid,
                    context={
                        "elapsed_ms": elapsed_ms,
                        "timeout_ms": timeout_ms,
                    },
                ),
            )
            if diagnostic_log is not None:
                diagnostic_log.emit(
                    DiagnosticEvent(
                        timestamp=datetime.now(UTC).isoformat(),
                        run_id=config.run_id,
                        phase="validator-timeout",
                        validator_id=vid,
                        status="failed",
                        duration_ms=elapsed_ms,
                        message=f"Validator {vid} exceeded soft timeout",
                        context={"elapsed_ms": elapsed_ms, "timeout_ms": timeout_ms},
                    )
                )

        if not crashed and elapsed_ms <= timeout_ms and diagnostic_log is not None:
            diagnostic_log.emit(
                DiagnosticEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    run_id=config.run_id,
                    phase="validator-complete",
                    validator_id=result.validator_id,
                    status="completed",
                    duration_ms=elapsed_ms,
                    message=f"Validator {result.validator_id} completed",
                    context={
                        "checks_run": result.checks_run,
                        "checks_passed": result.checks_passed,
                        "observation_count": len(result.drafts),
                    },
                )
            )

        if hook_registry is not None:
            from verdx.engine.runtime.hooks import dispatch_hook

            dispatch_hook(
                hook_registry,
                "validator-complete",
                {
                    "run_id": config.run_id,
                    "validator_id": result.validator_id,
                    "status": result.status,
                    "checks_run": result.checks_run,
                    "checks_passed": result.checks_passed,
                    "observation_count": len(result.drafts),
                    "duration_ms": elapsed_ms,
                },
            )

        # --- cache the result (only successful/completed results) ---
        if use_cache and cache_store is not None and result.status == "completed":
            cache_store.put(
                config.target_content_hash,
                vid,
                vver,
                result,
            )

        results.append(result)

    return results, recovery_items


def run_validation(
    config: RunConfig,
    diagnostic_log: DiagnosticLog | None = None,
    cache_store: CacheStore | None = None,
    hook_registry: HookRegistry | None = None,
) -> RunOutcome:
    """Execute a complete validation run.

    Two-layer design:
    1. **Validator execution** (cached per validator): The expensive
       part.  Each validator result is cached by
       (content_hash, validator_id, validator_version).
    2. **Assessment derivation** (always fresh): Canonicalization,
       escalation, scoring, and reporting always run from scratch
       using current policy, dispositions, and scoring config.

    This ensures policy/disposition/scoring changes are always reflected
    without re-running validators.
    """
    if diagnostic_log is not None:
        diagnostic_log.emit(
            DiagnosticEvent(
                timestamp=datetime.now(UTC).isoformat(),
                run_id=config.run_id,
                phase="run-start",
                validator_id=None,
                status="started",
                duration_ms=None,
                message="Validation run started",
                context={
                    "target_key": config.target_key,
                    "validator_count": len(config.validators),
                },
            )
        )

    if hook_registry is not None:
        from verdx.engine.runtime.hooks import dispatch_hook

        dispatch_hook(
            hook_registry,
            "run-start",
            {
                "run_id": config.run_id,
                "target_key": config.target_key,
                "validator_count": len(config.validators),
            },
        )

    run_start = time.monotonic()

    # Layer 1: validator execution (with per-validator caching)
    results, recovery_items = _execute_with_cache(
        config,
        cache_store,
        diagnostic_log,
        hook_registry,
    )
    validator_statuses = [
        ValidatorStatus(
            validator_id=r.validator_id,
            validator_version=r.validator_version,
            status=r.status,
            checks_run=r.checks_run,
            checks_passed=r.checks_passed,
            observation_count=len(r.drafts),
            duration_ms=r.duration_ms,
            error=r.error,
            skip_reason=r.skip_reason,
        )
        for r in results
    ]

    # Layer 2: assessment derivation (always fresh)
    all_observations = []
    for result in results:
        if result.status != "completed" or not result.drafts:
            continue

        bounded_drafts = bound_drafts(result.drafts)
        independence = evaluate_independence(
            producer_role=config.producer_role,
            validator_role=result.validator_id,
            producer_actor=config.producer_actor,
            validator_actor=(
                config.validator_actors.get(result.validator_id)
                if config.validator_actors is not None
                else None
            ),
        )
        provenance = Provenance(
            producer_role=config.producer_role,
            producer_id=config.producer_id,
            validator_id=result.validator_id,
            validator_version=result.validator_version,
            independence=independence,
        )

        observations = canonicalize(
            run_id=config.run_id,
            drafts=bounded_drafts,
            provenance=provenance,
            policy=config.policy,
            active_dispositions=config.active_dispositions,
        )
        all_observations.extend(observations)

    subject_coverage: tuple[SubjectCoverageRecord, ...] = ()
    if config.subject_discovery is not None:
        try:
            discovery = config.subject_discovery(Path(config.target_path), config.target_key)
        except Exception as exc:
            recovery_items.append(
                RecoveryItem(
                    id=f"recovery-{config.run_id}-subject-discovery",
                    kind="discovery-failed",
                    description=f"Subject discovery failed: {exc}",
                    validator_id="subject-discovery",
                    context={"error": str(exc)},
                )
            )
        else:
            derivation = derive_subject_coverage(
                run_id=config.run_id,
                target_key=config.target_key,
                target_content_hash=config.target_content_hash,
                subjects=discovery.subjects,
                expected_subjects=discovery.expected_subjects,
                validator_statuses=tuple(validator_statuses),
                observations=tuple(all_observations),
                validator_subject_keys_checked={
                    result.validator_id: frozenset(result.subject_keys_checked)
                    for result in results
                    if result.subject_keys_checked
                },
                validator_subject_keys_skipped={
                    result.validator_id: frozenset(result.subject_keys_skipped)
                    for result in results
                    if result.subject_keys_skipped
                },
                validator_subject_selectors=discovery.validator_subject_selectors,
                model_health_by_subject_key=discovery.model_health_by_subject_key,
                not_applicable_subject_keys=config.not_applicable_subject_keys,
            )
            subject_coverage = derivation.records

    if diagnostic_log is not None:
        diagnostic_log.emit(
            DiagnosticEvent(
                timestamp=datetime.now(UTC).isoformat(),
                run_id=config.run_id,
                phase="scoring",
                validator_id=None,
                status="started",
                duration_ms=None,
                message="Computing assessment score",
                context={"observation_count": len(all_observations)},
            )
        )

    assessment = assemble_assessment(
        run_id=config.run_id,
        target_key=config.target_key,
        target_content_hash=config.target_content_hash,
        timestamp=config.timestamp,
        observations=all_observations,
        validator_statuses=validator_statuses,
        critical_validator_ids=config.critical_validator_ids,
        recovery_items=recovery_items,
        previous_fingerprints=config.previous_fingerprints,
        previous_score=config.previous_score,
        subject_coverage=subject_coverage,
        dimension_map=config.dimension_map,
        scoring_config=config.scoring_config,
    )

    assessment_json = render_json(assessment)
    report_md = render_markdown(assessment)

    run_duration_ms = int((time.monotonic() - run_start) * 1000)

    if diagnostic_log is not None:
        diagnostic_log.emit(
            DiagnosticEvent(
                timestamp=datetime.now(UTC).isoformat(),
                run_id=config.run_id,
                phase="run-complete",
                validator_id=None,
                status="completed",
                duration_ms=run_duration_ms,
                message="Validation run completed",
                context={
                    "score": assessment.score.overall,
                    "coverage_status": assessment.coverage_status.value,
                    "observation_count": len(all_observations),
                },
            )
        )

    if hook_registry is not None:
        from verdx.engine.runtime.hooks import dispatch_hook

        dispatch_hook(
            hook_registry,
            "run-complete",
            {
                "run_id": config.run_id,
                "target_key": config.target_key,
                "score": assessment.score.overall,
                "coverage_status": assessment.coverage_status.value,
                "observation_count": len(all_observations),
                "duration_ms": run_duration_ms,
            },
        )

    return RunOutcome(
        assessment=assessment,
        assessment_json=assessment_json,
        report_md=report_md,
        observations=all_observations,
        validator_statuses=validator_statuses,
        recovery_items=recovery_items,
    )


def assess_stage(
    stage_name: str,
    pipeline: PipelineConfig,
    config: RunConfig,
) -> StageResult:
    """Run validation for a specific pipeline stage and evaluate its gate.

    Filters the config's validators to only those listed in the stage,
    runs validation, evaluates the gate, and returns a StageResult that
    tells the caller what to do next.
    """
    stage = pipeline.get_stage(stage_name)

    filtered_validators = [
        (v, vc)
        for v, vc in config.validators
        if getattr(v, "validator_id", None) in stage.validator_ids
    ]

    stage_config = RunConfig(
        run_id=config.run_id,
        target_key=config.target_key,
        target_path=config.target_path,
        target_content_hash=config.target_content_hash,
        timestamp=config.timestamp,
        producer_role=config.producer_role,
        producer_id=config.producer_id,
        policy=config.policy,
        validators=filtered_validators,
        critical_validator_ids=config.critical_validator_ids,
        active_dispositions=config.active_dispositions,
        previous_fingerprints=config.previous_fingerprints,
        previous_score=config.previous_score,
        producer_actor=config.producer_actor,
        validator_actors=config.validator_actors,
        timeout_seconds=config.timeout_seconds,
        dimension_map=config.dimension_map,
        subject_discovery=config.subject_discovery,
        not_applicable_subject_keys=config.not_applicable_subject_keys,
        scoring_config=config.scoring_config,
    )

    outcome = run_validation(stage_config)
    gate_decision = evaluate_gate(stage.gate, outcome.assessment)

    if gate_decision.passed:
        actions = stage.on_pass
        next_stage = stage.on_pass.advance
    else:
        actions = stage.on_fail if stage.on_fail is not None else stage.on_pass
        next_stage = None

    return StageResult(
        stage=stage_name,
        gate_decision=gate_decision,
        assessment=outcome.assessment,
        next_stage=next_stage,
        actions=actions,
    )
