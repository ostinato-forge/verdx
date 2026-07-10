"""Reporting: JSON, Markdown, and CI format renderers.

Pure functions. All inputs explicit. No I/O.

JSON is the full RunAssessment serialization (machine-readable).
Markdown leads with positive signal and enforces privacy rules:
no producer_id, no individual names, no per-actor counts.
JUnit and SARIF are CI-oriented summaries. They include observations, coverage
gaps, and recovery items so incomplete validation cannot be reported as clean.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from verdx.contracts.dimensions import EscalationLevel
from verdx.contracts.subjects import SubjectCoverageState

if TYPE_CHECKING:
    from verdx.contracts.assessment import CoverageGap, RecoveryItem, RunAssessment
    from verdx.contracts.observation import Observation
    from verdx.contracts.subjects import SubjectCoverageRecord

_SUBJECT_DEBT_STATES = {
    SubjectCoverageState.PARTIAL,
    SubjectCoverageState.FAILED,
    SubjectCoverageState.SKIPPED,
    SubjectCoverageState.MISSING,
    SubjectCoverageState.UNSUPPORTED,
    SubjectCoverageState.MODEL_LIMITED,
}


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def render_json(assessment: RunAssessment) -> str:
    """Render a RunAssessment as deterministic JSON."""
    return json.dumps(assessment.to_dict(), indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# JUnit report
# ---------------------------------------------------------------------------


def render_junit(assessment: RunAssessment) -> str:
    """Render a RunAssessment as JUnit XML for CI systems.

    JUnit is issue-oriented here: observations are failures, while incomplete
    coverage, recovery, and subject debt are errors.
    """
    subject_debt = _subject_coverage_debt(assessment)
    failure_count = len(assessment.observations)
    error_count = len(assessment.coverage_gaps) + len(assessment.recovery_items) + len(subject_debt)
    case_count = failure_count + error_count
    tests = case_count if case_count else 1

    suite = ET.Element(
        "testsuite",
        {
            "name": "verdx",
            "tests": str(tests),
            "failures": str(failure_count),
            "errors": str(error_count),
            "skipped": "0",
            "timestamp": assessment.timestamp,
        },
    )
    props = ET.SubElement(suite, "properties")
    _junit_property(props, "run_id", assessment.run_id)
    _junit_property(props, "target_key", assessment.target_key)
    _junit_property(props, "target_content_hash", assessment.target_content_hash)
    _junit_property(props, "coverage_status", assessment.coverage_status.value)
    _junit_property(props, "confidence", assessment.confidence.value)
    _junit_property(props, "score", str(assessment.score.overall))
    _junit_property(
        props,
        "junit_semantics",
        "observations-as-failures; coverage/recovery/subject-debt-as-errors",
    )

    for obs in assessment.observations:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"verdx.{obs.category}",
                "name": _observation_case_name(obs),
            },
        )
        failure = ET.SubElement(
            case,
            "failure",
            {
                "type": obs.escalation.value,
                "message": obs.summary,
            },
        )
        failure.text = _observation_detail(obs)

    for gap in assessment.coverage_gaps:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "verdx.coverage",
                "name": f"coverage gap: {gap.validator_id}",
            },
        )
        error = ET.SubElement(
            case,
            "error",
            {
                "type": "coverage-gap",
                "message": gap.reason,
            },
        )
        error.text = _coverage_gap_detail(gap)

    for item in assessment.recovery_items:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "verdx.recovery",
                "name": f"recovery item: {item.validator_id}",
            },
        )
        error = ET.SubElement(
            case,
            "error",
            {
                "type": item.kind,
                "message": item.description,
            },
        )
        error.text = _recovery_item_detail(item)

    for record in subject_debt:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "verdx.subject-coverage",
                "name": f"subject coverage: {record.subject.subject_key}",
            },
        )
        error = ET.SubElement(
            case,
            "error",
            {
                "type": "subject-coverage",
                "message": _subject_debt_message(record),
            },
        )
        error.text = _subject_debt_detail(record)

    if case_count == 0:
        ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "verdx.assessment",
                "name": "assessment clean",
            },
        )

    ET.indent(suite, space="  ")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        + ET.tostring(
            suite,
            encoding="unicode",
        )
        + "\n"
    )


def _junit_property(parent: ET.Element, name: str, value: str) -> None:
    ET.SubElement(parent, "property", {"name": name, "value": value})


def _observation_case_name(obs: Observation) -> str:
    location = f" at {obs.location}" if obs.location else ""
    return f"{obs.rule_id}{location}"


def _observation_detail(obs: Observation) -> str:
    lines = [
        obs.summary,
        f"Rule: {obs.rule_id}",
        f"Category: {obs.category}",
        f"Severity: {obs.severity.value}",
        f"Escalation: {obs.escalation.value}",
        f"Confidence: {obs.confidence.value}",
        f"Validator: {obs.validator_id} v{obs.validator_version}",
        f"Fingerprint: {obs.issue_fingerprint}",
        f"Four-Eyes: {_gate_check_status(obs)}",
    ]
    if obs.location:
        lines.append(f"Location: {obs.location}")
    if obs.disposition:
        lines.append(f"Disposition: {obs.disposition.decision} ({obs.disposition.effect})")
    if obs.evidence:
        lines.append("Evidence:")
        for ev in obs.evidence:
            lines.append(f"- {ev.source}/{ev.kind}: {ev.details}")
    return "\n".join(lines)


def _coverage_gap_detail(gap: CoverageGap) -> str:
    lines = [
        f"Validator: {gap.validator_id}",
        f"Reason: {gap.reason}",
        f"Impact: {gap.impact}",
    ]
    if gap.dimensions_affected:
        lines.append(f"Dimensions affected: {', '.join(gap.dimensions_affected)}")
    return "\n".join(lines)


def _recovery_item_detail(item: RecoveryItem) -> str:
    lines = [
        f"Validator: {item.validator_id}",
        f"Kind: {item.kind}",
        f"Description: {item.description}",
    ]
    if item.context:
        lines.append(f"Context keys: {', '.join(sorted(item.context))}")
    return "\n".join(lines)


def _subject_coverage_debt(assessment: RunAssessment) -> list[SubjectCoverageRecord]:
    return [
        record for record in assessment.subject_coverage if record.state in _SUBJECT_DEBT_STATES
    ]


def _subject_debt_message(record: SubjectCoverageRecord) -> str:
    return f"{record.subject.subject_key} is {record.state.value}"


def _subject_debt_detail(record: SubjectCoverageRecord) -> str:
    lines = [
        f"Subject: {record.subject.subject_key}",
        f"State: {record.state.value}",
        f"Criticality: {record.criticality}",
        f"Required validators: {', '.join(record.required_validator_ids)}",
    ]
    if record.completed_validator_ids:
        lines.append(f"Completed validators: {', '.join(record.completed_validator_ids)}")
    if record.failed_validator_ids:
        lines.append(f"Failed validators: {', '.join(record.failed_validator_ids)}")
    if record.skipped_validator_ids:
        lines.append(f"Skipped validators: {', '.join(record.skipped_validator_ids)}")
    if record.blocking_reason:
        lines.append(f"Blocking reason: {record.blocking_reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SARIF report
# ---------------------------------------------------------------------------


def render_sarif(assessment: RunAssessment) -> str:
    """Render a RunAssessment as SARIF 2.1.0 JSON."""
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    subject_debt = _subject_coverage_debt(assessment)

    for obs in assessment.observations:
        rules.setdefault(obs.rule_id, _sarif_rule_for_observation(obs))
        results.append(_sarif_result_for_observation(obs))

    if assessment.coverage_gaps:
        rules.setdefault("verdx.coverage-gap", _sarif_rule("verdx.coverage-gap"))
    for gap in assessment.coverage_gaps:
        results.append(_sarif_result_for_coverage_gap(gap))

    if assessment.recovery_items:
        rules.setdefault("verdx.recovery", _sarif_rule("verdx.recovery"))
    for item in assessment.recovery_items:
        results.append(_sarif_result_for_recovery_item(item))

    if subject_debt:
        rules.setdefault(
            "verdx.subject-coverage",
            _sarif_rule("verdx.subject-coverage"),
        )
    for record in subject_debt:
        results.append(_sarif_result_for_subject_debt(record))

    report = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "verdx",
                        "informationUri": "https://github.com/ostinato-forge/verdx",
                        "rules": [rules[key] for key in sorted(rules)],
                    },
                },
                "results": results,
                "properties": {
                    "run_id": assessment.run_id,
                    "target_key": assessment.target_key,
                    "target_content_hash": assessment.target_content_hash,
                    "coverage_status": assessment.coverage_status.value,
                    "confidence": assessment.confidence.value,
                    "score": assessment.score.overall,
                },
            },
        ],
    }
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _sarif_rule_for_observation(obs: Observation) -> dict[str, Any]:
    return _sarif_rule(
        obs.rule_id,
        name=obs.rule_id,
        short_description=obs.category,
        properties={
            "category": obs.category,
            "validator_id": obs.validator_id,
        },
    )


def _sarif_rule(
    rule_id: str,
    *,
    name: str | None = None,
    short_description: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": rule_id,
        "name": name or rule_id,
        "shortDescription": {"text": short_description or rule_id},
    }
    if properties:
        rule["properties"] = properties
    return rule


def _sarif_result_for_observation(obs: Observation) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": obs.rule_id,
        "level": _sarif_level(obs.escalation),
        "message": {"text": obs.summary},
        "properties": {
            "category": obs.category,
            "severity": obs.severity.value,
            "escalation": obs.escalation.value,
            "confidence": obs.confidence.value,
            "validator_id": obs.validator_id,
            "validator_version": obs.validator_version,
            "issue_fingerprint": obs.issue_fingerprint,
            "verdx": _gate_check_status(obs),
        },
    }
    if obs.location:
        result["locations"] = [_sarif_location(obs.location)]
    return result


def _sarif_result_for_coverage_gap(gap: CoverageGap) -> dict[str, Any]:
    return {
        "ruleId": "verdx.coverage-gap",
        "level": "error",
        "message": {"text": f"Coverage gap in {gap.validator_id}: {gap.reason}"},
        "properties": {
            "validator_id": gap.validator_id,
            "impact": gap.impact,
            "dimensions_affected": list(gap.dimensions_affected),
        },
    }


def _sarif_result_for_recovery_item(item: RecoveryItem) -> dict[str, Any]:
    return {
        "ruleId": "verdx.recovery",
        "level": "error",
        "message": {"text": item.description},
        "properties": {
            "validator_id": item.validator_id,
            "kind": item.kind,
            "context_keys": sorted(item.context),
        },
    }


def _sarif_result_for_subject_debt(record: SubjectCoverageRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": "verdx.subject-coverage",
        "level": "error" if record.criticality == "critical" else "warning",
        "message": {"text": _subject_debt_message(record)},
        "properties": {
            "subject_key": record.subject.subject_key,
            "state": record.state.value,
            "criticality": record.criticality,
            "required_validator_ids": list(record.required_validator_ids),
            "completed_validator_ids": list(record.completed_validator_ids),
            "failed_validator_ids": list(record.failed_validator_ids),
            "skipped_validator_ids": list(record.skipped_validator_ids),
            "blocking_reason": record.blocking_reason,
        },
    }
    if record.subject.location:
        result["locations"] = [_sarif_location(record.subject.location)]
    return result


def _sarif_location(location: str) -> dict[str, Any]:
    return {"physicalLocation": {"artifactLocation": {"uri": location}}}


def _sarif_level(level: EscalationLevel) -> str:
    if level in {EscalationLevel.L3, EscalationLevel.L4}:
        return "error"
    if level == EscalationLevel.L2:
        return "warning"
    return "note"


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


_ESCALATION_LABELS: dict[EscalationLevel, str] = {
    EscalationLevel.L0: "Informational",
    EscalationLevel.L1: "Low Priority",
    EscalationLevel.L2: "Medium Priority",
    EscalationLevel.L3: "Requires Human Decision",
    EscalationLevel.L4: "Blocking",
}

_TREND_ARROWS = {
    "improving": "trending better",
    "stable": "stable",
    "regressing": "trending worse",
}


def render_markdown(assessment: RunAssessment) -> str:
    """Render a human-readable Markdown report.

    Section order (positive signal first):
    1. Title and run metadata
    2. Coverage and confidence
    3. Improvement trend
    4. Clean summary
    5. Requires Human Decision (L3+)
    6. For Your Awareness (L0-L2)
    7. Score and caveats
    8. Recovery items
    9. Machine-readable artifact pointers
    """
    sections: list[str] = []

    sections.append(_section_title(assessment))
    sections.append(_section_coverage(assessment))
    sections.append(_section_subject_coverage(assessment))
    sections.append(_section_improvement(assessment))
    sections.append(_section_clean(assessment))
    sections.append(_section_human_decision(assessment))
    sections.append(_section_awareness(assessment))
    sections.append(_section_score(assessment))
    sections.append(_section_recovery(assessment))
    sections.append(_section_artifacts(assessment))

    return "\n\n".join(s for s in sections if s) + "\n"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _section_title(a: RunAssessment) -> str:
    lines = [
        f"# Assessment: {a.target_key}",
        "",
        f"- **Run**: `{a.run_id}`",
        f"- **Timestamp**: {a.timestamp}",
        f"- **Content hash**: `{a.target_content_hash}`",
    ]
    return "\n".join(lines)


def _section_subject_coverage(a: RunAssessment) -> str:
    if not a.subject_coverage:
        return ""

    lines = ["## Subject Coverage", ""]
    by_state: dict[SubjectCoverageState, int] = {}
    by_criticality: dict[str, int] = {}
    debt = []

    for record in a.subject_coverage:
        by_state[record.state] = by_state.get(record.state, 0) + 1
        by_criticality[record.criticality] = by_criticality.get(record.criticality, 0) + 1
        if record.state in _SUBJECT_DEBT_STATES:
            debt.append(record)

    lines.append("### Summary")
    lines.append("")
    for state in sorted(by_state, key=lambda item: item.value):
        lines.append(f"- **{state.value}**: {by_state[state]}")
    lines.append("")
    lines.append("### Criticality")
    lines.append("")
    for criticality in ("critical", "standard", "optional"):
        if criticality in by_criticality:
            lines.append(f"- **{criticality}**: {by_criticality[criticality]}")

    critical_debt = [record for record in debt if record.criticality == "critical"]
    if critical_debt:
        lines.append("")
        lines.append("### Critical Subject Debt")
        lines.append("")
        for record in critical_debt:
            reason = f": {record.blocking_reason}" if record.blocking_reason else ""
            lines.append(f"- **{record.state.value}** `{record.subject.subject_key}`{reason}")

    standard_debt = [record for record in debt if record.criticality == "standard"]
    if standard_debt:
        lines.append("")
        lines.append("### Standard Subject Debt")
        lines.append("")
        for kind in sorted({record.subject.kind for record in standard_debt}):
            keys = [
                f"`{record.subject.subject_key}`"
                for record in standard_debt
                if record.subject.kind == kind
            ]
            lines.append(f"- **{kind}**: {', '.join(keys)}")

    return "\n".join(lines)


def _section_coverage(a: RunAssessment) -> str:
    lines = [
        "## Coverage and Confidence",
        "",
        f"- **Coverage**: {a.coverage_status.value}",
        f"- **Confidence**: {a.confidence.value}",
    ]

    if a.coverage_gaps:
        lines.append("")
        lines.append("### Coverage Gaps")
        lines.append("")
        for gap in a.coverage_gaps:
            dims = ""
            if gap.dimensions_affected:
                dims = f" (affects: {', '.join(gap.dimensions_affected)})"
            lines.append(f"- **{gap.validator_id}**: {gap.reason}{dims}")
            lines.append(f"  - Impact: {gap.impact}")

    return "\n".join(lines)


def _section_improvement(a: RunAssessment) -> str:
    if a.improvement is None:
        return ""

    imp = a.improvement
    arrow = _TREND_ARROWS.get(imp.trend.value, imp.trend.value)
    lines = [
        "## Improvement",
        "",
        f"- **Trend**: {arrow}",
        f"- **Resolved**: {imp.resolved_count}",
        f"- **New**: {imp.new_count}",
        f"- **Persistent**: {imp.persistent_count}",
    ]
    return "\n".join(lines)


def _section_clean(a: RunAssessment) -> str:
    lines = [
        "## Clean Summary",
        "",
        f"- **Checks run**: {a.checks_run}",
        f"- **Checks passed**: {a.checks_passed}",
        f"- **Validators clean**: {a.validators_clean}",
    ]
    return "\n".join(lines)


def _section_human_decision(a: RunAssessment) -> str:
    high_obs = [
        obs for obs in a.observations if obs.escalation in (EscalationLevel.L3, EscalationLevel.L4)
    ]
    if not high_obs:
        return ""

    lines = ["## Requires Human Decision", ""]

    for obs in high_obs:
        label = _ESCALATION_LABELS[obs.escalation]
        lines.append(f"### [{label}] {obs.summary}")
        lines.append("")
        lines.append(f"- **Rule**: `{obs.rule_id}`")
        lines.append(f"- **Category**: {obs.category}")
        lines.append(f"- **Severity**: {obs.severity.value}")
        lines.append(f"- **Confidence**: {obs.confidence.value}")
        if obs.location:
            lines.append(f"- **Location**: `{obs.location}`")
        lines.append(f"- **Validator**: {obs.validator_id} v{obs.validator_version}")
        lines.append(f"- **Four-Eyes**: {_gate_check_status(obs)}")

        if obs.disposition:
            d = obs.disposition
            lines.append("")
            lines.append(
                f"> **Disposition**: {d.decision} by {d.team} "
                f"({d.effect}: {d.original_escalation.value} "
                f"-> {d.final_escalation.value})"
            )
            if d.expires:
                lines.append(f"> Expires: {d.expires}")

        if obs.evidence:
            lines.append("")
            lines.append("**Evidence:**")
            lines.append("")
            for ev in obs.evidence:
                lines.append(f"- {ev.source}/{ev.kind}: {ev.details}")

        if obs.invalidating_conditions:
            lines.append("")
            lines.append("**Invalidating conditions:**")
            lines.append("")
            for ic in obs.invalidating_conditions:
                lines.append(f"- {ic}")

        lines.append("")

    return "\n".join(lines).rstrip()


def _section_awareness(a: RunAssessment) -> str:
    low_obs = [
        obs
        for obs in a.observations
        if obs.escalation in (EscalationLevel.L0, EscalationLevel.L1, EscalationLevel.L2)
    ]
    if not low_obs:
        return ""

    lines = ["## For Your Awareness", ""]

    by_rule: dict[str, list[Observation]] = {}
    for obs in low_obs:
        by_rule.setdefault(obs.rule_id, []).append(obs)

    for rule_id in sorted(by_rule):
        group = by_rule[rule_id]
        lines.append(f"### `{rule_id}` ({len(group)} finding{'s' if len(group) != 1 else ''})")
        lines.append("")
        for obs in group:
            label = _ESCALATION_LABELS[obs.escalation]
            loc = f" at `{obs.location}`" if obs.location else ""
            disp_note = ""
            if obs.disposition:
                disp_note = f" [{obs.disposition.decision}]"
            lines.append(
                f"- [{label}] {obs.summary}{loc}{disp_note} (Four-Eyes: {_gate_check_status(obs)})"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


def _gate_check_status(obs: Observation) -> str:
    return f"{obs.independence.result} ({obs.independence.relation})"


def _section_score(a: RunAssessment) -> str:
    lines = [
        "## Score",
        "",
        f"**{a.score.overall}/100**",
    ]

    if a.score.coverage_penalty > 0:
        lines.append(f"- Coverage penalty: -{a.score.coverage_penalty}")
    if a.score.independence_penalty > 0:
        lines.append(f"- Independence penalty: -{a.score.independence_penalty}")

    if a.score.comparison:
        delta = a.score.comparison.delta
        direction = "+" if delta > 0 else ""
        lines.append(
            f"- vs. previous: {direction}{delta} (was {a.score.comparison.previous_score})"
        )

    caveats = _score_caveats(a)
    if caveats:
        lines.append("")
        lines.append("**Caveats:**")
        lines.append("")
        for caveat in caveats:
            lines.append(f"- {caveat}")

    return "\n".join(lines)


def _score_caveats(a: RunAssessment) -> list[str]:
    caveats: list[str] = []
    if a.coverage_status.value != "full":
        caveats.append(
            f"Coverage is {a.coverage_status.value} — score may not reflect full picture."
        )
    if a.coverage_gaps:
        validator_ids = [g.validator_id for g in a.coverage_gaps]
        caveats.append(f"Coverage gaps in: {', '.join(validator_ids)}.")
    critical_subject_debt = [
        record.subject.subject_key
        for record in a.subject_coverage
        if record.criticality == "critical"
        and record.state
        in {
            SubjectCoverageState.PARTIAL,
            SubjectCoverageState.FAILED,
            SubjectCoverageState.SKIPPED,
            SubjectCoverageState.MISSING,
            SubjectCoverageState.UNSUPPORTED,
            SubjectCoverageState.MODEL_LIMITED,
        }
    ]
    if critical_subject_debt:
        caveats.append(f"Critical subject debt in: {', '.join(critical_subject_debt)}.")
    return caveats


def _section_recovery(a: RunAssessment) -> str:
    if not a.recovery_items:
        return ""

    lines = ["## Recovery Items", ""]
    for ri in a.recovery_items:
        lines.append(f"- **[{ri.kind}]** {ri.description} (`{ri.validator_id}`)")

    return "\n".join(lines)


def _section_artifacts(a: RunAssessment) -> str:
    lines = [
        "---",
        "",
        f"Assessment: `{a.schema_version}`  ",
        f"Run: `{a.run_id}`  ",
        f"Target: `{a.target_key}`  ",
        f"Hash: `{a.target_content_hash}`",
    ]
    return "\n".join(lines)
