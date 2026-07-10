"""Audit contract types: approval records, timelines, compliance summaries."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """A single approval-related event in the audit trail."""

    intent_id: str
    event_type: str
    gate_state: str
    actor: str
    authority_role: str
    timestamp: str  # ISO 8601
    artifact_refs: tuple[str, ...] = ()
    detail: str | None = None
    reason_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "intent_id": self.intent_id,
            "event_type": self.event_type,
            "gate_state": self.gate_state,
            "actor": self.actor,
            "authority_role": self.authority_role,
            "timestamp": self.timestamp,
        }
        if self.artifact_refs:
            result["artifact_refs"] = list(self.artifact_refs)
        if self.detail is not None:
            result["detail"] = self.detail
        if self.reason_tags:
            result["reason_tags"] = list(self.reason_tags)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRecord":
        return cls(
            intent_id=data["intent_id"],
            event_type=data["event_type"],
            gate_state=data["gate_state"],
            actor=data["actor"],
            authority_role=data["authority_role"],
            timestamp=data["timestamp"],
            artifact_refs=tuple(data.get("artifact_refs", ())),
            detail=data.get("detail"),
            reason_tags=tuple(data.get("reason_tags", ())),
        )


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """A single event projected onto a timeline."""

    index: int
    intent_id: str
    event_type: str
    previous_state: str | None
    next_state: str
    actor: str
    authority_role: str
    timestamp: str  # ISO 8601
    elapsed_seconds: float | None = None
    detail: str | None = None
    artifact_refs_created: tuple[str, ...] = ()
    reason_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": self.index,
            "intent_id": self.intent_id,
            "event_type": self.event_type,
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "actor": self.actor,
            "authority_role": self.authority_role,
            "timestamp": self.timestamp,
        }
        if self.elapsed_seconds is not None:
            result["elapsed_seconds"] = self.elapsed_seconds
        if self.detail is not None:
            result["detail"] = self.detail
        if self.artifact_refs_created:
            result["artifact_refs_created"] = list(self.artifact_refs_created)
        if self.reason_tags:
            result["reason_tags"] = list(self.reason_tags)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimelineEntry":
        return cls(
            index=data["index"],
            intent_id=data["intent_id"],
            event_type=data["event_type"],
            previous_state=data.get("previous_state"),
            next_state=data["next_state"],
            actor=data["actor"],
            authority_role=data["authority_role"],
            timestamp=data["timestamp"],
            elapsed_seconds=data.get("elapsed_seconds"),
            detail=data.get("detail"),
            artifact_refs_created=tuple(data.get("artifact_refs_created", ())),
            reason_tags=tuple(data.get("reason_tags", ())),
        )


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """An artifact reference observed during audit."""

    ref: str
    created_by_actor: str
    created_at: str  # ISO 8601
    created_at_event_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "created_by_actor": self.created_by_actor,
            "created_at": self.created_at,
            "created_at_event_index": self.created_at_event_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactRef":
        return cls(
            ref=data["ref"],
            created_by_actor=data["created_by_actor"],
            created_at=data["created_at"],
            created_at_event_index=data["created_at_event_index"],
        )


@dataclass(frozen=True, slots=True)
class ComplianceSummary:
    """Aggregate compliance summary for an intent."""

    intent_id: str
    final_state: str | None
    total_events: int
    gates_satisfied: int
    gates_total: int
    violations: tuple[str, ...]
    rework_count: int
    actors_involved: tuple[str, ...]
    roles_involved: tuple[str, ...]
    approval_chain: tuple[ApprovalRecord, ...]
    cycle_time_seconds: float | None
    total_elapsed_seconds: float
    total_attention_minutes: float
    total_wait_minutes: float
    reason_tag_counts: dict[str, int]
    started_as_fast_path: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "intent_id": self.intent_id,
            "final_state": self.final_state,
            "total_events": self.total_events,
            "gates_satisfied": self.gates_satisfied,
            "gates_total": self.gates_total,
            "violations": list(self.violations),
            "rework_count": self.rework_count,
            "actors_involved": list(self.actors_involved),
            "roles_involved": list(self.roles_involved),
            "approval_chain": [r.to_dict() for r in self.approval_chain],
            "cycle_time_seconds": self.cycle_time_seconds,
            "total_elapsed_seconds": self.total_elapsed_seconds,
            "total_attention_minutes": self.total_attention_minutes,
            "total_wait_minutes": self.total_wait_minutes,
            "reason_tag_counts": dict(self.reason_tag_counts),
            "started_as_fast_path": self.started_as_fast_path,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComplianceSummary":
        return cls(
            intent_id=data["intent_id"],
            final_state=data.get("final_state"),
            total_events=data["total_events"],
            gates_satisfied=data["gates_satisfied"],
            gates_total=data["gates_total"],
            violations=tuple(data.get("violations", ())),
            rework_count=data["rework_count"],
            actors_involved=tuple(data.get("actors_involved", ())),
            roles_involved=tuple(data.get("roles_involved", ())),
            approval_chain=tuple(
                ApprovalRecord.from_dict(r) for r in data.get("approval_chain", ())
            ),
            cycle_time_seconds=data.get("cycle_time_seconds"),
            total_elapsed_seconds=data["total_elapsed_seconds"],
            total_attention_minutes=data["total_attention_minutes"],
            total_wait_minutes=data["total_wait_minutes"],
            reason_tag_counts=dict(data.get("reason_tag_counts", {})),
            started_as_fast_path=data.get("started_as_fast_path", False),
        )
