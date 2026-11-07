"""Multi-domain composite assessment contracts.

When verdx validates against multiple domains, each produces its
own RunAssessment.  CompositeAssessment wraps them with a unified gate
decision.  Default strategy is all-pass — never weighted-average as gate.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from verdx.contracts.assessment import AssessmentConfidence, RunAssessment
from verdx.contracts.dimensions import CoverageStatus

COMPOSITE_ASSESSMENT_SCHEMA_VERSION: Literal["verdx.composite-assessment.v1"] = (
    "verdx.composite-assessment.v1"
)


@dataclass(frozen=True, slots=True)
class DomainDecision:
    """Per-domain gate decision within a composite assessment."""

    domain: str
    required: bool
    threshold: int
    score: int
    coverage_status: CoverageStatus
    confidence: AssessmentConfidence
    passed: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "domain": self.domain,
            "required": self.required,
            "threshold": self.threshold,
            "score": self.score,
            "coverage_status": self.coverage_status.value,
            "confidence": self.confidence.value,
            "passed": self.passed,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainDecision:
        return cls(
            domain=data["domain"],
            required=data["required"],
            threshold=data["threshold"],
            score=data["score"],
            coverage_status=CoverageStatus(data["coverage_status"]),
            confidence=AssessmentConfidence(data["confidence"]),
            passed=data["passed"],
            reason=data.get("reason"),
        )


@dataclass(frozen=True, slots=True)
class CompositeGateDecision:
    """Aggregate gate decision across all domains."""

    strategy: Literal["all-pass", "minimum", "weighted-average"] = "all-pass"
    passed: bool = False
    score: int = 0
    required_threshold: int = 80
    failed_domains: tuple[str, ...] = ()
    requires_human: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "strategy": self.strategy,
            "passed": self.passed,
            "score": self.score,
            "required_threshold": self.required_threshold,
            "failed_domains": list(self.failed_domains),
            "requires_human": self.requires_human,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositeGateDecision:
        return cls(
            strategy=data.get("strategy", "all-pass"),
            passed=data["passed"],
            score=data["score"],
            required_threshold=data.get("required_threshold", 80),
            failed_domains=tuple(data.get("failed_domains", ())),
            requires_human=data.get("requires_human", False),
            reason=data.get("reason"),
        )


@dataclass(frozen=True, slots=True)
class CompositeAssessment:
    """Multi-domain assessment with per-domain results and aggregate decision."""

    schema_version: Literal["verdx.composite-assessment.v1"] = COMPOSITE_ASSESSMENT_SCHEMA_VERSION
    run_id: str = ""
    target_key: str = ""
    target_content_hash: str = ""
    timestamp: str = ""
    domains: dict[str, RunAssessment] = field(default_factory=dict)
    domain_decisions: tuple[DomainDecision, ...] = ()
    decision: CompositeGateDecision = field(default_factory=CompositeGateDecision)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.target_key:
            raise ValueError("target_key must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "target_key": self.target_key,
            "target_content_hash": self.target_content_hash,
            "timestamp": self.timestamp,
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "domain_decisions": [d.to_dict() for d in self.domain_decisions],
            "decision": self.decision.to_dict(),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositeAssessment:
        return cls(
            schema_version=data.get("schema_version", COMPOSITE_ASSESSMENT_SCHEMA_VERSION),
            run_id=data["run_id"],
            target_key=data["target_key"],
            target_content_hash=data.get("target_content_hash", ""),
            timestamp=data.get("timestamp", ""),
            domains={k: RunAssessment.from_dict(v) for k, v in data.get("domains", {}).items()},
            domain_decisions=tuple(
                DomainDecision.from_dict(d) for d in data.get("domain_decisions", ())
            ),
            decision=CompositeGateDecision.from_dict(data["decision"]),
            warnings=tuple(data.get("warnings", ())),
        )
