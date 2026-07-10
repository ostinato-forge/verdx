"""Subject-grain coverage models and selector helpers.

Pure core module: no filesystem, network, time, subprocess, or random input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from verdx.contracts.observation import Observation

SUBJECT_SCHEMA_VERSION = "verdx.subject.v1"
SUBJECT_COVERAGE_SCHEMA_VERSION = "verdx.subject_coverage.v1"

VALID_SUBJECT_KINDS = frozenset(
    {
        "target",
        "outline",
        "chapter",
        "topic",
        "section",
        "exercise",
        "asset",
        "file",
        "custom",
    }
)
VALID_SELECTOR_KINDS = frozenset({"all", "kind", "path", "subject", "rule", "category", "custom"})
VALID_CRITICALITIES = frozenset({"critical", "standard", "optional"})

_SLUG_RE = re.compile(r"[^a-z0-9._:/-]+")
_MULTI_SEP_RE = re.compile(r"/+")


class SubjectCoverageState(Enum):
    COMPLETE_CLEAN = "complete_clean"
    COMPLETE_WITH_OBSERVATIONS = "complete_with_observations"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    MODEL_LIMITED = "model_limited"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class SubjectSelector:
    """Pure data scope selector for subjects and coverage records."""

    kind: str
    values: tuple[str, ...] = ()
    include_children: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in VALID_SELECTOR_KINDS:
            raise ValueError(f"selector kind must be one of {sorted(VALID_SELECTOR_KINDS)}")
        if self.kind == "all" and self.values:
            raise ValueError("all selector must not have values")
        if self.kind != "all" and not self.values:
            raise ValueError(f"{self.kind} selector must have at least one value")
        if self.kind == "custom":
            schema = self.metadata.get("schema")
            if not isinstance(schema, str) or not schema:
                raise ValueError("custom selector requires non-empty metadata['schema']")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "values": list(self.values),
            "include_children": self.include_children,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubjectSelector:
        return cls(
            kind=data["kind"],
            values=tuple(data.get("values", ())),
            include_children=data.get("include_children", False),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class ValidationSubject:
    """A stable logical unit inside a validation target."""

    schema_version: str
    subject_key: str
    target_key: str
    kind: str
    label: str
    selector: SubjectSelector
    location: str | None = None
    parent_subject_key: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SUBJECT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SUBJECT_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if not self.subject_key:
            raise ValueError("subject_key must be non-empty")
        if not self.target_key:
            raise ValueError("target_key must be non-empty")
        if self.kind not in VALID_SUBJECT_KINDS:
            raise ValueError(f"subject kind must be one of {sorted(VALID_SUBJECT_KINDS)}")
        if not self.label:
            raise ValueError("label must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "subject_key": self.subject_key,
            "target_key": self.target_key,
            "kind": self.kind,
            "label": self.label,
            "selector": self.selector.to_dict(),
            "metadata": self.metadata,
        }
        if self.location is not None:
            result["location"] = self.location
        if self.parent_subject_key is not None:
            result["parent_subject_key"] = self.parent_subject_key
        if self.content_hash is not None:
            result["content_hash"] = self.content_hash
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidationSubject:
        return cls(
            schema_version=data["schema_version"],
            subject_key=data["subject_key"],
            target_key=data["target_key"],
            kind=data["kind"],
            label=data["label"],
            selector=SubjectSelector.from_dict(data["selector"]),
            location=data.get("location"),
            parent_subject_key=data.get("parent_subject_key"),
            content_hash=data.get("content_hash"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class ExpectedCoverageSubject:
    """Declares that selected subjects should be checked by validators."""

    subject_selector: SubjectSelector
    required_validator_ids: tuple[str, ...]
    criticality: str
    dimensions_affected: tuple[str, ...]
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.criticality not in VALID_CRITICALITIES:
            raise ValueError(f"criticality must be one of {sorted(VALID_CRITICALITIES)}")
        if any(not validator_id for validator_id in self.required_validator_ids):
            raise ValueError("required_validator_ids must contain non-empty strings")
        if not self.reason:
            raise ValueError("reason must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_selector": self.subject_selector.to_dict(),
            "required_validator_ids": list(self.required_validator_ids),
            "criticality": self.criticality,
            "dimensions_affected": list(self.dimensions_affected),
            "reason": self.reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpectedCoverageSubject:
        return cls(
            subject_selector=SubjectSelector.from_dict(data["subject_selector"]),
            required_validator_ids=tuple(data.get("required_validator_ids", ())),
            criticality=data["criticality"],
            dimensions_affected=tuple(data.get("dimensions_affected", ())),
            reason=data["reason"],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class SubjectModelHealth:
    """Model/discovery limitations that affect a subject."""

    parser_warnings: tuple[str, ...] = ()
    unmapped_fields: tuple[str, ...] = ()
    unsupported_features: tuple[str, ...] = ()
    discovery_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_debt(self) -> bool:
        return bool(self.parser_warnings or self.unmapped_fields or self.unsupported_features)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "parser_warnings": list(self.parser_warnings),
            "unmapped_fields": list(self.unmapped_fields),
            "unsupported_features": list(self.unsupported_features),
            "metadata": self.metadata,
        }
        if self.discovery_version is not None:
            result["discovery_version"] = self.discovery_version
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubjectModelHealth:
        return cls(
            parser_warnings=tuple(data.get("parser_warnings", ())),
            unmapped_fields=tuple(data.get("unmapped_fields", ())),
            unsupported_features=tuple(data.get("unsupported_features", ())),
            discovery_version=data.get("discovery_version"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class SubjectCoverageRecord:
    """Derived per-subject coverage truth for one run."""

    schema_version: str
    run_id: str
    target_key: str
    target_content_hash: str
    subject: ValidationSubject
    state: SubjectCoverageState
    criticality: str
    required_validator_ids: tuple[str, ...]
    completed_validator_ids: tuple[str, ...]
    failed_validator_ids: tuple[str, ...]
    skipped_validator_ids: tuple[str, ...]
    checks_run: int
    checks_passed: int
    observation_fingerprints: tuple[str, ...]
    coverage_gap_validator_ids: tuple[str, ...]
    blocking_reason: str | None
    model_health: SubjectModelHealth
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SUBJECT_COVERAGE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SUBJECT_COVERAGE_SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.target_key:
            raise ValueError("target_key must be non-empty")
        if not self.target_content_hash:
            raise ValueError("target_content_hash must be non-empty")
        if self.criticality not in VALID_CRITICALITIES:
            raise ValueError(f"criticality must be one of {sorted(VALID_CRITICALITIES)}")
        if self.checks_run < 0:
            raise ValueError("checks_run must be non-negative")
        if self.checks_passed < 0:
            raise ValueError("checks_passed must be non-negative")
        if self.checks_passed > self.checks_run:
            raise ValueError("checks_passed cannot exceed checks_run")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "target_key": self.target_key,
            "target_content_hash": self.target_content_hash,
            "subject": self.subject.to_dict(),
            "state": self.state.value,
            "criticality": self.criticality,
            "required_validator_ids": list(self.required_validator_ids),
            "completed_validator_ids": list(self.completed_validator_ids),
            "failed_validator_ids": list(self.failed_validator_ids),
            "skipped_validator_ids": list(self.skipped_validator_ids),
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "observation_fingerprints": list(self.observation_fingerprints),
            "coverage_gap_validator_ids": list(self.coverage_gap_validator_ids),
            "model_health": self.model_health.to_dict(),
            "metadata": self.metadata,
        }
        if self.blocking_reason is not None:
            result["blocking_reason"] = self.blocking_reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubjectCoverageRecord:
        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            target_key=data["target_key"],
            target_content_hash=data["target_content_hash"],
            subject=ValidationSubject.from_dict(data["subject"]),
            state=SubjectCoverageState(data["state"]),
            criticality=data["criticality"],
            required_validator_ids=tuple(data.get("required_validator_ids", ())),
            completed_validator_ids=tuple(data.get("completed_validator_ids", ())),
            failed_validator_ids=tuple(data.get("failed_validator_ids", ())),
            skipped_validator_ids=tuple(data.get("skipped_validator_ids", ())),
            checks_run=data["checks_run"],
            checks_passed=data["checks_passed"],
            observation_fingerprints=tuple(data.get("observation_fingerprints", ())),
            coverage_gap_validator_ids=tuple(data.get("coverage_gap_validator_ids", ())),
            blocking_reason=data.get("blocking_reason"),
            model_health=SubjectModelHealth.from_dict(data["model_health"]),
            metadata=dict(data.get("metadata", {})),
        )


def normalize_semantic_location(value: str | None) -> str:
    """Normalize stable semantic locations without preserving line numbers as identity."""
    if value is None:
        return ""
    normalized = value.strip().replace("\\", "/").lower()
    if ":" in normalized:
        head, tail = normalized.rsplit(":", 1)
        if tail.isdigit():
            normalized = head
    normalized = _SLUG_RE.sub("-", normalized)
    normalized = _MULTI_SEP_RE.sub("/", normalized)
    return normalized.strip("-/")


def derive_subject_key(
    target_key: str,
    kind: str,
    semantic_location: str,
    stable_domain_id: str | None = None,
) -> str:
    """Derive a deterministic subject key from semantic inputs."""
    if not target_key:
        raise ValueError("target_key must be non-empty")
    if kind not in VALID_SUBJECT_KINDS:
        raise ValueError(f"subject kind must be one of {sorted(VALID_SUBJECT_KINDS)}")
    normalized_location = normalize_semantic_location(semantic_location)
    if not normalized_location:
        raise ValueError("semantic_location must normalize to a non-empty value")
    normalized_domain_id = normalize_semantic_location(stable_domain_id)
    suffix = f"/{normalized_domain_id}" if normalized_domain_id else ""
    return f"{kind}:{normalized_location}{suffix}"


def matches_subject_selector(
    selector: SubjectSelector,
    subject: ValidationSubject,
    subjects_by_key: Mapping[str, ValidationSubject] | None = None,
) -> bool:
    """Return whether a selector matches a bare ValidationSubject."""
    if _matches_subject_selector_direct(selector, subject):
        return True
    if not selector.include_children or subjects_by_key is None:
        return False
    parent_key = subject.parent_subject_key
    while parent_key:
        parent = subjects_by_key.get(parent_key)
        if parent is None:
            return False
        if _matches_subject_selector_direct(selector, parent):
            return True
        parent_key = parent.parent_subject_key
    return False


def matches_coverage_selector(
    selector: SubjectSelector,
    record: SubjectCoverageRecord,
    observations_by_fingerprint: Mapping[str, Observation],
    subjects_by_key: Mapping[str, ValidationSubject] | None = None,
) -> bool:
    """Return whether a selector matches a coverage record."""
    if selector.kind == "rule":
        rules = set(selector.values)
        return any(
            (obs := observations_by_fingerprint.get(fp)) is not None and obs.rule_id in rules
            for fp in record.observation_fingerprints
        )
    if selector.kind == "category":
        categories = set(selector.values)
        return any(
            (obs := observations_by_fingerprint.get(fp)) is not None and obs.category in categories
            for fp in record.observation_fingerprints
        )
    return matches_subject_selector(selector, record.subject, subjects_by_key)


def _matches_subject_selector_direct(
    selector: SubjectSelector,
    subject: ValidationSubject,
) -> bool:
    if selector.kind == "all":
        return True
    if selector.kind == "kind":
        return subject.kind in selector.values
    if selector.kind == "subject":
        return subject.subject_key in selector.values
    if selector.kind == "path":
        values = {normalize_semantic_location(v) for v in selector.values}
        candidates = {
            normalize_semantic_location(subject.subject_key),
            normalize_semantic_location(subject.location),
        }
        file_path = subject.metadata.get("file_path")
        if isinstance(file_path, str):
            candidates.add(normalize_semantic_location(file_path))
        return bool(values & candidates)
    return False
