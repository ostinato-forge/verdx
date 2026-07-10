"""Canonicalization: turns validator drafts into final observations.

This is the heart of the engine. All inputs are explicit function
arguments -- no I/O, no current time, no randomness.

The boundary:
- Validators emit ObservationDraft (no identity, no escalation)
- Core produces Observation (identity, escalation, disposition applied)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from verdx.contracts.dimensions import Confidence, EscalationLevel
from verdx.contracts.observation import (
    OBSERVATION_SCHEMA_VERSION,
    AppliedDisposition,
    Observation,
    ObservationDraft,
)
from verdx.engine.validation.escalation import EscalationPolicy, evaluate
from verdx.engine.validation.fingerprint import (
    compute_evidence_hash,
    compute_issue_fingerprint,
    compute_observation_id,
    normalize_location,
)

if TYPE_CHECKING:
    from verdx.contracts.disposition import Disposition
    from verdx.contracts.independence import IndependenceRecord


@dataclass(frozen=True, slots=True)
class Provenance:
    """Provenance context attached to each draft during canonicalization."""

    producer_role: str
    producer_id: str | None
    validator_id: str
    validator_version: str
    independence: IndependenceRecord


def _apply_disposition(
    fingerprint: str,
    base_escalation: EscalationLevel,
    floor_escalation: EscalationLevel,
    active_dispositions: dict[str, Disposition],
) -> tuple[EscalationLevel, AppliedDisposition | None]:
    """Apply active disposition to computed escalation.

    Returns (final_escalation, applied_disposition_or_none).

    Rules:
    - accepted-risk / false-positive: cap at L1 but not below floor
    - deferred / context-dependent: retain computed escalation, attach info
    - Expired dispositions should be filtered before reaching core
    """
    disposition = active_dispositions.get(fingerprint)
    if disposition is None:
        return base_escalation, None

    original = base_escalation

    if disposition.decision in ("accepted-risk", "false-positive"):
        capped = EscalationLevel.L1
        if _escalation_rank(floor_escalation) > _escalation_rank(capped):
            final = floor_escalation
        else:
            final = capped
        effect = "capped"
    elif disposition.decision == "deferred":
        final = base_escalation
        effect = "deferred"
    else:
        final = base_escalation
        effect = "reevaluate"

    applied = AppliedDisposition(
        decision=disposition.decision,
        reason=disposition.reason,
        team=disposition.team,
        timestamp=disposition.timestamp,
        expires=disposition.expires,
        effect=effect,
        original_escalation=original,
        final_escalation=final,
    )
    return final, applied


def _escalation_rank(level: EscalationLevel) -> int:
    order = list(EscalationLevel)
    return order.index(level)


def _apply_independence(
    confidence: Confidence,
    independence: IndependenceRecord,
) -> Confidence:
    """Cap confidence if independence is unmet."""
    if independence.result == "unmet":
        return Confidence.POSSIBLE
    return confidence


def _sort_key(obs: Observation) -> tuple[str, str, str, str, str]:
    return (
        obs.target_key,
        obs.location or "",
        obs.rule_id,
        obs.category,
        obs.issue_fingerprint,
    )


def canonicalize(
    run_id: str,
    drafts: list[ObservationDraft],
    provenance: Provenance,
    policy: EscalationPolicy,
    active_dispositions: dict[str, Disposition],
) -> list[Observation]:
    """Turn validator drafts into final observations.

    All inputs explicit. No I/O, no randomness, no time reads.

    Steps:
    1. For each draft, compute issue_fingerprint
    2. Compute base escalation from dimensions + policy
    3. Apply category floor (already handled by policy.evaluate)
    4. Apply active disposition (can only reduce if above floor)
    5. Apply independence constraints on confidence
    6. Compute observation_id
    7. Produce final Observation
    8. Sort output for deterministic ordering
    """
    observations: list[Observation] = []

    for draft in drafts:
        normalized_loc = normalize_location(draft.location)

        fingerprint = compute_issue_fingerprint(
            target_key=draft.target_key,
            rule_id=draft.rule_id,
            location=draft.location,
            category=draft.category,
        )

        base_escalation = evaluate(policy, draft.category, draft.dimensions)

        floor_escalation = _compute_floor(policy, draft.category)

        final_escalation, applied_disp = _apply_disposition(
            fingerprint=fingerprint,
            base_escalation=base_escalation,
            floor_escalation=floor_escalation,
            active_dispositions=active_dispositions,
        )

        final_confidence = _apply_independence(
            draft.confidence,
            provenance.independence,
        )

        evidence_dicts = [e.to_dict() for e in draft.evidence]
        evidence_hash = compute_evidence_hash(evidence_dicts)

        obs_id = compute_observation_id(
            run_id=run_id,
            issue_fingerprint=fingerprint,
            evidence_hash=evidence_hash,
            validator_id=provenance.validator_id,
            validator_version=provenance.validator_version,
        )

        observation = Observation(
            schema_version=OBSERVATION_SCHEMA_VERSION,
            observation_id=obs_id,
            issue_fingerprint=fingerprint,
            target_key=draft.target_key,
            location=normalized_loc,
            rule_id=draft.rule_id,
            producer_role=provenance.producer_role,
            producer_id=provenance.producer_id,
            validator_id=provenance.validator_id,
            validator_version=provenance.validator_version,
            independence=provenance.independence,
            category=draft.category,
            severity=draft.severity,
            confidence=final_confidence,
            dimensions=draft.dimensions,
            escalation=final_escalation,
            summary=draft.summary,
            evidence=draft.evidence,
            reproduction=draft.reproduction,
            invalidating_conditions=draft.invalidating_conditions,
            disposition=applied_disp,
        )
        observations.append(observation)

    observations.sort(key=_sort_key)
    return observations


def _compute_floor(
    policy: EscalationPolicy,
    category: str,
) -> EscalationLevel:
    """Find the applicable floor for a category, defaulting to L0."""
    for floor in policy.floors:
        if floor.category == category:
            return floor.minimum
    return EscalationLevel.L0
