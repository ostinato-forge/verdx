"""Pure audit query functions over the event log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.audit import (
    ApprovalRecord,
    ArtifactRef,
    ComplianceSummary,
    TimelineEntry,
)
from verdx.contracts.events import EventType, StateEvent
from verdx.engine.lifecycle.replay import replay

if TYPE_CHECKING:
    from verdx.contracts.states import State

_APPROVAL_EVENTS = frozenset(
    {
        EventType.approval_granted,
        EventType.approval_rejected,
        EventType.review_submitted,
        EventType.override_requested,
        EventType.override_granted,
        EventType.override_rejected,
        EventType.carry_forward_granted,
        EventType.fast_path_authorized,
    }
)


def timeline(events: list[StateEvent], intent_id: str) -> list[TimelineEntry]:
    """Project events for an intent into a timeline."""
    entries: list[TimelineEntry] = []
    for i, ev in enumerate(e for e in events if e.intent_id == intent_id):
        entries.append(
            TimelineEntry(
                index=i,
                intent_id=ev.intent_id,
                event_type=ev.event_type.value,
                previous_state=ev.previous_state.value if ev.previous_state else None,
                next_state=ev.next_state.value,
                actor=ev.actor,
                authority_role=ev.authority_role.value,
                timestamp=ev.timestamp.isoformat(),
                elapsed_seconds=ev.elapsed_seconds,
                detail=ev.detail,
                artifact_refs_created=ev.artifact_refs.created,
                reason_tags=tuple(t.value for t in ev.reason_tags),
            )
        )
    return entries


def who_approved(
    events: list[StateEvent],
    intent_id: str,
    gate_state: State,
) -> list[ApprovalRecord]:
    """Find approval events at a specific gate for an intent."""
    results: list[ApprovalRecord] = []
    for ev in events:
        if (
            ev.intent_id == intent_id
            and ev.previous_state == gate_state
            and ev.event_type in _APPROVAL_EVENTS
        ):
            results.append(_approval_record(ev))
    return results


def evidence_at_gate(
    events: list[StateEvent],
    intent_id: str,
    gate_state: State,
) -> list[ArtifactRef]:
    """Collect artifact refs accumulated up to and including a gate crossing."""
    accumulated: list[ArtifactRef] = []
    idx = 0
    for ev in events:
        if ev.intent_id != intent_id:
            continue
        for ref in ev.artifact_refs.created:
            accumulated.append(
                ArtifactRef(
                    ref=ref,
                    created_by_actor=ev.actor,
                    created_at=ev.timestamp.isoformat(),
                    created_at_event_index=idx,
                )
            )
        if ev.previous_state == gate_state and ev.event_type in _APPROVAL_EVENTS:
            return accumulated
        idx += 1
    return []


def approval_chain(events: list[StateEvent], intent_id: str) -> list[ApprovalRecord]:
    """All approval events for an intent, in order."""
    return [
        _approval_record(ev)
        for ev in events
        if ev.intent_id == intent_id and ev.event_type in _APPROVAL_EVENTS
    ]


def compliance_summary(
    events: list[StateEvent],
    intent_id: str,
) -> ComplianceSummary:
    """Build a compliance summary for an intent."""
    filtered = [e for e in events if e.intent_id == intent_id]
    result = replay(filtered)
    intent_result = result.intents.get(intent_id)

    chain = approval_chain(events, intent_id)

    actors = sorted({ev.actor for ev in filtered})
    roles = sorted({ev.authority_role.value for ev in filtered})

    if intent_result is None:
        return ComplianceSummary(
            intent_id=intent_id,
            final_state=None,
            total_events=0,
            gates_satisfied=0,
            gates_total=0,
            violations=(),
            rework_count=0,
            actors_involved=tuple(actors),
            roles_involved=tuple(roles),
            approval_chain=tuple(chain),
            cycle_time_seconds=None,
            total_elapsed_seconds=0.0,
            total_attention_minutes=0.0,
            total_wait_minutes=0.0,
            reason_tag_counts={},
        )

    ir = intent_result
    m = ir.metrics
    return ComplianceSummary(
        intent_id=intent_id,
        final_state=ir.final_state.value if ir.final_state else None,
        total_events=ir.event_count,
        gates_satisfied=sum(1 for g in ir.gates if g.satisfied),
        gates_total=len(ir.gates),
        violations=ir.violations,
        rework_count=m.rework_count if m else 0,
        actors_involved=tuple(actors),
        roles_involved=tuple(roles),
        approval_chain=tuple(chain),
        cycle_time_seconds=m.cycle_time_seconds if m else None,
        total_elapsed_seconds=m.total_elapsed_seconds if m else 0.0,
        total_attention_minutes=m.total_attention_minutes if m else 0.0,
        total_wait_minutes=m.total_wait_minutes if m else 0.0,
        reason_tag_counts=dict(m.reason_tag_counts) if m else {},
        started_as_fast_path=m.started_as_fast_path if m else False,
    )


def format_trace(entries: list[TimelineEntry], *, fmt: str = "ascii") -> str:
    """Render a timeline as ASCII or Mermaid."""
    if fmt == "mermaid":
        return _format_mermaid(entries)
    return _format_ascii(entries)


def format_compliance_report(
    summary: ComplianceSummary,
    entries: list[TimelineEntry],
) -> str:
    """Render a compliance report as markdown."""
    lines: list[str] = []
    lines.append(f"# Compliance Report: {summary.intent_id}")
    lines.append("")

    # Status
    lines.append("## Status")
    lines.append("")
    status = summary.final_state or "unknown"
    lines.append(f"- **Final state**: {status}")
    lines.append(f"- **Total events**: {summary.total_events}")
    lines.append(f"- **Rework count**: {summary.rework_count}")
    if summary.started_as_fast_path:
        lines.append("- **Fast path**: yes")
    lines.append("")

    # Gate Summary
    lines.append("## Gate Summary")
    lines.append("")
    lines.append(f"- **Gates satisfied**: {summary.gates_satisfied}/{summary.gates_total}")
    lines.append("")

    # Approval Chain
    lines.append("## Approval Chain")
    lines.append("")
    if summary.approval_chain:
        lines.append("| Event | Gate | Actor | Role | Timestamp |")
        lines.append("|-------|------|-------|------|-----------|")
        for rec in summary.approval_chain:
            lines.append(
                f"| {rec.event_type} | {rec.gate_state} "
                f"| {rec.actor} | {rec.authority_role} "
                f"| {rec.timestamp[:19]} |"
            )
    else:
        lines.append("No approvals recorded.")
    lines.append("")

    # Timing Metrics
    lines.append("## Timing Metrics")
    lines.append("")
    if summary.cycle_time_seconds is not None:
        lines.append(f"- **Cycle time**: {summary.cycle_time_seconds:.1f}s")
    lines.append(f"- **Elapsed**: {summary.total_elapsed_seconds:.1f}s")
    lines.append(f"- **Attention**: {summary.total_attention_minutes:.1f} min")
    lines.append(f"- **Wait**: {summary.total_wait_minutes:.1f} min")
    lines.append("")

    # Violations
    if summary.violations:
        lines.append("## Violations")
        lines.append("")
        for v in summary.violations:
            lines.append(f"- {v}")
        lines.append("")

    # Actors
    lines.append("## Actors")
    lines.append("")
    lines.append(f"- **Actors**: {', '.join(summary.actors_involved)}")
    lines.append(f"- **Roles**: {', '.join(summary.roles_involved)}")
    lines.append("")

    # Timeline (abbreviated)
    lines.append("## Timeline")
    lines.append("")
    display = entries[:20]
    for e in display:
        prev = e.previous_state or "(none)"
        lines.append(f"- [{e.index}] {prev} -> {e.next_state} ({e.event_type}, {e.actor})")
    if len(entries) > 20:
        lines.append(f"- ... ({len(entries) - 20} more events)")
    lines.append("")

    return "\n".join(lines)


# -- private helpers --


def _approval_record(ev: StateEvent) -> ApprovalRecord:
    return ApprovalRecord(
        intent_id=ev.intent_id,
        event_type=ev.event_type.value,
        gate_state=ev.previous_state.value if ev.previous_state else "",
        actor=ev.actor,
        authority_role=ev.authority_role.value,
        timestamp=ev.timestamp.isoformat(),
        artifact_refs=ev.artifact_refs.created,
        detail=ev.detail,
        reason_tags=tuple(t.value for t in ev.reason_tags),
    )


def _format_ascii(entries: list[TimelineEntry]) -> str:
    if not entries:
        return "No events."
    lines: list[str] = []
    iid = entries[0].intent_id
    lines.append(f"Trace: {iid} ({len(entries)} events)")
    lines.append("")
    for e in entries:
        prev = e.previous_state or "(none)"
        line = (
            f"{e.index:>3}  {e.timestamp[:19]}  {prev:>18} -> {e.next_state:<18}"
            f"  {e.event_type:<26}  {e.actor}/{e.authority_role}"
        )
        if e.artifact_refs_created:
            refs = ", ".join(e.artifact_refs_created)
            line += f"  [{refs}]"
        lines.append(line)
    return "\n".join(lines)


def _format_mermaid(entries: list[TimelineEntry]) -> str:
    if not entries:
        return "No events."
    lines: list[str] = ["stateDiagram-v2"]
    for e in entries:
        src = e.previous_state or "[*]"
        dst = e.next_state
        label = f"{e.event_type} ({e.actor})"
        lines.append(f"    {src} --> {dst} : {label}")
    return "\n".join(lines)
