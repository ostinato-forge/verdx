"""Disposition model: team decisions on settled observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DISPOSITION_SCHEMA_VERSION = "verdx.disposition.v1"

VALID_DECISIONS = frozenset({"accepted-risk", "false-positive", "deferred", "context-dependent"})


@dataclass(frozen=True, slots=True)
class Disposition:
    schema_version: str
    issue_fingerprint: str
    decision: str
    reason: str
    team: str
    timestamp: str
    expires: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != DISPOSITION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {DISPOSITION_SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        if not self.issue_fingerprint:
            raise ValueError("issue_fingerprint must be non-empty")
        if self.decision not in VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(VALID_DECISIONS)}, got {self.decision!r}"
            )
        if not self.reason:
            raise ValueError("reason must be non-empty")
        if not self.team:
            raise ValueError("team must be non-empty")
        if not self.timestamp:
            raise ValueError("timestamp must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "issue_fingerprint": self.issue_fingerprint,
            "decision": self.decision,
            "reason": self.reason,
            "team": self.team,
            "timestamp": self.timestamp,
        }
        if self.expires is not None:
            result["expires"] = self.expires
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Disposition:
        return cls(
            schema_version=data["schema_version"],
            issue_fingerprint=data["issue_fingerprint"],
            decision=data["decision"],
            reason=data["reason"],
            team=data["team"],
            timestamp=data["timestamp"],
            expires=data.get("expires"),
        )
