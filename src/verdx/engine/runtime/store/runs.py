"""Run storage: lifecycle management for validation runs.

Each run gets a directory under <state_root>/runs/<run-id>/
containing status.json, assessment.json, and report.md.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from verdx.engine.runtime.store.atomic import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

VALID_RUN_STATUSES = frozenset({"started", "completed", "crashed"})


@dataclass(frozen=True, slots=True)
class RunStatus:
    run_id: str
    status: str
    timestamp: str
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if self.status not in VALID_RUN_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_RUN_STATUSES)}, got {self.status!r}"
            )
        if not self.timestamp:
            raise ValueError("timestamp must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "run_id": self.run_id,
            "status": self.status,
            "timestamp": self.timestamp,
        }
        if self.error is not None:
            result["error"] = self.error
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunStatus:
        return cls(
            run_id=data["run_id"],
            status=data["status"],
            timestamp=data["timestamp"],
            error=data.get("error"),
        )


class RunStore:
    """File-backed run storage."""

    def __init__(self, state_root: Path) -> None:
        self._runs_dir = state_root / "runs"

    def _run_dir(self, run_id: str) -> Path:
        run_dir = self._runs_dir / run_id
        if not run_dir.resolve().is_relative_to(self._runs_dir.resolve()):
            raise ValueError(f"Invalid run_id: {run_id}")
        return run_dir

    def create_run(self, run_id: str, timestamp: str) -> RunStatus:
        """Create a new run directory and write initial status."""
        run_dir = self._run_dir(run_id)
        if run_dir.exists():
            raise ValueError(f"Run already exists: {run_id}")

        status = RunStatus(
            run_id=run_id,
            status="started",
            timestamp=timestamp,
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_status(run_id, status)
        return status

    def complete_run(
        self,
        run_id: str,
        timestamp: str,
        assessment_json: str,
        report_md: str,
        subject_coverage_json: str | None = None,
    ) -> RunStatus:
        """Mark run as completed, writing assessment and report atomically."""
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise ValueError(f"Run does not exist: {run_id}")

        atomic_write_text(run_dir / "assessment.json", assessment_json)
        atomic_write_text(run_dir / "report.md", report_md)
        if subject_coverage_json is not None:
            atomic_write_text(run_dir / "subject-coverage.json", subject_coverage_json)

        status = RunStatus(
            run_id=run_id,
            status="completed",
            timestamp=timestamp,
        )
        self._write_status(run_id, status)
        return status

    def crash_run(
        self,
        run_id: str,
        timestamp: str,
        error: str,
    ) -> RunStatus:
        """Mark run as crashed."""
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise ValueError(f"Run does not exist: {run_id}")

        status = RunStatus(
            run_id=run_id,
            status="crashed",
            timestamp=timestamp,
            error=error,
        )
        self._write_status(run_id, status)
        return status

    def get_status(self, run_id: str) -> RunStatus | None:
        """Read run status, or None if run doesn't exist."""
        status_path = self._run_dir(run_id) / "status.json"
        if not status_path.exists():
            return None
        data = json.loads(status_path.read_text(encoding="utf-8"))
        return RunStatus.from_dict(data)

    def get_assessment(self, run_id: str) -> str | None:
        """Read assessment JSON, or None if not yet written."""
        path = self._run_dir(run_id) / "assessment.json"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_report(self, run_id: str) -> str | None:
        """Read report Markdown, or None if not yet written."""
        path = self._run_dir(run_id) / "report.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_subject_coverage(self, run_id: str) -> str | None:
        """Read subject coverage JSON, or None if not written."""
        path = self._run_dir(run_id) / "subject-coverage.json"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def list_runs(self) -> list[str]:
        """List all run IDs."""
        if not self._runs_dir.exists():
            return []
        return sorted(d.name for d in self._runs_dir.iterdir() if d.is_dir())

    def delete_run(self, run_id: str) -> bool:
        """Delete a run directory. Returns True if deleted, False if not found."""
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return False
        shutil.rmtree(run_dir)
        return True

    def _write_status(self, run_id: str, status: RunStatus) -> None:
        status_json = json.dumps(status.to_dict(), indent=2)
        atomic_write_text(
            self._run_dir(run_id) / "status.json",
            status_json,
        )
