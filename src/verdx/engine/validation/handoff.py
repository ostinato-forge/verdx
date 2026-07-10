"""Handoff briefs for human gate reviewers.

Pure functions. Takes a RunAssessment, returns a structured brief
with the information a human reviewer needs to make a gate decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdx.contracts.assessment import RunAssessment


@dataclass(frozen=True, slots=True)
class ObservationSummary:
    rule_id: str
    severity: str
    escalation: str
    summary: str
    location: str
    independence_result: str


@dataclass(frozen=True, slots=True)
class HandoffBrief:
    run_id: str
    target_key: str
    timestamp: str
    score: int
    coverage_status: str
    confidence: str
    observation_count: int
    top_observations: tuple[ObservationSummary, ...]
    coverage_gaps: tuple[str, ...]
    recovery_summaries: tuple[str, ...]
    trend_description: str | None
    independence_summary: str


_MAX_TOP_OBSERVATIONS = 10


def compose_handoff(assessment: RunAssessment) -> HandoffBrief:
    """Build a handoff brief from a completed assessment."""
    sorted_obs = sorted(
        assessment.observations,
        key=lambda o: o.escalation.value,
        reverse=True,
    )

    top = tuple(
        ObservationSummary(
            rule_id=o.rule_id,
            severity=o.severity.value,
            escalation=o.escalation.value,
            summary=o.summary,
            location=o.location or "n/a",
            independence_result=(o.independence.result if o.independence is not None else "n/a"),
        )
        for o in sorted_obs[:_MAX_TOP_OBSERVATIONS]
    )

    coverage_gaps = tuple(g.validator_id for g in assessment.coverage_gaps)

    recovery_summaries = tuple(ri.description for ri in assessment.recovery_items)

    trend = None
    if assessment.improvement is not None:
        imp = assessment.improvement
        trend = (
            f"{imp.resolved_count} resolved, {imp.new_count} new, {imp.persistent_count} persistent"
        )

    unmet_roles = sorted(
        {
            o.independence.validator_role
            for o in assessment.observations
            if o.independence is not None and o.independence.result == "unmet"
        }
    )
    if unmet_roles:
        independence_summary = f"unmet: {', '.join(unmet_roles)}"
    elif assessment.observations:
        independence_summary = "satisfied"
    else:
        independence_summary = "n/a"

    return HandoffBrief(
        run_id=assessment.run_id,
        target_key=assessment.target_key,
        timestamp=assessment.timestamp,
        score=assessment.score.overall,
        coverage_status=assessment.coverage_status.value,
        confidence=assessment.confidence.value,
        observation_count=len(assessment.observations),
        top_observations=top,
        coverage_gaps=coverage_gaps,
        recovery_summaries=recovery_summaries,
        trend_description=trend,
        independence_summary=independence_summary,
    )


def render_handoff_markdown(brief: HandoffBrief) -> str:
    """Render a handoff brief as markdown for human reviewers."""
    lines = [
        f"# Handoff Brief: {brief.target_key}",
        "",
        f"**Run:** {brief.run_id}  ",
        f"**Timestamp:** {brief.timestamp}  ",
        f"**Score:** {brief.score}/100  ",
        f"**Coverage:** {brief.coverage_status}  ",
        f"**Confidence:** {brief.confidence}  ",
        f"**Independence:** {brief.independence_summary}  ",
    ]

    if brief.trend_description:
        lines.append(f"**Trend:** {brief.trend_description}  ")

    if brief.top_observations:
        lines.append("")
        lines.append(f"## Top Observations ({brief.observation_count} total)")
        lines.append("")
        for obs in brief.top_observations:
            lines.append(
                f"- **[{obs.escalation}] {obs.rule_id}** ({obs.severity}) — {obs.summary}  "
            )
            lines.append(f"  Location: {obs.location}")

    if brief.coverage_gaps:
        lines.append("")
        lines.append("## Coverage Gaps")
        lines.append("")
        for gap in brief.coverage_gaps:
            lines.append(f"- {gap}")

    if brief.recovery_summaries:
        lines.append("")
        lines.append("## Recovery Items")
        lines.append("")
        for summary in brief.recovery_summaries:
            lines.append(f"- {summary}")

    lines.append("")
    return "\n".join(lines)
