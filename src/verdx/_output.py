"""Output formatting — text and JSON renderers, format auto-detection."""

from __future__ import annotations

import dataclasses
import json
import sys

from verdx.types import Assessment, FixResult


def auto_detect_format() -> str:
    """Return 'text' for TTY, 'json' when piped."""
    return "text" if sys.stdout.isatty() else "json"


def format_json(assessment: Assessment) -> str:
    """Render an assessment as indented JSON."""
    return json.dumps(dataclasses.asdict(assessment), indent=2, sort_keys=False)


def format_text(assessment: Assessment) -> str:
    """Render a human-readable summary for terminal output."""
    lines: list[str] = []

    bar = _score_bar(assessment.score)
    lines.append(f"Score: {assessment.score}/100  {bar}  [{assessment.verdict.upper()}]")
    lines.append("")

    if assessment.observations:
        lines.append(f"Findings ({len(assessment.observations)}):")
        for obs in assessment.observations:
            loc = f"  {obs.location}" if obs.location else ""
            mark = f"  ({obs.disposition})" if obs.disposition else ""
            lines.append(f"  [{obs.severity.upper():>8}] {obs.message}{loc}{mark}")
        lines.append("")

    gate = (assessment.metadata or {}).get("gate") or {}
    if assessment.verdict == "block":
        gate_notes: list[str] = []
        if gate.get("coverage_status") == "degraded":
            gate_notes.append("coverage degraded")
        if gate.get("confidence") == "low":
            gate_notes.append("confidence low")
        if gate_notes:
            lines.append(f"Blocked on: {', '.join(gate_notes)}")
            for g in gate.get("coverage_gaps", []):
                lines.append(f"  gap: {g['validator_id']} ({g['reason']})")
            lines.append("")

    if assessment.overrides_applied:
        lines.append(f"Overrides applied: {len(assessment.overrides_applied)}")
        for ov in assessment.overrides_applied:
            lines.append(f"  {ov.signature[:12]}... {ov.decision} ({ov.justification})")
        lines.append("")

    lines.append(f"Validators: {len(assessment.validators_run)}")
    lines.append(f"Hash: {assessment.decision_hash[:16]}...")

    return "\n".join(lines)


def format_fix_json(result: FixResult) -> str:
    """Render a fix result as indented JSON."""
    return json.dumps(dataclasses.asdict(result), indent=2, sort_keys=False)


def format_fix_text(result: FixResult, *, dry_run: bool = False) -> str:
    """Render a fix result for terminal output."""
    lines: list[str] = []
    prefix = "[dry-run] " if dry_run else ""

    before = result.assessment_before.score
    after = result.assessment_after.score
    delta = after - before

    lines.append(f"{prefix}Score: {before} -> {after} ({'+' if delta >= 0 else ''}{delta})")
    lines.append("")

    if result.fixes_applied:
        lines.append(f"Fixes applied ({len(result.fixes_applied)}):")
        for action in result.fixes_applied:
            lines.append(f"  [{action.fixer_id}] {action.description}")
        lines.append("")
    else:
        lines.append("No fixable observations found.")
        lines.append("")

    remaining = len(result.remaining_observations)
    if remaining:
        lines.append(f"Remaining observations: {remaining}")

    lines.append(f"Iterations: {result.iterations}")
    lines.append(f"Verdict: {result.assessment_after.verdict.upper()}")

    return "\n".join(lines)


def _score_bar(score: int, width: int = 20) -> str:
    """Render a text progress bar for the score."""
    filled = round(score / 100 * width)
    return f"[{'#' * filled}{'.' * (width - filled)}]"
