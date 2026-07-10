"""Validator protocol: the contract between validators and the runner.

Validators are callables: (ValidationContext) -> ValidatorResult.
No base class required -- duck typing via protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from verdx.contracts.observation import ObservationDraft


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Context passed to each validator invocation."""

    run_id: str
    target_key: str
    target_path: str
    target_content_hash: str
    config: dict[str, Any]
    previous_observations: list[dict[str, Any]]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    """Result returned by a validator invocation."""

    validator_id: str
    validator_version: str
    drafts: list[ObservationDraft]
    checks_run: int
    checks_passed: int
    duration_ms: int
    status: str
    error: str | None = None
    skip_reason: str | None = None
    subject_keys_checked: tuple[str, ...] = ()
    subject_keys_skipped: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for caching."""

        d: dict[str, Any] = {
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "drafts": [draft.to_dict() for draft in self.drafts],
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "duration_ms": self.duration_ms,
            "status": self.status,
        }
        if self.error is not None:
            d["error"] = self.error
        if self.skip_reason is not None:
            d["skip_reason"] = self.skip_reason
        if self.subject_keys_checked:
            d["subject_keys_checked"] = list(self.subject_keys_checked)
        if self.subject_keys_skipped:
            d["subject_keys_skipped"] = list(self.subject_keys_skipped)
        if self.metadata is not None:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidatorResult:
        """Deserialize from a JSON-parsed dict."""
        from verdx.contracts.observation import ObservationDraft

        return cls(
            validator_id=data["validator_id"],
            validator_version=data["validator_version"],
            drafts=[ObservationDraft.from_dict(d) for d in data.get("drafts", [])],
            checks_run=data["checks_run"],
            checks_passed=data["checks_passed"],
            duration_ms=data["duration_ms"],
            status=data["status"],
            error=data.get("error"),
            skip_reason=data.get("skip_reason"),
            subject_keys_checked=tuple(data.get("subject_keys_checked", ())),
            subject_keys_skipped=tuple(data.get("subject_keys_skipped", ())),
            metadata=data.get("metadata"),
        )


class Validator(Protocol):
    """Protocol for validator callables."""

    def __call__(self, context: ValidationContext) -> ValidatorResult: ...
