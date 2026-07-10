"""Public API for verdx — stable surface for skills and downstream tools.

Skills import from here. Everything else is internal.
See platform-contract.md for the stability commitment.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from verdx.contracts.assessment import RunAssessment, Score, ValidatorStatus
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.observation import Evidence, Observation, ObservationDraft
from verdx.engine.runtime.domains import detect_domains, get_domain, list_domains
from verdx.engine.runtime.workflow import run_gate_workflow

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from verdx.contracts.disposition import Disposition
    from verdx.contracts.escalation import EscalationPolicy
    from verdx.engine.runtime.engine import RunOutcome


def validate(
    target: Path,
    *,
    domains: list[str] | None = None,
    profile: str | None = None,
    static_only: bool = False,
    threshold: int = 80,
    changed_files: frozenset[str] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    active_dispositions: dict[str, Disposition] | None = None,
) -> RunAssessment:
    """Run verdx validation on a target and return the assessment.

    Simplified wrapper around run_gate_workflow() for the common case.
    Auto-detects domains if none specified. Uses a default passthrough
    escalation policy and a temporary state directory.
    """
    if domains is None:
        detected = detect_domains(target)
        if not detected:
            msg = f"no domains detected for target: {target}"
            raise ValueError(msg)
        domain_names = list(detected)
    else:
        domain_names = domains

    def _fallback_policy() -> EscalationPolicy:
        from verdx.contracts.dimensions import DimLevel, EscalationLevel
        from verdx.contracts.escalation import (
            EscalationPolicy,
            EscalationRule,
            RuleMatch,
        )

        return EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="blocking",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.BLOCKING),
                    level=EscalationLevel.L4,
                ),
                EscalationRule(
                    name="high",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
                    level=EscalationLevel.L3,
                ),
                EscalationRule(
                    name="medium",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.MEDIUM),
                    level=EscalationLevel.L2,
                ),
                EscalationRule(
                    name="low-fallback",
                    match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
            ),
        )

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    timestamp = datetime.now(tz=UTC).isoformat()
    target_key = target.name

    assessments: list[RunAssessment] = []
    all_critical: set[str] = set()
    merged_dimension_map: dict[str, list[str]] = {}

    for domain_name in domain_names:
        domain_spec = get_domain(domain_name)
        try:
            domain_cfg = domain_spec.load_default_config()
            policy = domain_cfg.policy
            critical = set(domain_cfg.critical_validators)
            dim_map = dict(domain_cfg.dimension_map or {})
            scoring_config = getattr(domain_cfg, "scoring_config", None)
        except Exception:
            policy = _fallback_policy()
            critical = set()
            dim_map = {}
            scoring_config = None
        with tempfile.TemporaryDirectory(prefix="verdx-") as tmp:
            outcome: RunOutcome = run_gate_workflow(
                domain=domain_spec,
                target=target,
                target_key=target_key,
                state_dir=Path(tmp),
                policy=policy,
                threshold=threshold,
                static_only=static_only,
                active_dispositions=active_dispositions,
                scoring_config=scoring_config,
                dimension_map=dim_map,
                critical_validator_ids=frozenset(critical),
                profiles=(profile,) if profile else (),
                run_id=run_id,
                timestamp=timestamp,
                changed_files=changed_files,
                cli_overrides=cli_overrides,
            )
            assessments.append(outcome.assessment)
            all_critical |= critical
            for category, dims in dim_map.items():
                existing = merged_dimension_map.setdefault(category, [])
                existing.extend(d for d in dims if d not in existing)

    if not assessments:
        msg = "no assessments produced"
        raise RuntimeError(msg)

    # One detected domain returns its own assessment unchanged. Multiple detected
    # domains are merged so no domain's findings are dropped (R2-5).
    if len(assessments) == 1:
        return assessments[0]

    from verdx.engine.validation.scoring import merge_assessments

    return merge_assessments(
        assessments,
        critical_validator_ids=frozenset(all_critical),
        dimension_map=merged_dimension_map or None,
    )


def quick_check(
    target: Path,
    baseline: RunAssessment,
    *,
    domains: list[str] | None = None,
    static_only: bool = False,
    threshold: int = 80,
    changed_files: frozenset[str] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> RunAssessment:
    """Fast checkpoint — re-validate and return only newly introduced findings.

    Runs a full validation, then compares observation fingerprints against
    the baseline. The returned RunAssessment contains only new findings and
    an ImprovementSummary showing resolved/new/persistent counts.
    """
    from verdx.contracts.assessment import ImprovementSummary, Trend

    current = validate(
        target,
        domains=domains,
        static_only=static_only,
        threshold=threshold,
        changed_files=changed_files,
        cli_overrides=cli_overrides,
    )

    baseline_fps = {obs.issue_fingerprint for obs in baseline.observations}
    current_fps = {obs.issue_fingerprint for obs in current.observations}

    new_fps = current_fps - baseline_fps
    resolved_fps = baseline_fps - current_fps
    persistent_fps = baseline_fps & current_fps

    new_observations = tuple(
        obs for obs in current.observations if obs.issue_fingerprint in new_fps
    )

    if resolved_fps and not new_fps:
        trend = Trend.IMPROVING
    elif new_fps and not resolved_fps:
        trend = Trend.REGRESSING
    elif not new_fps and not resolved_fps:
        trend = Trend.STABLE
    elif len(new_fps) > len(resolved_fps):
        trend = Trend.REGRESSING
    elif len(resolved_fps) > len(new_fps):
        trend = Trend.IMPROVING
    else:
        trend = Trend.STABLE

    improvement = ImprovementSummary(
        resolved_count=len(resolved_fps),
        new_count=len(new_fps),
        persistent_count=len(persistent_fps),
        resolved_fingerprints=tuple(sorted(resolved_fps)),
        new_fingerprints=tuple(sorted(new_fps)),
        persistent_fingerprints=tuple(sorted(persistent_fps)),
        trend=trend,
    )

    return RunAssessment(
        schema_version=current.schema_version,
        run_id=current.run_id,
        target_key=current.target_key,
        target_content_hash=current.target_content_hash,
        timestamp=current.timestamp,
        coverage_status=current.coverage_status,
        validator_statuses=current.validator_statuses,
        coverage_gaps=current.coverage_gaps,
        checks_run=current.checks_run,
        checks_passed=current.checks_passed,
        validators_clean=current.validators_clean,
        observations=new_observations,
        recovery_items=current.recovery_items,
        confidence=current.confidence,
        score=current.score,
        escalation_summary=current.escalation_summary,
        improvement=improvement,
        subject_coverage=current.subject_coverage,
        domain=current.domain,
    )


__all__ = [
    "Confidence",
    "CoverageStatus",
    "DimLevel",
    "EscalationLevel",
    "Evidence",
    "Observation",
    "ObservationDraft",
    "RunAssessment",
    "Score",
    "Severity",
    "ValidatorStatus",
    "detect_domains",
    "get_domain",
    "list_domains",
    "quick_check",
    "run_gate_workflow",
    "validate",
]
