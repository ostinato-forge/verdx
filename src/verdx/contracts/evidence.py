"""GateEvidence — compact agent-facing projection of RunAssessment.

``--format agent`` produces this ~500-token JSON.  Skills parse it,
check ``schema_version``, and obey exit semantics.  Full RunAssessment
stays in ``--format json`` and report bundles.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from verdx.contracts.assessment import AssessmentConfidence, CoverageGap
from verdx.contracts.dimensions import CoverageStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

EVIDENCE_SCHEMA_VERSION: Literal["verdx.evidence.v1"] = "verdx.evidence.v1"


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """Aggregate counts for the agent view."""

    checks_run: int
    checks_passed: int
    validators_run: int
    validators_clean: int
    by_escalation: Mapping[str, int]
    blocking_findings: int
    awareness_items: int
    domains: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "validators_run": self.validators_run,
            "validators_clean": self.validators_clean,
            "by_escalation": dict(self.by_escalation),
            "blocking_findings": self.blocking_findings,
            "awareness_items": self.awareness_items,
            "domains": list(self.domains),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceSummary:
        return cls(
            checks_run=data["checks_run"],
            checks_passed=data["checks_passed"],
            validators_run=data["validators_run"],
            validators_clean=data["validators_clean"],
            by_escalation=dict(data["by_escalation"]),
            blocking_findings=data["blocking_findings"],
            awareness_items=data["awareness_items"],
            domains=tuple(data.get("domains", ())),
        )


@dataclass(frozen=True, slots=True)
class ActionItem:
    """A finding that requires action (L3/L4)."""

    rule: str
    severity: str
    escalation: str
    summary: str
    location: str | None
    count: int

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rule": self.rule,
            "severity": self.severity,
            "escalation": self.escalation,
            "summary": self.summary,
            "count": self.count,
        }
        if self.location is not None:
            result["location"] = self.location
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionItem:
        return cls(
            rule=data["rule"],
            severity=data["severity"],
            escalation=data["escalation"],
            summary=data["summary"],
            location=data.get("location"),
            count=data["count"],
        )


@dataclass(frozen=True, slots=True)
class AwarenessItem:
    """A finding for awareness only (L0-L2)."""

    rule: str
    severity: str
    escalation: str
    count: int
    example: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "escalation": self.escalation,
            "count": self.count,
            "example": self.example,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AwarenessItem:
        return cls(
            rule=data["rule"],
            severity=data["severity"],
            escalation=data["escalation"],
            count=data["count"],
            example=data["example"],
        )


@dataclass(frozen=True, slots=True)
class RecoveryProjection:
    """Compact recovery item for agent output — excludes raw context to prevent secret leaks."""

    id: str
    kind: str
    severity: str
    description: str
    validator_id: str
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "description": self.description,
            "validator_id": self.validator_id,
        }
        if self.code:
            result["code"] = self.code
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryProjection:
        return cls(
            id=data["id"],
            kind=data["kind"],
            severity=data["severity"],
            description=data["description"],
            validator_id=data["validator_id"],
            code=data.get("code", ""),
        )


@dataclass(frozen=True, slots=True)
class EvidenceTruncation:
    """Tracks what was omitted to stay within the token budget."""

    truncated: bool
    estimated_tokens: int
    hard_max_tokens: int = 1000
    omitted_action_groups: int = 0
    omitted_awareness_groups: int = 0
    omitted_observations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "truncated": self.truncated,
            "estimated_tokens": self.estimated_tokens,
            "hard_max_tokens": self.hard_max_tokens,
            "omitted_action_groups": self.omitted_action_groups,
            "omitted_awareness_groups": self.omitted_awareness_groups,
            "omitted_observations": self.omitted_observations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceTruncation:
        return cls(
            truncated=data["truncated"],
            estimated_tokens=data["estimated_tokens"],
            hard_max_tokens=data.get("hard_max_tokens", 1000),
            omitted_action_groups=data.get("omitted_action_groups", 0),
            omitted_awareness_groups=data.get("omitted_awareness_groups", 0),
            omitted_observations=data.get("omitted_observations", 0),
        )


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """Agent-facing evidence projection.  Target ~500 tokens, hard cap 1000."""

    schema_version: Literal["verdx.evidence.v1"] = EVIDENCE_SCHEMA_VERSION
    run_id: str = ""
    target_key: str = ""
    domain: str | None = None
    score: int = 0
    threshold: int = 80
    passed: bool = False
    coverage_ok: bool = True
    confidence_ok: bool = True
    coverage: CoverageStatus = CoverageStatus.FULL
    confidence: AssessmentConfidence = AssessmentConfidence.STRONG
    summary: EvidenceSummary = field(
        default_factory=lambda: EvidenceSummary(
            checks_run=0,
            checks_passed=0,
            validators_run=0,
            validators_clean=0,
            by_escalation={},
            blocking_findings=0,
            awareness_items=0,
        )
    )
    action_required: tuple[ActionItem, ...] = ()
    awareness: tuple[AwarenessItem, ...] = ()
    recovery: tuple[RecoveryProjection, ...] = ()
    coverage_gaps: tuple[CoverageGap, ...] = ()
    truncation: EvidenceTruncation | None = None
    drill_down_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "target_key": self.target_key,
            "domain": self.domain,
            "score": self.score,
            "threshold": self.threshold,
            "passed": self.passed,
            "coverage_ok": self.coverage_ok,
            "confidence_ok": self.confidence_ok,
            "coverage": self.coverage.value,
            "confidence": self.confidence.value,
            "summary": self.summary.to_dict(),
            "action_required": [a.to_dict() for a in self.action_required],
            "awareness": [a.to_dict() for a in self.awareness],
            "recovery": [r.to_dict() for r in self.recovery],
            "coverage_gaps": [g.to_dict() for g in self.coverage_gaps],
            "drill_down_available": self.drill_down_available,
        }
        if self.truncation is not None:
            result["truncation"] = self.truncation.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateEvidence:
        truncation = None
        if "truncation" in data and data["truncation"] is not None:
            truncation = EvidenceTruncation.from_dict(data["truncation"])
        return cls(
            schema_version=data.get("schema_version", EVIDENCE_SCHEMA_VERSION),
            run_id=data["run_id"],
            target_key=data["target_key"],
            domain=data.get("domain"),
            score=data["score"],
            threshold=data["threshold"],
            passed=data["passed"],
            coverage_ok=data.get("coverage_ok", True),
            confidence_ok=data.get("confidence_ok", True),
            coverage=CoverageStatus(data["coverage"]),
            confidence=AssessmentConfidence(data["confidence"]),
            summary=EvidenceSummary.from_dict(data["summary"]),
            action_required=tuple(ActionItem.from_dict(a) for a in data.get("action_required", ())),
            awareness=tuple(AwarenessItem.from_dict(a) for a in data.get("awareness", ())),
            recovery=tuple(RecoveryProjection.from_dict(r) for r in data.get("recovery", ())),
            coverage_gaps=tuple(CoverageGap.from_dict(g) for g in data.get("coverage_gaps", ())),
            truncation=truncation,
            drill_down_available=data.get("drill_down_available", True),
        )
