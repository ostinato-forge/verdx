"""Recompute decisions from frozen inputs and verify saved artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from verdx._content import canonical_hash, file_digest, matches
from verdx.acceptance.models import (
    Artifact,
    CheckCoverage,
    CheckDecision,
    CheckEvidence,
    CheckRule,
    CheckStatus,
    Decision,
    Receipt,
)
from verdx.acceptance.policy import parse_policy, policy_hash
from verdx.contracts.assessment import RunAssessment

EXIT_CODES = {"pass": 0, "fail": 1, "incomplete": 2, "not_applicable": 3}


def artifact_path(root: Path, artifact: Artifact) -> Path:
    candidate = root / artifact.path
    for part in [candidate, *candidate.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"artifact is a symlink: {artifact.path}")
    if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError(f"missing or unsafe artifact: {artifact.path}")
    if candidate.stat().st_size != artifact.size or file_digest(candidate) != artifact.sha256:
        raise ValueError(f"artifact digest mismatch: {artifact.path}")
    return candidate


def _domain_decision(rule: CheckRule, evidence: CheckEvidence, root: Path) -> CheckDecision:
    execution = evidence.execution
    if execution.returncode != 0 or execution.stdout is None:
        return CheckDecision(
            id=rule.id,
            required=rule.required,
            status="incomplete",
            coverage="incomplete",
            reason="domain execution did not produce a complete assessment",
        )
    try:
        path = artifact_path(root, execution.stdout)
        assessment = RunAssessment.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return CheckDecision(
            id=rule.id,
            required=rule.required,
            status="incomplete",
            coverage="incomplete",
            reason=f"invalid domain assessment: {type(exc).__name__}",
        )
    statuses = assessment.validator_statuses
    no_applicable_files = statuses and all(
        status.status == "skipped" and (status.skip_reason or "").startswith("no applicable ")
        for status in statuses
    )
    incomplete = (
        not statuses
        or assessment.checks_run == 0
        or assessment.coverage_status.value != "full"
        or assessment.confidence.value == "low"
        or any(item.status != "completed" for item in statuses)
        or assessment.coverage_gaps
        or assessment.recovery_items
    )
    findings = sum(item.severity.value != "info" for item in assessment.observations)
    status: CheckStatus
    coverage: CheckCoverage
    if findings:
        status, reason = "fail", f"domain reported {findings} finding(s)"
        coverage = "incomplete" if incomplete else "full"
        if incomplete:
            reason += "; domain coverage is incomplete"
    elif no_applicable_files:
        status, coverage, reason = (
            "not_applicable",
            "none",
            "the domain found no applicable files",
        )
    elif incomplete:
        status, coverage, reason = "incomplete", "incomplete", "domain coverage is incomplete"
    else:
        status, coverage, reason = "pass", "full", "all domain checks completed without findings"
    return CheckDecision(
        id=rule.id, required=rule.required, status=status, coverage=coverage, reason=reason
    )


def check_decision(rule: CheckRule, evidence: CheckEvidence, root: Path) -> CheckDecision:
    execution = evidence.execution
    version = evidence.version
    status: CheckStatus
    if execution.state == "not_applicable":
        status, reason = "not_applicable", execution.error or "no matching input files"
    elif rule.version_command is not None and (
        version is None or version.state != "completed" or version.returncode != 0
    ):
        status, reason = "incomplete", "checker version probe did not complete"
    elif execution.state != "completed":
        status, reason = "incomplete", execution.error or execution.state
    elif rule.domain is not None:
        return _domain_decision(rule, evidence, root)
    elif execution.returncode in rule.success_codes:
        status, reason = "pass", f"command exited with {execution.returncode}"
    elif execution.returncode in rule.failure_codes:
        status, reason = "fail", f"command exited with {execution.returncode}"
    elif execution.returncode in rule.not_applicable_codes:
        status, reason = (
            "not_applicable",
            f"command reported no applicable checks ({execution.returncode})",
        )
    else:
        status, reason = "incomplete", f"unmapped exit code {execution.returncode}"
    coverage: CheckCoverage = (
        "full"
        if status in ("pass", "fail")
        else "none"
        if status == "not_applicable"
        else "incomplete"
    )
    return CheckDecision(
        id=rule.id, required=rule.required, status=status, coverage=coverage, reason=reason
    )


def derive_decision(receipt: Receipt, root: Path) -> Decision:
    expected = [rule.id for rule in receipt.policy.checks]
    actual = [item.id for item in receipt.evidence]
    if actual != expected:
        raise ValueError("receipt must include every configured check once, in policy order")
    for rule, evidence in zip(receipt.policy.checks, receipt.evidence, strict=True):
        applies = not rule.paths or any(
            matches(file.path, tuple(rule.paths)) for file in receipt.before
        )
        if applies and evidence.execution.state == "not_applicable":
            raise ValueError(
                "an applicable check cannot be recorded as not applicable without running"
            )
        if not applies and evidence.execution.state == "completed":
            raise ValueError("a check ran outside its declared applicability")
    checks = [
        check_decision(rule, evidence, root)
        for rule, evidence in zip(receipt.policy.checks, receipt.evidence, strict=True)
    ]
    required = [check for check in checks if check.required]
    reasons: list[str] = []
    if receipt.before != receipt.after:
        reasons.append("input files changed while checks were running")
    if receipt.input_error:
        reasons.append(receipt.input_error)
    if reasons:
        return Decision(outcome="incomplete", coverage="incomplete", reasons=reasons, checks=checks)
    if not required or all(check.status == "not_applicable" for check in required):
        return Decision(
            outcome="not_applicable",
            coverage="none",
            reasons=["no required checks evaluated applicable work"],
            checks=checks,
        )
    missing = [check for check in required if check.coverage != "full"]
    failed = [check for check in required if check.status == "fail"]
    reasons = [f"{check.id}: {check.reason}" for check in required if check.status != "pass"]
    return Decision(
        outcome="fail" if failed else "incomplete" if missing else "pass",
        coverage="incomplete" if missing else "full",
        reasons=reasons,
        checks=checks,
    )


def decision_hash(receipt: Receipt) -> str:
    payload = receipt.model_dump(
        mode="json",
        exclude={
            "run_id",
            "started_at",
            "finished_at",
            "target",
            "decision_hash",
        },
    )
    for evidence in payload["evidence"]:
        for kind in ("execution", "version"):
            if evidence[kind] is not None:
                evidence[kind].pop("duration_ms")
    return canonical_hash(payload)


def replay(path: str | Path) -> Receipt:
    """Verify artifacts and recompute a decision without running any checks."""
    source = Path(path).resolve()
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("receipt exceeds the 32 MiB size limit")
    receipt = Receipt.model_validate_json(source.read_bytes())
    root = source.parent
    if receipt.policy_hash != policy_hash(receipt.policy):
        raise ValueError("policy hash mismatch")
    raw_policy = artifact_path(root, receipt.policy_artifact).read_bytes()
    if parse_policy(raw_policy) != receipt.policy:
        raise ValueError("saved policy differs from the policy artifact")
    for files in (receipt.before, receipt.after):
        paths = [item.path for item in files]
        if paths != sorted(set(paths)):
            raise ValueError("input manifests must contain unique, sorted paths")
    for item in receipt.evidence:
        for execution in (item.execution, item.version):
            if execution is None:
                continue
            for artifact in (execution.stdout, execution.stderr):
                if artifact is not None:
                    artifact_path(root, artifact)
    if derive_decision(receipt, root) != receipt.decision:
        raise ValueError("saved decision does not match the evidence")
    if decision_hash(receipt) != receipt.decision_hash:
        raise ValueError("decision hash mismatch")
    return receipt


def summary(receipt: Receipt, path: Path) -> dict[str, Any]:
    return {
        "outcome": receipt.decision.outcome,
        "coverage": receipt.decision.coverage,
        "checks": [check.model_dump() for check in receipt.decision.checks],
        "reasons": receipt.decision.reasons,
        "policy_hash": receipt.policy_hash,
        "decision_hash": receipt.decision_hash,
        "receipt": str(path),
    }
