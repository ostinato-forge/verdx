"""Assessment models: RunAssessment and supporting types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.contracts.observation import Observation
from verdx.contracts.subjects import SubjectCoverageRecord

ASSESSMENT_SCHEMA_VERSION = "verdx.assessment.v1"


class AssessmentConfidence(Enum):
    STRONG = "strong"
    GUARDED = "guarded"
    LOW = "low"


class Trend(Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    REGRESSING = "regressing"


@dataclass(frozen=True, slots=True)
class ValidatorStatus:
    validator_id: str
    validator_version: str
    status: str
    checks_run: int
    checks_passed: int
    observation_count: int
    duration_ms: int
    error: str | None = None
    skip_reason: str | None = None

    VALID_STATUSES = frozenset({"completed", "failed", "skipped"})

    def __post_init__(self) -> None:
        if not self.validator_id:
            raise ValueError("validator_id must be non-empty")
        if not self.validator_version:
            raise ValueError("validator_version must be non-empty")
        if self.status not in self.VALID_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(self.VALID_STATUSES)}, got {self.status!r}"
            )
        if self.checks_run < 0:
            raise ValueError("checks_run must be non-negative")
        if self.checks_passed < 0:
            raise ValueError("checks_passed must be non-negative")
        if self.checks_passed > self.checks_run:
            raise ValueError("checks_passed cannot exceed checks_run")
        if self.observation_count < 0:
            raise ValueError("observation_count must be non-negative")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "status": self.status,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "observation_count": self.observation_count,
            "duration_ms": self.duration_ms,
        }
        if self.error is not None:
            result["error"] = self.error
        if self.skip_reason is not None:
            result["skip_reason"] = self.skip_reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidatorStatus:
        return cls(
            validator_id=data["validator_id"],
            validator_version=data["validator_version"],
            status=data["status"],
            checks_run=data["checks_run"],
            checks_passed=data["checks_passed"],
            observation_count=data["observation_count"],
            duration_ms=data["duration_ms"],
            error=data.get("error"),
            skip_reason=data.get("skip_reason"),
        )


@dataclass(frozen=True, slots=True)
class CoverageGap:
    validator_id: str
    reason: str
    impact: str
    dimensions_affected: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.validator_id:
            raise ValueError("validator_id must be non-empty")
        if not self.reason:
            raise ValueError("reason must be non-empty")
        if not self.impact:
            raise ValueError("impact must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_id": self.validator_id,
            "reason": self.reason,
            "impact": self.impact,
            "dimensions_affected": list(self.dimensions_affected),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageGap:
        return cls(
            validator_id=data["validator_id"],
            reason=data["reason"],
            impact=data["impact"],
            dimensions_affected=tuple(data["dimensions_affected"]),
        )


@dataclass(frozen=True, slots=True)
class RecoveryItem:
    id: str
    kind: str
    description: str
    validator_id: str
    context: dict[str, Any]
    code: str = ""
    severity: str = "degraded"

    VALID_KINDS = frozenset(
        {"blocked", "retryable", "cleanup-needed", "discovery-failed", "degraded"}
    )
    VALID_SEVERITIES = frozenset({"degraded", "blocking"})

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("id must be non-empty")
        if self.kind not in self.VALID_KINDS:
            raise ValueError(f"kind must be one of {sorted(self.VALID_KINDS)}, got {self.kind!r}")
        if not self.description:
            raise ValueError("description must be non-empty")
        if not self.validator_id:
            raise ValueError("validator_id must be non-empty")
        if self.severity not in self.VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(self.VALID_SEVERITIES)}, got {self.severity!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "validator_id": self.validator_id,
            "context": self.context,
            "severity": self.severity,
        }
        if self.code:
            result["code"] = self.code
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryItem:
        return cls(
            id=data["id"],
            kind=data["kind"],
            description=data["description"],
            validator_id=data["validator_id"],
            context=data["context"],
            code=data.get("code", ""),
            severity=data.get("severity", "degraded"),
        )


@dataclass(frozen=True, slots=True)
class ImprovementSummary:
    resolved_count: int
    new_count: int
    persistent_count: int
    resolved_fingerprints: tuple[str, ...]
    trend: Trend
    new_fingerprints: tuple[str, ...] = ()
    persistent_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.resolved_count < 0:
            raise ValueError("resolved_count must be non-negative")
        if self.new_count < 0:
            raise ValueError("new_count must be non-negative")
        if self.persistent_count < 0:
            raise ValueError("persistent_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved_count": self.resolved_count,
            "new_count": self.new_count,
            "persistent_count": self.persistent_count,
            "resolved_fingerprints": list(self.resolved_fingerprints),
            "new_fingerprints": list(self.new_fingerprints),
            "persistent_fingerprints": list(self.persistent_fingerprints),
            "trend": self.trend.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementSummary:
        return cls(
            resolved_count=data["resolved_count"],
            new_count=data["new_count"],
            persistent_count=data["persistent_count"],
            resolved_fingerprints=tuple(data["resolved_fingerprints"]),
            new_fingerprints=tuple(data.get("new_fingerprints", ())),
            persistent_fingerprints=tuple(data.get("persistent_fingerprints", ())),
            trend=Trend(data["trend"]),
        )


@dataclass(frozen=True, slots=True)
class DimensionScore:
    dimension: str
    score: int
    observation_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": self.score,
            "observation_count": self.observation_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionScore:
        return cls(
            dimension=data["dimension"],
            score=data["score"],
            observation_count=data["observation_count"],
        )


@dataclass(frozen=True, slots=True)
class ScoreComparison:
    previous_score: int
    delta: int

    def to_dict(self) -> dict[str, int]:
        return {
            "previous_score": self.previous_score,
            "delta": self.delta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoreComparison:
        return cls(
            previous_score=data["previous_score"],
            delta=data["delta"],
        )


@dataclass(frozen=True, slots=True)
class Score:
    overall: int
    dimension_scores: dict[str, DimensionScore]
    observation_count: int
    escalation_distribution: dict[EscalationLevel, int]
    coverage_penalty: int
    independence_penalty: int = 0
    comparison: ScoreComparison | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.overall <= 100:
            raise ValueError("overall must be between 0 and 100")
        if self.coverage_penalty < 0:
            raise ValueError("coverage_penalty must be non-negative")
        if self.independence_penalty < 0:
            raise ValueError("independence_penalty must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "overall": self.overall,
            "dimension_scores": {k: v.to_dict() for k, v in self.dimension_scores.items()},
            "observation_count": self.observation_count,
            "escalation_distribution": {
                k.value: v for k, v in self.escalation_distribution.items()
            },
            "coverage_penalty": self.coverage_penalty,
            "independence_penalty": self.independence_penalty,
        }
        if self.comparison is not None:
            result["comparison"] = self.comparison.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Score:
        comparison = None
        if "comparison" in data and data["comparison"] is not None:
            comparison = ScoreComparison.from_dict(data["comparison"])
        return cls(
            overall=data["overall"],
            dimension_scores={
                k: DimensionScore.from_dict(v) for k, v in data["dimension_scores"].items()
            },
            observation_count=data["observation_count"],
            escalation_distribution={
                EscalationLevel(k): v for k, v in data["escalation_distribution"].items()
            },
            coverage_penalty=data["coverage_penalty"],
            independence_penalty=data.get("independence_penalty", 0),
            comparison=comparison,
        )


@dataclass(frozen=True, slots=True)
class EscalationSummary:
    counts: dict[EscalationLevel, int]
    highest: EscalationLevel

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": {k.value: v for k, v in self.counts.items()},
            "highest": self.highest.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationSummary:
        return cls(
            counts={EscalationLevel(k): v for k, v in data["counts"].items()},
            highest=EscalationLevel(data["highest"]),
        )


@dataclass(frozen=True, slots=True)
class RunAssessment:
    schema_version: str
    run_id: str
    target_key: str
    target_content_hash: str
    timestamp: str
    coverage_status: CoverageStatus
    validator_statuses: tuple[ValidatorStatus, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    checks_run: int
    checks_passed: int
    validators_clean: int
    observations: tuple[Observation, ...]
    recovery_items: tuple[RecoveryItem, ...]
    confidence: AssessmentConfidence
    score: Score
    escalation_summary: EscalationSummary
    improvement: ImprovementSummary | None = None
    subject_coverage: tuple[SubjectCoverageRecord, ...] = ()
    domain: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != ASSESSMENT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {ASSESSMENT_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.target_key:
            raise ValueError("target_key must be non-empty")
        if not self.target_content_hash:
            raise ValueError("target_content_hash must be non-empty")
        if not self.timestamp:
            raise ValueError("timestamp must be non-empty")
        if self.checks_run < 0:
            raise ValueError("checks_run must be non-negative")
        if self.checks_passed < 0:
            raise ValueError("checks_passed must be non-negative")
        if self.validators_clean < 0:
            raise ValueError("validators_clean must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "target_key": self.target_key,
            "target_content_hash": self.target_content_hash,
            "timestamp": self.timestamp,
            "coverage_status": self.coverage_status.value,
            "validator_statuses": [vs.to_dict() for vs in self.validator_statuses],
            "coverage_gaps": [cg.to_dict() for cg in self.coverage_gaps],
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "validators_clean": self.validators_clean,
            "observations": [o.to_dict() for o in self.observations],
            "recovery_items": [ri.to_dict() for ri in self.recovery_items],
            "confidence": self.confidence.value,
            "score": self.score.to_dict(),
            "escalation_summary": self.escalation_summary.to_dict(),
        }
        if self.subject_coverage:
            result["subject_coverage"] = [record.to_dict() for record in self.subject_coverage]
        if self.improvement is not None:
            result["improvement"] = self.improvement.to_dict()
        if self.domain:
            result["domain"] = self.domain
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunAssessment:
        improvement = None
        if "improvement" in data and data["improvement"] is not None:
            improvement = ImprovementSummary.from_dict(data["improvement"])
        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            target_key=data["target_key"],
            target_content_hash=data["target_content_hash"],
            timestamp=data["timestamp"],
            coverage_status=CoverageStatus(data["coverage_status"]),
            validator_statuses=tuple(
                ValidatorStatus.from_dict(vs) for vs in data["validator_statuses"]
            ),
            coverage_gaps=tuple(CoverageGap.from_dict(cg) for cg in data["coverage_gaps"]),
            checks_run=data["checks_run"],
            checks_passed=data["checks_passed"],
            validators_clean=data["validators_clean"],
            observations=tuple(Observation.from_dict(o) for o in data["observations"]),
            recovery_items=tuple(RecoveryItem.from_dict(ri) for ri in data["recovery_items"]),
            confidence=AssessmentConfidence(data["confidence"]),
            score=Score.from_dict(data["score"]),
            escalation_summary=EscalationSummary.from_dict(data["escalation_summary"]),
            improvement=improvement,
            subject_coverage=tuple(
                SubjectCoverageRecord.from_dict(record)
                for record in data.get("subject_coverage", ())
            ),
            domain=data.get("domain", ""),
        )
