"""Scoring, coverage, confidence, improvement, and assessment assembly.

Pure functions. All inputs explicit. No I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.assessment import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentConfidence,
    CoverageGap,
    DimensionScore,
    EscalationSummary,
    ImprovementSummary,
    RecoveryItem,
    RunAssessment,
    Score,
    ScoreComparison,
    Trend,
    ValidatorStatus,
)
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    EscalationLevel,
    Severity,
)
from verdx.engine.validation.coverage import (
    rollup_coverage_status,
    subject_coverage_penalty,
)

if TYPE_CHECKING:
    from verdx.contracts.config import ScoringConfig
    from verdx.contracts.observation import Observation
    from verdx.contracts.subjects import SubjectCoverageRecord

# ---------------------------------------------------------------------------
# Default penalty tables
# ---------------------------------------------------------------------------

_DISPOSITION_PENALTY_MULTIPLIERS: dict[str, float] = {
    "false-positive": 0.0,
    "accepted-risk": 0.1,
    "deferred": 1.0,
    "context-dependent": 1.0,
}

SEVERITY_CONFIDENCE_PENALTY: dict[Severity, dict[Confidence, int]] = {
    Severity.CRITICAL: {
        Confidence.CONFIRMED: 40,
        Confidence.PROBABLE: 30,
        Confidence.POSSIBLE: 20,
    },
    Severity.HIGH: {
        Confidence.CONFIRMED: 30,
        Confidence.PROBABLE: 22,
        Confidence.POSSIBLE: 15,
    },
    Severity.MEDIUM: {
        Confidence.CONFIRMED: 20,
        Confidence.PROBABLE: 15,
        Confidence.POSSIBLE: 10,
    },
    Severity.LOW: {
        Confidence.CONFIRMED: 10,
        Confidence.PROBABLE: 8,
        Confidence.POSSIBLE: 5,
    },
    Severity.INFO: {
        Confidence.CONFIRMED: 3,
        Confidence.PROBABLE: 2,
        Confidence.POSSIBLE: 1,
    },
}

_PER_RULE_DECAY = 0.5
_PER_RULE_MIN_FRACTION = 0.1
_PER_RULE_CAP_MULTIPLIER = 2.5

COVERAGE_PENALTIES = {
    "failed_critical": 30,
    "skipped_critical": 25,
    "failed_non_critical": 10,
    "skipped_non_critical": 5,
    "truncated_high_critical": 3,
}

DEFAULT_INDEPENDENCE_PENALTY = 15


def _merge_severity_penalties(
    overrides: dict[Severity, dict[Confidence, int]] | None,
) -> dict[Severity, dict[Confidence, int]]:
    """Merge partial severity penalty overrides with defaults."""
    if not overrides:
        return SEVERITY_CONFIDENCE_PENALTY
    merged: dict[Severity, dict[Confidence, int]] = {}
    for severity in Severity:
        default_conf = SEVERITY_CONFIDENCE_PENALTY.get(severity, {})
        override_conf = overrides.get(severity, {})
        merged[severity] = {**default_conf, **override_conf}
    return merged


# ---------------------------------------------------------------------------
# Coverage status
# ---------------------------------------------------------------------------


def compute_coverage_status(
    validator_statuses: list[ValidatorStatus],
    critical_validator_ids: frozenset[str],
    subject_coverage: tuple[SubjectCoverageRecord, ...] = (),
    recovery_items: list[RecoveryItem] | tuple[RecoveryItem, ...] = (),
) -> CoverageStatus:
    """Determine coverage status from validator outcomes and criticality."""
    if any(item.kind == "discovery-failed" for item in recovery_items):
        return CoverageStatus.DEGRADED

    if subject_coverage:
        return rollup_coverage_status(
            subject_coverage,
            tuple(validator_statuses),
            critical_validator_ids,
        )

    has_critical_failure = False
    has_non_critical_failure = False

    for vs in validator_statuses:
        if vs.status in ("failed", "skipped"):
            if vs.validator_id in critical_validator_ids:
                has_critical_failure = True
            else:
                has_non_critical_failure = True

    if has_critical_failure:
        return CoverageStatus.DEGRADED
    if has_non_critical_failure:
        return CoverageStatus.PARTIAL
    return CoverageStatus.FULL


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def compute_confidence(
    coverage_status: CoverageStatus,
    observations: list[Observation],
    coverage_gaps: list[CoverageGap],
    recovery_items: list[RecoveryItem],
) -> AssessmentConfidence:
    """Determine assessment confidence.

    - STRONG: full coverage, no recovery blockers, no coverage gaps
    - GUARDED: observations exist or non-critical gaps exist
    - LOW: degraded coverage, blocking recovery item, or critical gaps
    """
    if coverage_status == CoverageStatus.DEGRADED:
        return AssessmentConfidence.LOW

    has_blockers = any(ri.kind == "blocked" for ri in recovery_items)
    if has_blockers:
        return AssessmentConfidence.LOW

    if coverage_status == CoverageStatus.PARTIAL:
        return AssessmentConfidence.LOW

    if observations or coverage_gaps:
        return AssessmentConfidence.GUARDED

    return AssessmentConfidence.STRONG


# ---------------------------------------------------------------------------
# Positive coverage
# ---------------------------------------------------------------------------


def compute_positive_coverage(
    validator_statuses: list[ValidatorStatus],
) -> tuple[int, int, int]:
    """Compute (checks_run, checks_passed, validators_clean).

    Positive signal is computed from validator-reported counts,
    not inferred from absence of observations.
    """
    checks_run = sum(vs.checks_run for vs in validator_statuses)
    checks_passed = sum(vs.checks_passed for vs in validator_statuses)
    validators_clean = sum(
        1 for vs in validator_statuses if vs.status == "completed" and vs.observation_count == 0
    )
    return checks_run, checks_passed, validators_clean


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _effective_penalties(
    observations: list[Observation],
    scoring_config: ScoringConfig | None = None,
) -> list[int]:
    """Compute per-observation penalties with diminishing returns per rule.

    Within each rule_id group, the first observation pays full penalty.
    Subsequent observations decay geometrically (x0.5 each), with a
    floor of 10% of the base penalty (minimum 1 point).
    Total deduction per rule group is capped at 2.5x the highest base
    penalty in the group, so noisy rules cannot dominate the score.
    """
    severity_penalties = _merge_severity_penalties(
        scoring_config.severity_penalties if scoring_config else None
    )
    decay = (
        scoring_config.decay_factor
        if scoring_config and scoring_config.decay_factor is not None
        else _PER_RULE_DECAY
    )
    min_frac = (
        scoring_config.min_fraction
        if scoring_config and scoring_config.min_fraction is not None
        else _PER_RULE_MIN_FRACTION
    )
    cap_mult = (
        scoring_config.cap_multiplier
        if scoring_config and scoring_config.cap_multiplier is not None
        else _PER_RULE_CAP_MULTIPLIER
    )

    groups: dict[str, list[int]] = {}
    for i, obs in enumerate(observations):
        groups.setdefault(obs.rule_id, []).append(i)

    penalties = [0] * len(observations)
    for indices in groups.values():
        bases = [
            severity_penalties[observations[i].severity][observations[i].confidence]
            for i in indices
        ]
        ranked = sorted(zip(indices, bases, strict=True), key=lambda t: -t[1])

        cap = int(ranked[0][1] * cap_mult)
        running_total = 0

        for rank, (idx, base) in enumerate(ranked):
            disp = observations[idx].disposition
            if disp is not None:
                mult = _DISPOSITION_PENALTY_MULTIPLIERS.get(disp.decision, 1.0)
                base = int(base * mult)
            floor = max(1, int(base * min_frac)) if base > 0 else 0
            raw = max(floor, int(base * decay**rank)) if base > 0 else 0
            allowed = min(raw, cap - running_total)
            penalties[idx] = max(0, allowed)
            running_total += penalties[idx]
    return penalties


def compute_score(
    observations: list[Observation],
    validator_statuses: list[ValidatorStatus],
    critical_validator_ids: frozenset[str],
    previous_score: int | None = None,
    subject_coverage: tuple[SubjectCoverageRecord, ...] = (),
    scoring_config: ScoringConfig | None = None,
) -> Score:
    """Compute quality score with observation and coverage penalties.

    1. Start at 100
    2. Deduct observation penalties by severity and confidence
       (with diminishing returns for repeated instances of the same rule)
    3. Deduct coverage penalty for gaps and failed/skipped validators
    4. Clamp to 0..100
    5. Attach escalation distribution
    6. Attach dimension scores
    """
    coverage_penalties = {**COVERAGE_PENALTIES}
    if scoring_config and scoring_config.coverage_penalties:
        coverage_penalties.update(scoring_config.coverage_penalties)

    penalties = _effective_penalties(observations, scoring_config)
    deductions = sum(penalties)

    for obs in observations:
        if obs.severity in (Severity.HIGH, Severity.CRITICAL):
            for ev in obs.evidence:
                if ev.truncated:
                    deductions += coverage_penalties["truncated_high_critical"]

    coverage_penalty = 0
    for vs in validator_statuses:
        is_critical = vs.validator_id in critical_validator_ids
        if vs.status == "failed":
            key = "failed_critical" if is_critical else "failed_non_critical"
            coverage_penalty += coverage_penalties[key]
        elif vs.status == "skipped":
            key = "skipped_critical" if is_critical else "skipped_non_critical"
            coverage_penalty += coverage_penalties[key]

    coverage_penalty += subject_coverage_penalty(subject_coverage)

    per_validator = (
        scoring_config.independence_penalty
        if scoring_config and scoring_config.independence_penalty is not None
        else DEFAULT_INDEPENDENCE_PENALTY
    )
    unmet_validators = {
        obs.independence.validator_role
        for obs in observations
        if obs.independence is not None and obs.independence.result == "unmet"
    }
    indep_penalty = len(unmet_validators) * per_validator

    deductions += coverage_penalty + indep_penalty
    overall = max(0, min(100, 100 - deductions))

    escalation_dist = _compute_escalation_distribution(observations)
    dimension_scores = _compute_dimension_scores(observations, penalties)

    comparison = None
    if previous_score is not None:
        comparison = ScoreComparison(
            previous_score=previous_score,
            delta=overall - previous_score,
        )

    return Score(
        overall=overall,
        dimension_scores=dimension_scores,
        observation_count=len(observations),
        escalation_distribution=escalation_dist,
        coverage_penalty=coverage_penalty,
        independence_penalty=indep_penalty,
        comparison=comparison,
    )


def _compute_escalation_distribution(
    observations: list[Observation],
) -> dict[EscalationLevel, int]:
    dist: dict[EscalationLevel, int] = {}
    for obs in observations:
        dist[obs.escalation] = dist.get(obs.escalation, 0) + 1
    return dist


def _compute_dimension_scores(
    observations: list[Observation],
    penalties: list[int],
) -> dict[str, DimensionScore]:
    dim_obs_counts: dict[str, int] = {}
    dim_penalties: dict[str, int] = {}

    for i, obs in enumerate(observations):
        penalty = penalties[i]
        for dim_name in obs.dimensions:
            dim_obs_counts[dim_name] = dim_obs_counts.get(dim_name, 0) + 1
            dim_penalties[dim_name] = dim_penalties.get(dim_name, 0) + penalty

    result: dict[str, DimensionScore] = {}
    for dim_name in sorted(dim_obs_counts):
        raw = max(0, min(100, 100 - dim_penalties[dim_name]))
        result[dim_name] = DimensionScore(
            dimension=dim_name,
            score=raw,
            observation_count=dim_obs_counts[dim_name],
        )
    return result


# ---------------------------------------------------------------------------
# Escalation summary
# ---------------------------------------------------------------------------


def compute_escalation_summary(
    observations: list[Observation],
) -> EscalationSummary:
    """Build escalation summary from observations."""
    dist = _compute_escalation_distribution(observations)
    if dist:
        highest = max(dist, key=lambda lvl: list(EscalationLevel).index(lvl))
    else:
        highest = EscalationLevel.L0
    return EscalationSummary(counts=dist, highest=highest)


# ---------------------------------------------------------------------------
# Improvement summary
# ---------------------------------------------------------------------------


def compute_improvement(
    current_fingerprints: set[str],
    previous_fingerprints: set[str] | None,
) -> ImprovementSummary | None:
    """Compute improvement summary vs. previous run.

    Returns None if there's no previous run to compare against.
    """
    if previous_fingerprints is None:
        return None

    resolved = previous_fingerprints - current_fingerprints
    new = current_fingerprints - previous_fingerprints
    persistent = current_fingerprints & previous_fingerprints

    resolved_count = len(resolved)
    new_count = len(new)

    if resolved_count > new_count:
        trend = Trend.IMPROVING
    elif new_count > resolved_count:
        trend = Trend.REGRESSING
    else:
        trend = Trend.STABLE

    return ImprovementSummary(
        resolved_count=resolved_count,
        new_count=new_count,
        persistent_count=len(persistent),
        resolved_fingerprints=tuple(sorted(resolved)),
        new_fingerprints=tuple(sorted(new)),
        persistent_fingerprints=tuple(sorted(persistent)),
        trend=trend,
    )


# ---------------------------------------------------------------------------
# Coverage gaps from failed validators
# ---------------------------------------------------------------------------


def coverage_gaps_from_statuses(
    validator_statuses: list[ValidatorStatus],
    dimension_map: dict[str, list[str]] | None = None,
) -> list[CoverageGap]:
    """Generate CoverageGap entries for failed/skipped validators.

    dimension_map: optional mapping of validator_id -> affected dimensions.
    If not provided, gaps report empty dimensions_affected.
    """
    gaps: list[CoverageGap] = []
    for vs in validator_statuses:
        if vs.status == "failed":
            dims: tuple[str, ...] = ()
            if dimension_map and vs.validator_id in dimension_map:
                dims = tuple(dimension_map[vs.validator_id])
            gaps.append(
                CoverageGap(
                    validator_id=vs.validator_id,
                    reason=vs.error or "validator failed",
                    impact=f"{vs.validator_id} checks not performed",
                    dimensions_affected=dims,
                )
            )
        elif vs.status == "skipped":
            skip_dims: tuple[str, ...] = ()
            if dimension_map and vs.validator_id in dimension_map:
                skip_dims = tuple(dimension_map[vs.validator_id])
            gaps.append(
                CoverageGap(
                    validator_id=vs.validator_id,
                    reason=vs.skip_reason or "validator skipped",
                    impact=f"{vs.validator_id} checks not performed",
                    dimensions_affected=skip_dims,
                )
            )
    return gaps


# ---------------------------------------------------------------------------
# Assessment assembly
# ---------------------------------------------------------------------------


def assemble_assessment(
    run_id: str,
    target_key: str,
    target_content_hash: str,
    timestamp: str,
    observations: list[Observation],
    validator_statuses: list[ValidatorStatus],
    critical_validator_ids: frozenset[str],
    recovery_items: list[RecoveryItem],
    previous_fingerprints: set[str] | None = None,
    previous_score: int | None = None,
    coverage_gaps: list[CoverageGap] | None = None,
    subject_coverage: tuple[SubjectCoverageRecord, ...] = (),
    dimension_map: dict[str, list[str]] | None = None,
    scoring_config: ScoringConfig | None = None,
) -> RunAssessment:
    """Assemble a complete RunAssessment from components.

    Pure function. All inputs explicit.
    """
    auto_gaps = coverage_gaps_from_statuses(
        validator_statuses,
        dimension_map,
    )
    all_gaps = list(coverage_gaps or []) + auto_gaps

    coverage_status = compute_coverage_status(
        validator_statuses,
        critical_validator_ids,
        subject_coverage,
        recovery_items,
    )

    checks_run, checks_passed, validators_clean = compute_positive_coverage(
        validator_statuses,
    )

    confidence = compute_confidence(
        coverage_status,
        observations,
        all_gaps,
        recovery_items,
    )

    current_fps = {obs.issue_fingerprint for obs in observations}
    improvement = compute_improvement(current_fps, previous_fingerprints)

    score = compute_score(
        observations,
        validator_statuses,
        critical_validator_ids,
        previous_score,
        subject_coverage,
        scoring_config,
    )

    escalation_summary = compute_escalation_summary(observations)

    return RunAssessment(
        schema_version=ASSESSMENT_SCHEMA_VERSION,
        run_id=run_id,
        target_key=target_key,
        target_content_hash=target_content_hash,
        timestamp=timestamp,
        coverage_status=coverage_status,
        validator_statuses=tuple(validator_statuses),
        coverage_gaps=tuple(all_gaps),
        checks_run=checks_run,
        checks_passed=checks_passed,
        validators_clean=validators_clean,
        observations=tuple(observations),
        recovery_items=tuple(recovery_items),
        confidence=confidence,
        score=score,
        escalation_summary=escalation_summary,
        improvement=improvement,
        subject_coverage=subject_coverage,
    )


def merge_assessments(
    assessments: list[RunAssessment],
    *,
    critical_validator_ids: frozenset[str],
    dimension_map: dict[str, list[str]] | None = None,
) -> RunAssessment:
    """Compose several per-domain assessments into one.

    Re-assembles over the deduplicated union of every assessment's observations
    (by fingerprint) and validator statuses (by validator id) with the combined
    critical-validator set, so a target matching more than one domain keeps all
    of their findings, counted once, instead of dropping every domain but the
    last.

    Limitations on the rarely-hit multi-domain path: re-uses the default scoring
    config, and takes the critical-validator set from the caller (derived from
    default domain config), so a domain-specific scoring config or a
    profile-modified critical set is not carried into the merge.
    """
    if not assessments:
        raise ValueError("merge_assessments requires at least one assessment")
    first = assessments[0]

    obs_by_fingerprint: dict[str, Observation] = {}
    for a in assessments:
        for obs in a.observations:
            obs_by_fingerprint.setdefault(obs.issue_fingerprint, obs)
    observations = list(obs_by_fingerprint.values())

    status_by_id: dict[str, ValidatorStatus] = {}
    for a in assessments:
        for vs in a.validator_statuses:
            status_by_id.setdefault(vs.validator_id, vs)
    validator_statuses = list(status_by_id.values())

    recovery_items = [ri for a in assessments for ri in a.recovery_items]
    subject_coverage = tuple(sc for a in assessments for sc in a.subject_coverage)

    # assemble_assessment regenerates gaps for failed/skipped validators from the
    # union statuses; carry only the gaps it will not regenerate, to avoid both
    # losing them and duplicating the auto-generated ones.
    auto_ids = {vs.validator_id for vs in validator_statuses if vs.status in ("failed", "skipped")}
    coverage_gaps = [
        g for a in assessments for g in a.coverage_gaps if g.validator_id not in auto_ids
    ]

    return assemble_assessment(
        run_id=first.run_id,
        target_key=first.target_key,
        target_content_hash=first.target_content_hash,
        timestamp=first.timestamp,
        observations=observations,
        validator_statuses=validator_statuses,
        critical_validator_ids=critical_validator_ids,
        recovery_items=recovery_items,
        coverage_gaps=coverage_gaps,
        subject_coverage=subject_coverage,
        dimension_map=dimension_map,
    )
