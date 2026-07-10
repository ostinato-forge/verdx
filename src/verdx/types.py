"""Porcelain types — the public contract for skills and CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
VALID_DECISIONS = frozenset({"false-positive", "accepted-risk", "deferred"})


@dataclass(frozen=True, slots=True)
class Observation:
    """A single quality finding, simplified from the engine's full type.

    disposition carries the team decision on this finding (false-positive,
    accepted-risk, deferred) when one has been applied, otherwise None. A
    dispositioned finding stays visible but is not auto-fixed.
    """

    severity: str
    category: str
    location: str | None
    message: str
    fixable: bool
    signature: str
    disposition: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(VALID_SEVERITIES)}, got {self.severity!r}"
            )
        if not self.category:
            raise ValueError("category must be non-empty")
        if not self.message:
            raise ValueError("message must be non-empty")
        if not self.signature:
            raise ValueError("signature must be non-empty")


@dataclass(frozen=True, slots=True)
class Override:
    """A recorded override for a false positive or accepted risk."""

    signature: str
    decision: str
    justification: str
    by: str
    date: str
    expires: str | None = None

    def __post_init__(self) -> None:
        if not self.signature:
            raise ValueError("signature must be non-empty")
        if self.decision not in VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(VALID_DECISIONS)}, got {self.decision!r}"
            )
        if not self.justification:
            raise ValueError("justification must be non-empty")
        if not self.by:
            raise ValueError("by must be non-empty")
        if not self.date:
            raise ValueError("date must be non-empty")


@dataclass(frozen=True, slots=True)
class Assessment:
    """Result of verdx.validate() — the porcelain assessment."""

    score: int
    verdict: str
    observations: tuple[Observation, ...]
    decision_hash: str
    overrides_applied: tuple[Override, ...]
    validators_run: tuple[str, ...]
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError(f"score must be 0-100, got {self.score}")
        if self.verdict not in ("pass", "block"):
            raise ValueError(f"verdict must be 'pass' or 'block', got {self.verdict!r}")
        if not self.decision_hash:
            raise ValueError("decision_hash must be non-empty")


@dataclass(frozen=True, slots=True)
class FixAction:
    """A single fix that was applied."""

    observation_signature: str
    fixer_id: str
    description: str
    files_modified: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FixResult:
    """Result of verdx.fix(): before/after assessment plus what changed."""

    assessment_before: Assessment
    assessment_after: Assessment
    fixes_applied: tuple[FixAction, ...]
    iterations: int
    remaining_observations: tuple[Observation, ...] = field(default=())
    termination_reason: str = "incomplete"

    def __post_init__(self) -> None:
        if self.iterations < 1:
            raise ValueError(f"iterations must be >= 1, got {self.iterations}")


@dataclass(frozen=True, slots=True)
class DriveResult:
    """Result of verdx.drive(): verdx owns the loop, the fix command is the fixer of last resort.

    verdx validates (live), and while the verdict is ``block`` it invokes the fix command to
    repair, then re-validates. Only verdx decides "done": ``converged`` is True exactly
    when the final verdict is ``pass``. The fix command never declares completion.
    """

    assessment_before: Assessment
    assessment_after: Assessment
    iterations: int
    fix_invocations: int
    converged: bool
    termination_reason: str

    def __post_init__(self) -> None:
        if self.iterations < 0:
            raise ValueError(f"iterations must be >= 0, got {self.iterations}")
