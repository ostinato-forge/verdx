"""CheckContext — the validator's view of a single check execution.

Replaces ValidationContext.  The ``previous_observations`` property always
returns ``[]`` — validators must not be history-dependent (reproducibility
invariant).  Domain state enters as plain ``config`` or
``ValidationSubject.metadata``.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from verdx.contracts.domain import SubjectResolver
from verdx.contracts.subjects import ValidationSubject


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Passed to every ``@validator``-decorated check function.

    Provides the target path, merged config, subject resolution,
    and execution constraints.  Serializable for process-pool workers
    (minus the resolver, which is rebuilt from subjects).
    """

    run_id: str
    target_key: str
    target: Path
    target_content_hash: str
    domain: str
    check_id: str
    check_version: str
    config: Mapping[str, Any]
    timeout_seconds: int
    scratch_dir: Path | None = None
    subjects: Mapping[str, ValidationSubject] = field(default_factory=dict)
    subject_resolver: SubjectResolver = field(default_factory=SubjectResolver.empty)

    @property
    def target_path(self) -> str:
        return str(self.target)

    @property
    def previous_observations(self) -> list[dict[str, Any]]:
        return []

    def relpath(self, path: str | Path) -> str:
        """Return path relative to target, as posix string."""
        p = Path(path)
        if p.is_absolute():
            return p.relative_to(self.target).as_posix()
        return p.as_posix()

    def subject_key_for(self, path: str | Path, *, fallback_kind: str = "file") -> str:
        """Resolve a path to its subject key, with deterministic fallback."""
        return self.subject_resolver.subject_key_for(
            self.target_key,
            path,
            fallback_kind=fallback_kind,
            domain=self.domain,
        )

    def subject_for(self, path: str | Path) -> ValidationSubject | None:
        """Look up the ValidationSubject for a path, or None."""
        return self.subject_resolver.subject_for(path)

    def to_worker_dict(self) -> dict[str, Any]:
        """Serialize for process-pool transmission (JSON-safe)."""
        return {
            "run_id": self.run_id,
            "target_key": self.target_key,
            "target": str(self.target),
            "target_content_hash": self.target_content_hash,
            "domain": self.domain,
            "check_id": self.check_id,
            "check_version": self.check_version,
            "config": self.config,
            "timeout_seconds": self.timeout_seconds,
            "scratch_dir": str(self.scratch_dir) if self.scratch_dir else None,
            "subjects": {k: v.to_dict() for k, v in self.subjects.items()},
        }

    @classmethod
    def from_worker_dict(cls, data: dict[str, Any]) -> CheckContext:
        """Rebuild from process-pool transmission."""
        subjects = {k: ValidationSubject.from_dict(v) for k, v in data.get("subjects", {}).items()}
        resolver = SubjectResolver(subjects=subjects)
        return cls(
            run_id=data["run_id"],
            target_key=data["target_key"],
            target=Path(data["target"]),
            target_content_hash=data["target_content_hash"],
            domain=data["domain"],
            check_id=data["check_id"],
            check_version=data["check_version"],
            config=data["config"],
            timeout_seconds=data["timeout_seconds"],
            scratch_dir=Path(data["scratch_dir"]) if data.get("scratch_dir") else None,
            subjects=subjects,
            subject_resolver=resolver,
        )
