"""Evidence projector — RunAssessment → GateEvidence.

Produces the compact, agent-facing ``GateEvidence`` projection from a
full ``RunAssessment``.  Groups observations into action-required (L3/L4)
and awareness (L0-L2) items, projects recovery items without secrets,
and enforces a token budget by progressively truncating lower-priority
groups.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING

from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.contracts.evidence import (
    ActionItem,
    AwarenessItem,
    EvidenceSummary,
    EvidenceTruncation,
    GateEvidence,
    RecoveryProjection,
)

if TYPE_CHECKING:
    from verdx.contracts.assessment import RecoveryItem, RunAssessment
    from verdx.contracts.observation import Observation

_ACTION_LEVELS = frozenset({EscalationLevel.L3, EscalationLevel.L4})


def project_evidence(
    assessment: RunAssessment,
    *,
    threshold: int = 80,
    domain: str | None = None,
    hard_max_tokens: int = 1000,
) -> GateEvidence:
    """Project a RunAssessment into compact GateEvidence for agent consumption."""
    actions, awareness = _group_observations(assessment.observations)
    recovery = _project_recovery(assessment.recovery_items)

    passed = assessment.score.overall >= threshold
    coverage_ok = assessment.coverage_status in (CoverageStatus.FULL, CoverageStatus.PARTIAL)
    confidence_ok = assessment.confidence != AssessmentConfidence.LOW

    summary = _build_summary(assessment, actions, awareness)

    actions, awareness, recovery, truncation = _truncate_to_budget(
        actions,
        awareness,
        recovery,
        hard_max_tokens,
        assessment=assessment,
        threshold=threshold,
        domain=domain,
        passed=passed,
        coverage_ok=coverage_ok,
        confidence_ok=confidence_ok,
        summary=summary,
    )

    return GateEvidence(
        run_id=assessment.run_id,
        target_key=assessment.target_key,
        domain=domain or assessment.domain or None,
        score=assessment.score.overall,
        threshold=threshold,
        passed=passed,
        coverage_ok=coverage_ok,
        confidence_ok=confidence_ok,
        coverage=assessment.coverage_status,
        confidence=assessment.confidence,
        summary=summary,
        action_required=tuple(actions),
        awareness=tuple(awareness),
        recovery=tuple(recovery),
        coverage_gaps=assessment.coverage_gaps,
        truncation=truncation,
    )


# ---------------------------------------------------------------------------
# Observation grouping
# ---------------------------------------------------------------------------


def _group_observations(
    observations: tuple[Observation, ...],
) -> tuple[list[ActionItem], list[AwarenessItem]]:
    action_groups: dict[str, list[Observation]] = defaultdict(list)
    awareness_groups: dict[str, list[Observation]] = defaultdict(list)

    for obs in observations:
        if obs.escalation in _ACTION_LEVELS:
            action_groups[obs.rule_id].append(obs)
        else:
            awareness_groups[obs.rule_id].append(obs)

    actions: list[ActionItem] = []
    for rule_id, group in sorted(action_groups.items()):
        first = group[0]
        actions.append(
            ActionItem(
                rule=rule_id,
                severity=first.severity.value,
                escalation=first.escalation.value,
                summary=first.summary,
                location=first.location,
                count=len(group),
            )
        )

    awareness_items: list[AwarenessItem] = []
    for rule_id, group in sorted(awareness_groups.items()):
        first = group[0]
        awareness_items.append(
            AwarenessItem(
                rule=rule_id,
                severity=first.severity.value,
                escalation=first.escalation.value,
                count=len(group),
                example=first.summary,
            )
        )

    return actions, awareness_items


# ---------------------------------------------------------------------------
# Recovery projection
# ---------------------------------------------------------------------------


def _project_recovery(
    items: tuple[RecoveryItem, ...],
) -> list[RecoveryProjection]:
    return [
        RecoveryProjection(
            id=item.id,
            kind=item.kind,
            severity=item.severity,
            description=item.description,
            validator_id=item.validator_id,
            code=item.code,
        )
        for item in items
    ]


# ---------------------------------------------------------------------------
# Summary building
# ---------------------------------------------------------------------------


def _build_summary(
    assessment: RunAssessment,
    actions: list[ActionItem],
    awareness: list[AwarenessItem],
) -> EvidenceSummary:
    blocking_count = sum(a.count for a in actions)
    awareness_count = sum(a.count for a in awareness)

    by_escalation: dict[str, int] = {}
    for level, count in assessment.escalation_summary.counts.items():
        by_escalation[level if isinstance(level, str) else level.value] = count

    domains = (assessment.domain,) if assessment.domain else ()

    return EvidenceSummary(
        checks_run=assessment.checks_run,
        checks_passed=assessment.checks_passed,
        validators_run=len(assessment.validator_statuses),
        validators_clean=assessment.validators_clean,
        by_escalation=by_escalation,
        blocking_findings=blocking_count,
        awareness_items=awareness_count,
        domains=domains,
    )


# ---------------------------------------------------------------------------
# Token budget truncation
# ---------------------------------------------------------------------------


def _estimate_tokens(evidence: GateEvidence) -> int:
    """Rough token estimate: JSON bytes / 4."""
    return len(json.dumps(evidence.to_dict(), default=str)) // 4


def _truncate_to_budget(
    actions: list[ActionItem],
    awareness: list[AwarenessItem],
    recovery: list[RecoveryProjection],
    hard_max: int,
    *,
    assessment: RunAssessment,
    threshold: int,
    domain: str | None,
    passed: bool,
    coverage_ok: bool,
    confidence_ok: bool,
    summary: EvidenceSummary,
) -> tuple[
    list[ActionItem],
    list[AwarenessItem],
    list[RecoveryProjection],
    EvidenceTruncation | None,
]:
    def _probe_tokens() -> int:
        probe = GateEvidence(
            run_id=assessment.run_id,
            target_key=assessment.target_key,
            domain=domain or assessment.domain or None,
            score=assessment.score.overall,
            threshold=threshold,
            passed=passed,
            coverage_ok=coverage_ok,
            confidence_ok=confidence_ok,
            coverage=assessment.coverage_status,
            confidence=assessment.confidence,
            summary=summary,
            action_required=tuple(actions),
            awareness=tuple(awareness),
            recovery=tuple(recovery),
            coverage_gaps=assessment.coverage_gaps,
        )
        return _estimate_tokens(probe)

    tokens = _probe_tokens()
    if tokens <= hard_max:
        return actions, awareness, recovery, None

    omitted_awareness = 0
    omitted_actions = 0
    total_omitted_obs = 0

    while tokens > hard_max and awareness:
        dropped_aw = awareness.pop()
        total_omitted_obs += dropped_aw.count
        omitted_awareness += 1
        tokens = _probe_tokens()

    while tokens > hard_max and actions:
        dropped_act = actions.pop()
        total_omitted_obs += dropped_act.count
        omitted_actions += 1
        tokens = _probe_tokens()

    truncation = EvidenceTruncation(
        truncated=True,
        estimated_tokens=tokens,
        hard_max_tokens=hard_max,
        omitted_action_groups=omitted_actions,
        omitted_awareness_groups=omitted_awareness,
        omitted_observations=total_omitted_obs,
    )
    return actions, awareness, recovery, truncation
