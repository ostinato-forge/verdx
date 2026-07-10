"""Assessment derivation — transforms ExecutionResult into RunAssessment.

Takes raw ``ValidatorResult``s from ``execute_plan()`` and drives them
through canonicalization, scoring, and coverage to produce a scored
``RunAssessment``.  This is Layer 2 of the two-layer design: always
runs fresh (never cached) so policy/disposition/scoring changes are
reflected immediately.

Coexists with the old ``engine.py`` orchestration until PR 5 wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from verdx.contracts.assessment import ValidatorStatus
from verdx.engine.runtime.bounding import bound_drafts
from verdx.engine.validation.canonicalize import Provenance, canonicalize
from verdx.engine.validation.independence import ActorFacts, evaluate_independence
from verdx.engine.validation.scoring import assemble_assessment

if TYPE_CHECKING:
    from verdx.contracts.assessment import RunAssessment
    from verdx.contracts.config import ScoringConfig
    from verdx.contracts.disposition import Disposition
    from verdx.contracts.observation import Observation
    from verdx.contracts.plan import ResolvedGatePlan
    from verdx.contracts.subjects import SubjectCoverageRecord
    from verdx.contracts.validator import ValidatorResult
    from verdx.engine.runtime.executor import ExecutionResult
    from verdx.engine.validation.escalation import EscalationPolicy


@dataclass(frozen=True, slots=True)
class AssessmentResult:
    """Output of derive_assessment().  Wraps the scored RunAssessment
    with the flat observation list for downstream use (projector, reports).
    """

    assessment: RunAssessment
    observations: tuple[Observation, ...]


def derive_assessment(
    execution_result: ExecutionResult,
    plan: ResolvedGatePlan,
    *,
    policy: EscalationPolicy,
    active_dispositions: dict[str, Disposition] | None = None,
    producer_role: str = "agent",
    producer_id: str | None = None,
    producer_actor: ActorFacts | None = None,
    validator_actors: dict[str, ActorFacts] | None = None,
    previous_fingerprints: set[str] | None = None,
    previous_score: int | None = None,
    subject_coverage: tuple[SubjectCoverageRecord, ...] = (),
    scoring_config: ScoringConfig | None = None,
    critical_validator_ids: frozenset[str] | None = None,
    dimension_map: dict[str, list[str]] | None = None,
) -> AssessmentResult:
    """Derive a scored RunAssessment from execution results.

    This is the pure Layer 2 orchestration: no I/O, no caching, no
    validator execution.  It transforms ``ExecutionResult`` through
    bounding → canonicalization → scoring.
    """
    dispositions = active_dispositions or {}

    policy_excluded_ids = {c.check_id for c in plan.checks if c.skip_code == "capability-denied"}
    scorable_results = tuple(
        r for r in execution_result.results if r.validator_id not in policy_excluded_ids
    )
    validator_statuses = _build_validator_statuses(scorable_results)

    all_observations: list[Observation] = []
    for result in execution_result.results:
        if result.status != "completed" or not result.drafts:
            continue

        bounded = bound_drafts(result.drafts)
        provenance = _build_provenance(
            result,
            producer_role=producer_role,
            producer_id=producer_id,
            producer_actor=producer_actor,
            validator_actors=validator_actors,
        )
        observations = canonicalize(
            run_id=plan.run_id,
            drafts=bounded,
            provenance=provenance,
            policy=policy,
            active_dispositions=dispositions,
        )
        all_observations.extend(observations)

    critical_ids = (
        critical_validator_ids if critical_validator_ids is not None else _derive_critical_ids(plan)
    )
    recovery_items = list(execution_result.recovery_items)

    assessment = assemble_assessment(
        run_id=plan.run_id,
        target_key=plan.target_key,
        target_content_hash=plan.target_content_hash,
        timestamp=plan.timestamp,
        observations=all_observations,
        validator_statuses=validator_statuses,
        critical_validator_ids=critical_ids,
        recovery_items=recovery_items,
        previous_fingerprints=previous_fingerprints,
        previous_score=previous_score,
        subject_coverage=subject_coverage,
        scoring_config=scoring_config,
        dimension_map=dimension_map,
    )

    return AssessmentResult(
        assessment=assessment,
        observations=tuple(all_observations),
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_validator_statuses(
    results: tuple[ValidatorResult, ...],
) -> list[ValidatorStatus]:
    return [
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


def _build_provenance(
    result: ValidatorResult,
    *,
    producer_role: str,
    producer_id: str | None,
    producer_actor: ActorFacts | None,
    validator_actors: dict[str, ActorFacts] | None,
) -> Provenance:
    validator_actor = (
        validator_actors.get(result.validator_id) if validator_actors is not None else None
    )
    independence = evaluate_independence(
        producer_role=producer_role,
        validator_role=result.validator_id,
        producer_actor=producer_actor,
        validator_actor=validator_actor,
    )
    return Provenance(
        producer_role=producer_role,
        producer_id=producer_id,
        validator_id=result.validator_id,
        validator_version=result.validator_version,
        independence=independence,
    )


def _derive_critical_ids(plan: ResolvedGatePlan) -> frozenset[str]:
    return frozenset(c.check_id for c in plan.checks if c.critical)
