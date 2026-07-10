"""Subject coverage history: append-only cross-run coverage snapshots.

Stores lightweight snapshots derived from SubjectCoverageRecord,
enabling trend analysis and readiness tracking across runs.

History is keyed by target_key hash (same layout as observation history).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from verdx.engine.runtime.store.history import _target_key_hash
from verdx.engine.runtime.store.jsonl import append_jsonl, read_jsonl

if TYPE_CHECKING:
    from pathlib import Path

    from verdx.contracts.subjects import SubjectCoverageRecord


@dataclass(frozen=True, slots=True)
class SubjectCoverageSnapshot:
    """Lightweight frozen snapshot of per-subject coverage for history storage.

    Derived from SubjectCoverageRecord but omits bulky fields
    (full validator ID lists, observation fingerprints, model_health details)
    to keep the append-only log compact.
    """

    run_id: str
    timestamp: str
    target_key: str
    target_content_hash: str
    subject_key: str
    subject_kind: str
    subject_label: str
    state: str
    criticality: str
    observation_count: int
    checks_run: int
    checks_passed: int
    coverage_gap_validator_ids: tuple[str, ...]
    blocking_reason: str | None
    has_model_debt: bool

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "target_key": self.target_key,
            "target_content_hash": self.target_content_hash,
            "subject_key": self.subject_key,
            "subject_kind": self.subject_kind,
            "subject_label": self.subject_label,
            "state": self.state,
            "criticality": self.criticality,
            "observation_count": self.observation_count,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "coverage_gap_validator_ids": list(self.coverage_gap_validator_ids),
            "has_model_debt": self.has_model_debt,
        }
        if self.blocking_reason is not None:
            result["blocking_reason"] = self.blocking_reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubjectCoverageSnapshot:
        return cls(
            run_id=data["run_id"],
            timestamp=data["timestamp"],
            target_key=data["target_key"],
            target_content_hash=data["target_content_hash"],
            subject_key=data["subject_key"],
            subject_kind=data["subject_kind"],
            subject_label=data["subject_label"],
            state=data["state"],
            criticality=data["criticality"],
            observation_count=data["observation_count"],
            checks_run=data["checks_run"],
            checks_passed=data["checks_passed"],
            coverage_gap_validator_ids=tuple(data.get("coverage_gap_validator_ids", ())),
            blocking_reason=data.get("blocking_reason"),
            has_model_debt=data.get("has_model_debt", False),
        )

    @classmethod
    def from_record(
        cls,
        record: SubjectCoverageRecord,
        timestamp: str,
    ) -> SubjectCoverageSnapshot:
        """Extract a lightweight snapshot from a full coverage record."""
        return cls(
            run_id=record.run_id,
            timestamp=timestamp,
            target_key=record.target_key,
            target_content_hash=record.target_content_hash,
            subject_key=record.subject.subject_key,
            subject_kind=record.subject.kind,
            subject_label=record.subject.label,
            state=record.state.value,
            criticality=record.criticality,
            observation_count=len(record.observation_fingerprints),
            checks_run=record.checks_run,
            checks_passed=record.checks_passed,
            coverage_gap_validator_ids=record.coverage_gap_validator_ids,
            blocking_reason=record.blocking_reason,
            has_model_debt=record.model_health.has_debt,
        )


class SubjectCoverageHistoryStore:
    """File-backed subject coverage history, keyed by target_key hash."""

    def __init__(self, state_root: Path) -> None:
        self._history_dir = state_root / "history"

    def _target_dir(self, target_key: str) -> Path:
        return self._history_dir / _target_key_hash(target_key)

    def append(
        self,
        target_key: str,
        snapshots: list[SubjectCoverageSnapshot],
    ) -> None:
        """Append subject coverage snapshots to target history."""
        if not snapshots:
            return
        path = self._target_dir(target_key) / "subject-coverage.jsonl"
        append_jsonl(path, [s.to_dict() for s in snapshots])

    def get_all(self, target_key: str) -> list[SubjectCoverageSnapshot]:
        """Read all subject coverage snapshots for a target."""
        path = self._target_dir(target_key) / "subject-coverage.jsonl"
        return [SubjectCoverageSnapshot.from_dict(d) for d in read_jsonl(path)]

    def get_by_subject(
        self,
        target_key: str,
        subject_key: str,
    ) -> list[SubjectCoverageSnapshot]:
        """Read subject coverage snapshots filtered by subject_key."""
        return [s for s in self.get_all(target_key) if s.subject_key == subject_key]

    def get_latest_by_subject(
        self,
        target_key: str,
    ) -> dict[str, SubjectCoverageSnapshot]:
        """Return the most recent snapshot per subject_key.

        Since JSONL is append-order, the last entry per subject_key wins.
        """
        latest: dict[str, SubjectCoverageSnapshot] = {}
        for snapshot in self.get_all(target_key):
            latest[snapshot.subject_key] = snapshot
        return latest
