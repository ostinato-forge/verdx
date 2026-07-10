"""Readiness and promotion records: typed control-loop from coverage to judgment.

Pure core module: no filesystem, network, time, subprocess, or random input.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus
from verdx.contracts.subjects import SubjectCoverageRecord, SubjectCoverageState

READINESS_SCHEMA_VERSION = "verdx.readiness.v1"

_DEBT_STATES = frozenset(
    {
        SubjectCoverageState.PARTIAL,
        SubjectCoverageState.FAILED,
        SubjectCoverageState.SKIPPED,
        SubjectCoverageState.MISSING,
        SubjectCoverageState.UNSUPPORTED,
        SubjectCoverageState.MODEL_LIMITED,
    }
)

_BLOCKING_BLOCKER_KINDS = frozenset(
    {
        "critical_subject_debt",
        "gate_failed",
        "degraded_coverage",
    }
)


class ReadinessState(Enum):
    READY = "ready"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class ReadinessBlocker:
    kind: str
    description: str
    subject_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "description": self.description,
        }
        if self.subject_keys:
            result["subject_keys"] = list(self.subject_keys)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReadinessBlocker:
        return cls(
            kind=data["kind"],
            description=data["description"],
            subject_keys=tuple(data.get("subject_keys", ())),
        )


@dataclass(frozen=True, slots=True)
class ReadinessRecord:
    schema_version: str
    target_key: str
    run_id: str
    state: ReadinessState
    score: int
    threshold: int | None
    coverage_status: CoverageStatus
    confidence: AssessmentConfidence
    blockers: tuple[ReadinessBlocker, ...]
    debt_summary: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        if self.schema_version != READINESS_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {READINESS_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if not 0 <= self.score <= 100:
            raise ValueError("score must be between 0 and 100")
        if not self.target_key:
            raise ValueError("target_key must be non-empty")
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.explanation:
            raise ValueError("explanation must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "target_key": self.target_key,
            "run_id": self.run_id,
            "state": self.state.value,
            "score": self.score,
            "coverage_status": self.coverage_status.value,
            "confidence": self.confidence.value,
            "blockers": [b.to_dict() for b in self.blockers],
            "debt_summary": list(self.debt_summary),
            "explanation": self.explanation,
        }
        if self.threshold is not None:
            result["threshold"] = self.threshold
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReadinessRecord:
        return cls(
            schema_version=data["schema_version"],
            target_key=data["target_key"],
            run_id=data["run_id"],
            state=ReadinessState(data["state"]),
            score=data["score"],
            threshold=data.get("threshold"),
            coverage_status=CoverageStatus(data["coverage_status"]),
            confidence=AssessmentConfidence(data["confidence"]),
            blockers=tuple(ReadinessBlocker.from_dict(b) for b in data.get("blockers", ())),
            debt_summary=tuple(data.get("debt_summary", ())),
            explanation=data["explanation"],
        )


def derive_readiness(
    *,
    score: int,
    threshold: int | None,
    coverage_status: CoverageStatus,
    confidence: AssessmentConfidence,
    gate_passed: bool,
    gate_reason: str,
    subject_coverage: tuple[SubjectCoverageRecord, ...],
    target_key: str,
    run_id: str,
) -> ReadinessRecord:
    """Derive a readiness record from assessment and coverage state.

    Pure function: no I/O, deterministic output for the same inputs.
    """
    blockers: list[ReadinessBlocker] = []
    debt_summary: list[str] = []

    _collect_subject_blockers(subject_coverage, blockers, debt_summary)

    if not gate_passed:
        blockers.append(
            ReadinessBlocker(
                kind="gate_failed",
                description=f"Gate did not pass: {gate_reason}",
            )
        )

    if coverage_status is CoverageStatus.DEGRADED:
        blockers.append(
            ReadinessBlocker(
                kind="degraded_coverage",
                description="Coverage is degraded; validation results are incomplete",
            )
        )

    if confidence is AssessmentConfidence.LOW:
        blockers.append(
            ReadinessBlocker(
                kind="low_confidence",
                description="Assessment confidence is low; human review recommended",
            )
        )

    state = _determine_state(blockers, coverage_status)
    explanation = _build_explanation(state, score, threshold, blockers, debt_summary)

    return ReadinessRecord(
        schema_version=READINESS_SCHEMA_VERSION,
        target_key=target_key,
        run_id=run_id,
        state=state,
        score=score,
        threshold=threshold,
        coverage_status=coverage_status,
        confidence=confidence,
        blockers=tuple(blockers),
        debt_summary=tuple(debt_summary),
        explanation=explanation,
    )


def _collect_subject_blockers(
    subject_coverage: tuple[SubjectCoverageRecord, ...],
    blockers: list[ReadinessBlocker],
    debt_summary: list[str],
) -> None:
    critical_debt_keys: list[str] = []
    for record in subject_coverage:
        if record.state not in _DEBT_STATES:
            continue
        label = record.subject.label
        state_label = record.state.value.replace("_", " ")
        if record.criticality == "critical":
            critical_debt_keys.append(record.subject.subject_key)
        else:
            debt_summary.append(
                f"{record.criticality} subject {label!r} "
                f"({record.subject.subject_key}): {state_label}"
            )

    if critical_debt_keys:
        blockers.append(
            ReadinessBlocker(
                kind="critical_subject_debt",
                description=(f"{len(critical_debt_keys)} critical subject(s) have coverage debt"),
                subject_keys=tuple(critical_debt_keys),
            )
        )


def _determine_state(
    blockers: list[ReadinessBlocker],
    coverage_status: CoverageStatus,
) -> ReadinessState:
    if any(b.kind in _BLOCKING_BLOCKER_KINDS for b in blockers):
        return ReadinessState.BLOCKED
    if any(b.kind == "low_confidence" for b in blockers):
        return ReadinessState.NEEDS_REVIEW
    if coverage_status is CoverageStatus.PARTIAL:
        return ReadinessState.NEEDS_REVIEW
    return ReadinessState.READY


def _build_explanation(
    state: ReadinessState,
    score: int,
    threshold: int | None,
    blockers: list[ReadinessBlocker],
    debt_summary: list[str],
) -> str:
    if state is ReadinessState.READY:
        parts = [f"Score {score}"]
        if threshold is not None:
            parts[0] += f" (threshold {threshold})"
        if debt_summary:
            parts.append(f"{len(debt_summary)} non-critical debt item(s) noted")
        return "; ".join(parts) + " -- ready to advance"

    if state is ReadinessState.BLOCKED:
        reasons = [b.description for b in blockers if b.kind in _BLOCKING_BLOCKER_KINDS]
        return f"Blocked: {'; '.join(reasons)}"

    reasons = []
    if any(b.kind == "low_confidence" for b in blockers):
        reasons.append("low confidence")
    reasons.append("needs human review")
    return f"Score {score} -- {', '.join(reasons)}"
