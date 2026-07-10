"""Observation data model: ObservationDraft, Observation, Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from verdx.contracts.dimensions import Confidence, DimLevel, EscalationLevel, Severity
from verdx.contracts.independence import IndependenceRecord

OBSERVATION_SCHEMA_VERSION = "verdx.observation.v1"


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    kind: str
    details: str
    data: dict[str, Any]
    truncated: bool = False
    original_size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "kind": self.kind,
            "details": self.details,
            "data": self.data,
            "truncated": self.truncated,
        }
        if self.original_size is not None:
            result["original_size"] = self.original_size
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        return cls(
            source=data["source"],
            kind=data["kind"],
            details=data["details"],
            data=data["data"],
            truncated=data.get("truncated", False),
            original_size=data.get("original_size"),
        )


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    target_key: str
    location: str | None
    rule_id: str
    category: str
    severity: Severity
    confidence: Confidence
    dimensions: dict[str, DimLevel]
    summary: str
    subject_key: str | None = None
    evidence: tuple[Evidence, ...] = ()
    reproduction: tuple[str, ...] = ()
    invalidating_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.target_key:
            raise ValueError("target_key must be non-empty")
        if not self.rule_id:
            raise ValueError("rule_id must be non-empty")
        if not self.category:
            raise ValueError("category must be non-empty")
        if not self.summary:
            raise ValueError("summary must be non-empty")
        if not self.dimensions:
            raise ValueError("dimensions must contain at least one entry")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "target_key": self.target_key,
            "location": self.location,
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "dimensions": {k: v.value for k, v in self.dimensions.items()},
            "summary": self.summary,
            "evidence": [e.to_dict() for e in self.evidence],
            "reproduction": list(self.reproduction),
            "invalidating_conditions": list(self.invalidating_conditions),
        }
        if self.subject_key is not None:
            result["subject_key"] = self.subject_key
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationDraft:
        return cls(
            target_key=data["target_key"],
            location=data.get("location"),
            rule_id=data["rule_id"],
            category=data["category"],
            severity=Severity(data["severity"]),
            confidence=Confidence(data["confidence"]),
            dimensions={k: DimLevel(v) for k, v in data["dimensions"].items()},
            summary=data["summary"],
            subject_key=data.get("subject_key"),
            evidence=tuple(Evidence.from_dict(e) for e in data.get("evidence", [])),
            reproduction=tuple(data.get("reproduction", [])),
            invalidating_conditions=tuple(data.get("invalidating_conditions", [])),
        )


@dataclass(frozen=True, slots=True)
class AppliedDisposition:
    decision: str
    reason: str
    team: str
    timestamp: str
    expires: str | None
    effect: str
    original_escalation: EscalationLevel
    final_escalation: EscalationLevel

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
            "team": self.team,
            "timestamp": self.timestamp,
            "effect": self.effect,
            "original_escalation": self.original_escalation.value,
            "final_escalation": self.final_escalation.value,
        }
        if self.expires is not None:
            result["expires"] = self.expires
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppliedDisposition:
        return cls(
            decision=data["decision"],
            reason=data["reason"],
            team=data["team"],
            timestamp=data["timestamp"],
            expires=data.get("expires"),
            effect=data["effect"],
            original_escalation=EscalationLevel(data["original_escalation"]),
            final_escalation=EscalationLevel(data["final_escalation"]),
        )


@dataclass(frozen=True, slots=True)
class Observation:
    schema_version: str
    observation_id: str
    issue_fingerprint: str
    target_key: str
    location: str | None
    rule_id: str
    producer_role: str
    producer_id: str | None
    validator_id: str
    validator_version: str
    independence: IndependenceRecord
    category: str
    severity: Severity
    confidence: Confidence
    dimensions: dict[str, DimLevel]
    escalation: EscalationLevel
    summary: str
    subject_key: str | None = None
    evidence: tuple[Evidence, ...] = ()
    reproduction: tuple[str, ...] = ()
    invalidating_conditions: tuple[str, ...] = ()
    disposition: AppliedDisposition | None = None

    def __post_init__(self) -> None:
        if self.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {OBSERVATION_SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        if not self.observation_id:
            raise ValueError("observation_id must be non-empty")
        if not self.issue_fingerprint:
            raise ValueError("issue_fingerprint must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "issue_fingerprint": self.issue_fingerprint,
            "target_key": self.target_key,
            "location": self.location,
            "rule_id": self.rule_id,
            "producer_role": self.producer_role,
            "producer_id": self.producer_id,
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "independence": self.independence.to_dict(),
            "category": self.category,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "dimensions": {k: v.value for k, v in self.dimensions.items()},
            "escalation": self.escalation.value,
            "summary": self.summary,
            "evidence": [e.to_dict() for e in self.evidence],
            "reproduction": list(self.reproduction),
            "invalidating_conditions": list(self.invalidating_conditions),
        }
        if self.subject_key is not None:
            result["subject_key"] = self.subject_key
        if self.disposition is not None:
            result["disposition"] = self.disposition.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        disposition = None
        if "disposition" in data and data["disposition"] is not None:
            disposition = AppliedDisposition.from_dict(data["disposition"])
        return cls(
            schema_version=data["schema_version"],
            observation_id=data["observation_id"],
            issue_fingerprint=data["issue_fingerprint"],
            target_key=data["target_key"],
            location=data.get("location"),
            rule_id=data["rule_id"],
            producer_role=data["producer_role"],
            producer_id=data.get("producer_id"),
            validator_id=data["validator_id"],
            validator_version=data["validator_version"],
            independence=IndependenceRecord.from_dict(data["independence"]),
            category=data["category"],
            severity=Severity(data["severity"]),
            confidence=Confidence(data["confidence"]),
            dimensions={k: DimLevel(v) for k, v in data["dimensions"].items()},
            escalation=EscalationLevel(data["escalation"]),
            summary=data["summary"],
            subject_key=data.get("subject_key"),
            evidence=tuple(Evidence.from_dict(e) for e in data.get("evidence", [])),
            reproduction=tuple(data.get("reproduction", [])),
            invalidating_conditions=tuple(data.get("invalidating_conditions", [])),
            disposition=disposition,
        )
