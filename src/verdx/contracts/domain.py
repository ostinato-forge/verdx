"""Domain plugin interface contracts.

Every domain package (markdown-gate, example-gate, etc.) exposes a
``register_domain()`` entry point via ``verdx.domains`` that returns
a ``DomainSpec``.  All four methods are required — domains without
profiles or subjects return empty tuples/results.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from verdx.contracts.subjects import (
    ExpectedCoverageSubject,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
    derive_subject_key,
    normalize_semantic_location,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from verdx.contracts.check import CheckSpec
    from verdx.contracts.config import DomainConfig


# ---------------------------------------------------------------------------
# Subject discovery result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubjectDiscoveryResult:
    """Output of a domain's ``discover_subjects()`` method."""

    subjects: tuple[ValidationSubject, ...] = ()
    expected_subjects: tuple[ExpectedCoverageSubject, ...] = ()
    model_health_by_subject_key: Mapping[str, SubjectModelHealth] = field(default_factory=dict)
    discovery_warnings: tuple[str, ...] = ()
    validator_subject_selectors: Mapping[str, SubjectSelector] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Subject resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubjectResolver:
    """Resolves file paths to subject keys within a CheckContext."""

    subjects: Mapping[str, ValidationSubject] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> SubjectResolver:
        return cls()

    def subject_for(self, path: str | Path) -> ValidationSubject | None:
        """Find the subject that best matches a file path.

        Matching order: exact subject_key, exact location, exact
        metadata['file_path'], then longest location prefix.
        """
        normalized = normalize_semantic_location(str(path))

        for subject in self.subjects.values():
            if normalize_semantic_location(subject.subject_key) == normalized:
                return subject
            if subject.location and normalize_semantic_location(subject.location) == normalized:
                return subject
            file_path = subject.metadata.get("file_path", "")
            if file_path and normalize_semantic_location(str(file_path)) == normalized:
                return subject

        best_len = 0
        best_subject: ValidationSubject | None = None
        for subject in self.subjects.values():
            if not subject.location:
                continue
            loc = normalize_semantic_location(subject.location)
            if normalized.startswith(loc) and len(loc) > best_len:
                best_len = len(loc)
                best_subject = subject

        return best_subject

    def subject_key_for(
        self,
        target_key: str,
        path: str | Path,
        *,
        fallback_kind: str = "file",
        domain: str | None = None,
    ) -> str:
        """Returns the subject key for a path, or a deterministic fallback."""
        subject = self.subject_for(path)
        if subject is not None:
            return subject.subject_key
        return derive_subject_key(target_key, fallback_kind, str(path), stable_domain_id=domain)

    @classmethod
    def from_discovery(cls, result: SubjectDiscoveryResult) -> SubjectResolver:
        """Build a resolver from a discovery result."""
        return cls(subjects={s.subject_key: s for s in result.subjects})


# ---------------------------------------------------------------------------
# DomainSpec — the required domain plugin interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainSpec:
    """Required interface for a domain plugin.

    Every domain must implement all four callables.  Domains without
    profiles return ``()``.  Domains without subjects return
    ``SubjectDiscoveryResult()``.
    """

    name: str
    markers: tuple[str, ...]
    load_default_config: Callable[[], DomainConfig]
    load_checks: Callable[[], tuple[CheckSpec, ...]]
    load_profiles: Callable[[], tuple[dict[str, Any], ...]]
    discover_subjects: Callable[[Path, str], SubjectDiscoveryResult]
    load_pipeline_config: Callable[[], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not callable(self.load_default_config):
            raise ValueError("load_default_config must be callable")
        if not callable(self.load_checks):
            raise ValueError("load_checks must be callable")
        if not callable(self.load_profiles):
            raise ValueError("load_profiles must be callable")
        if not callable(self.discover_subjects):
            raise ValueError("discover_subjects must be callable")
        if self.load_pipeline_config is not None and not callable(self.load_pipeline_config):
            raise ValueError("load_pipeline_config must be callable or None")
