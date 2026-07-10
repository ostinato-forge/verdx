"""History storage: append-only observation and run history.

History is keyed by target_key hash (stable identity, not content).
This enables cross-run comparison even when content changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from verdx.engine.runtime.store.jsonl import append_jsonl, read_jsonl

if TYPE_CHECKING:
    from pathlib import Path


def _target_key_hash(target_key: str) -> str:
    """Hash a target_key for use as a directory name."""
    return hashlib.sha256(target_key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Minimal run metadata stored in history."""

    run_id: str
    timestamp: str
    target_content_hash: str
    score: int
    observation_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "target_content_hash": self.target_content_hash,
            "score": self.score,
            "observation_count": self.observation_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        return cls(
            run_id=data["run_id"],
            timestamp=data["timestamp"],
            target_content_hash=data["target_content_hash"],
            score=data["score"],
            observation_count=data["observation_count"],
        )


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """Observation snapshot stored in history for cross-run comparison."""

    run_id: str
    observation_id: str
    issue_fingerprint: str
    target_content_hash: str
    rule_id: str
    category: str
    severity: str
    escalation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "observation_id": self.observation_id,
            "issue_fingerprint": self.issue_fingerprint,
            "target_content_hash": self.target_content_hash,
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "escalation": self.escalation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationRecord:
        return cls(
            run_id=data["run_id"],
            observation_id=data["observation_id"],
            issue_fingerprint=data["issue_fingerprint"],
            target_content_hash=data["target_content_hash"],
            rule_id=data["rule_id"],
            category=data["category"],
            severity=data["severity"],
            escalation=data["escalation"],
        )


class HistoryStore:
    """File-backed history storage, keyed by target_key hash."""

    def __init__(self, state_root: Path) -> None:
        self._history_dir = state_root / "history"

    def _target_dir(self, target_key: str) -> Path:
        return self._history_dir / _target_key_hash(target_key)

    def append_run(self, target_key: str, record: RunRecord) -> None:
        """Append a run record to target history."""
        path = self._target_dir(target_key) / "runs.jsonl"
        append_jsonl(path, [record.to_dict()])

    def append_observations(
        self,
        target_key: str,
        records: list[ObservationRecord],
    ) -> None:
        """Append observation records to target history."""
        if not records:
            return
        path = self._target_dir(target_key) / "observations.jsonl"
        append_jsonl(path, [r.to_dict() for r in records])

    def get_runs(self, target_key: str) -> list[RunRecord]:
        """Read all run records for a target."""
        path = self._target_dir(target_key) / "runs.jsonl"
        return [RunRecord.from_dict(d) for d in read_jsonl(path)]

    def get_observations(self, target_key: str) -> list[ObservationRecord]:
        """Read all observation records for a target."""
        path = self._target_dir(target_key) / "observations.jsonl"
        return [ObservationRecord.from_dict(d) for d in read_jsonl(path)]

    def get_previous_fingerprints(
        self,
        target_key: str,
        exclude_run_id: str | None = None,
    ) -> set[str] | None:
        """Get fingerprints from the most recent run for improvement tracking.

        Returns None if no previous runs exist.
        """
        observations = self.get_observations(target_key)
        if not observations:
            return None

        runs = self.get_runs(target_key)
        if not runs:
            return None

        candidate_runs = runs
        if exclude_run_id:
            candidate_runs = [r for r in runs if r.run_id != exclude_run_id]

        if not candidate_runs:
            return None

        last_run_id = candidate_runs[-1].run_id

        return {o.issue_fingerprint for o in observations if o.run_id == last_run_id}

    def get_target_keys(self) -> list[str]:
        """List all target key hashes with history."""
        if not self._history_dir.exists():
            return []
        return sorted(d.name for d in self._history_dir.iterdir() if d.is_dir())
