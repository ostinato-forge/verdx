"""Pure subject coverage derivation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from verdx.contracts.dimensions import CoverageStatus
from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    ExpectedCoverageSubject,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
    matches_subject_selector,
    normalize_semantic_location,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from verdx.contracts.assessment import ValidatorStatus
    from verdx.contracts.observation import Observation

SUBJECT_COVERAGE_PENALTIES = {
    "missing_critical": 20,
    "failed_critical": 20,
    "unsupported_critical": 18,
    "model_limited_critical": 15,
    "partial_critical": 12,
    "skipped_critical": 12,
    "missing_standard": 8,
    "failed_standard": 8,
    "unsupported_standard": 6,
    "model_limited_standard": 5,
    "partial_standard": 4,
    "skipped_standard": 4,
}

_CRITICALITY_RANK = {"optional": 0, "standard": 1, "critical": 2}
_DEBT_STATES = frozenset(
    {
        SubjectCoverageState.PARTIAL,
        SubjectCoverageState.FAILED,
        SubjectCoverageState.SKIPPED,
        SubjectCoverageState.MISSING,
        SubjectCoverageState.UNSUPPORTED,
        SubjectCoverageState.MODEL_LIMITED,
    }
)


@dataclass(frozen=True, slots=True)
class SubjectCoverageDerivation:
    records: tuple[SubjectCoverageRecord, ...]
    warnings: tuple[str, ...] = ()


def derive_subject_coverage(
    *,
    run_id: str,
    target_key: str,
    target_content_hash: str,
    subjects: tuple[ValidationSubject, ...],
    expected_subjects: tuple[ExpectedCoverageSubject, ...],
    validator_statuses: tuple[ValidatorStatus, ...],
    observations: tuple[Observation, ...],
    validator_subject_keys_checked: Mapping[str, frozenset[str]] | None = None,
    validator_subject_keys_skipped: Mapping[str, frozenset[str]] | None = None,
    validator_subject_selectors: Mapping[str, SubjectSelector] | None = None,
    model_health_by_subject_key: Mapping[str, SubjectModelHealth] | None = None,
    not_applicable_subject_keys: frozenset[str] = frozenset(),
) -> SubjectCoverageDerivation:
    """Derive immutable subject coverage records from explicit run inputs."""
    subjects_by_key = {subject.subject_key: subject for subject in subjects}
    expected_by_key = _expand_expectations(expected_subjects, subjects, subjects_by_key)
    observations_by_subject, warnings = _map_observations_to_subjects(
        subjects=subjects,
        observations=observations,
        validator_subject_selectors=validator_subject_selectors or {},
    )

    status_by_validator = {status.validator_id: status for status in validator_statuses}
    checked_by_validator = validator_subject_keys_checked or {}
    skipped_by_validator = validator_subject_keys_skipped or {}
    selectors_by_validator = validator_subject_selectors or {}
    health_by_key = model_health_by_subject_key or {}

    records: list[SubjectCoverageRecord] = []
    for subject_key in sorted(expected_by_key):
        subject = subjects_by_key[subject_key]
        expected = expected_by_key[subject_key]
        required_ids = expected.required_validator_ids
        status_sets = _classify_validator_statuses(
            subject=subject,
            subjects_by_key=subjects_by_key,
            required_validator_ids=required_ids,
            status_by_validator=status_by_validator,
            checked_by_validator=checked_by_validator,
            skipped_by_validator=skipped_by_validator,
            selectors_by_validator=selectors_by_validator,
        )
        model_health = health_by_key.get(subject_key, SubjectModelHealth())
        observation_fingerprints = tuple(
            sorted(obs.issue_fingerprint for obs in observations_by_subject.get(subject_key, ()))
        )
        state, blocking_reason = _derive_state(
            subject_key=subject_key,
            required_validator_ids=required_ids,
            status_sets=status_sets,
            model_health=model_health,
            observation_fingerprints=observation_fingerprints,
            not_applicable_subject_keys=not_applicable_subject_keys,
        )
        checks_run = sum(status_by_validator[vid].checks_run for vid in status_sets.completed)
        checks_passed = sum(status_by_validator[vid].checks_passed for vid in status_sets.completed)
        records.append(
            SubjectCoverageRecord(
                schema_version=SUBJECT_COVERAGE_SCHEMA_VERSION,
                run_id=run_id,
                target_key=target_key,
                target_content_hash=target_content_hash,
                subject=subject,
                state=state,
                criticality=expected.criticality,
                required_validator_ids=tuple(sorted(required_ids)),
                completed_validator_ids=tuple(sorted(status_sets.completed)),
                failed_validator_ids=tuple(sorted(status_sets.failed)),
                skipped_validator_ids=tuple(sorted(status_sets.skipped)),
                checks_run=checks_run,
                checks_passed=checks_passed,
                observation_fingerprints=observation_fingerprints,
                coverage_gap_validator_ids=tuple(sorted(status_sets.failed | status_sets.skipped)),
                blocking_reason=blocking_reason,
                model_health=model_health,
                metadata={"reasons": sorted(expected.reasons)},
            )
        )

    return SubjectCoverageDerivation(records=tuple(records), warnings=tuple(warnings))


def rollup_coverage_status(
    subject_records: tuple[SubjectCoverageRecord, ...],
    validator_statuses: tuple[ValidatorStatus, ...],
    critical_validator_ids: frozenset[str],
) -> CoverageStatus:
    """Roll subject coverage into the legacy run-level coverage status."""
    if any(
        record.criticality == "critical" and record.state in _DEBT_STATES
        for record in subject_records
    ):
        return CoverageStatus.DEGRADED
    if any(
        record.criticality == "standard" and record.state in _DEBT_STATES
        for record in subject_records
    ):
        return CoverageStatus.PARTIAL

    for status in validator_statuses:
        if status.status in {"failed", "skipped"}:
            if status.validator_id in critical_validator_ids:
                return CoverageStatus.DEGRADED
            return CoverageStatus.PARTIAL
    return CoverageStatus.FULL


def subject_coverage_penalty(subject_records: tuple[SubjectCoverageRecord, ...]) -> int:
    """Return score penalty for subject coverage debt."""
    penalty = 0
    for record in subject_records:
        if record.criticality == "optional":
            continue
        key = f"{record.state.value}_{record.criticality}"
        penalty += SUBJECT_COVERAGE_PENALTIES.get(key, 0)
    return penalty


@dataclass(frozen=True, slots=True)
class _StatusSets:
    completed: frozenset[str]
    failed: frozenset[str]
    skipped: frozenset[str]
    missing: frozenset[str]


@dataclass(slots=True)
class _ExpandedExpectation:
    required_validator_ids: set[str]
    criticality: str
    reasons: set[str]


def _expand_expectations(
    expected_subjects: tuple[ExpectedCoverageSubject, ...],
    subjects: tuple[ValidationSubject, ...],
    subjects_by_key: Mapping[str, ValidationSubject],
) -> dict[str, _ExpandedExpectation]:
    expanded: dict[str, _ExpandedExpectation] = {}
    for expected in expected_subjects:
        for subject in subjects:
            if not matches_subject_selector(expected.subject_selector, subject, subjects_by_key):
                continue
            entry = expanded.setdefault(
                subject.subject_key,
                _ExpandedExpectation(
                    required_validator_ids=set(),
                    criticality=expected.criticality,
                    reasons=set(),
                ),
            )
            entry.required_validator_ids.update(expected.required_validator_ids)
            entry.reasons.add(expected.reason)
            if _CRITICALITY_RANK[expected.criticality] > _CRITICALITY_RANK[entry.criticality]:
                entry.criticality = expected.criticality
    return expanded


def _classify_validator_statuses(
    *,
    subject: ValidationSubject,
    subjects_by_key: Mapping[str, ValidationSubject],
    required_validator_ids: set[str],
    status_by_validator: Mapping[str, ValidatorStatus],
    checked_by_validator: Mapping[str, frozenset[str]],
    skipped_by_validator: Mapping[str, frozenset[str]],
    selectors_by_validator: Mapping[str, SubjectSelector],
) -> _StatusSets:
    completed: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    missing: set[str] = set()

    for validator_id in required_validator_ids:
        status = status_by_validator.get(validator_id)
        if status is None:
            missing.add(validator_id)
            continue
        explicit_checked = checked_by_validator.get(validator_id)
        explicit_skipped = skipped_by_validator.get(validator_id)
        if explicit_skipped is not None and subject.subject_key in explicit_skipped:
            skipped.add(validator_id)
            continue
        if status.status == "failed":
            failed.add(validator_id)
            continue
        if status.status == "skipped":
            skipped.add(validator_id)
            continue
        if explicit_checked is not None:
            if subject.subject_key in explicit_checked:
                completed.add(validator_id)
            else:
                missing.add(validator_id)
            continue
        selector = selectors_by_validator.get(validator_id)
        if selector is None or matches_subject_selector(selector, subject, subjects_by_key):
            completed.add(validator_id)
        else:
            missing.add(validator_id)

    return _StatusSets(
        completed=frozenset(completed),
        failed=frozenset(failed),
        skipped=frozenset(skipped),
        missing=frozenset(missing),
    )


def _derive_state(
    *,
    subject_key: str,
    required_validator_ids: set[str],
    status_sets: _StatusSets,
    model_health: SubjectModelHealth,
    observation_fingerprints: tuple[str, ...],
    not_applicable_subject_keys: frozenset[str],
) -> tuple[SubjectCoverageState, str | None]:
    if subject_key in not_applicable_subject_keys:
        return SubjectCoverageState.NOT_APPLICABLE, None
    if not required_validator_ids:
        return SubjectCoverageState.UNSUPPORTED, "no validators declared for subject"
    if status_sets.failed:
        return SubjectCoverageState.FAILED, _reason("failed", status_sets.failed)
    if status_sets.skipped and not (status_sets.completed or status_sets.missing):
        return SubjectCoverageState.SKIPPED, _reason("skipped", status_sets.skipped)
    if status_sets.missing and not status_sets.completed:
        return SubjectCoverageState.MISSING, _reason("missing", status_sets.missing)
    if status_sets.skipped or status_sets.missing:
        missing_or_skipped = status_sets.skipped | status_sets.missing
        return SubjectCoverageState.PARTIAL, _reason("partial", missing_or_skipped)
    if model_health.unsupported_features:
        return SubjectCoverageState.UNSUPPORTED, "; ".join(model_health.unsupported_features)
    if model_health.has_debt:
        return SubjectCoverageState.MODEL_LIMITED, _model_health_reason(model_health)
    if observation_fingerprints:
        return SubjectCoverageState.COMPLETE_WITH_OBSERVATIONS, None
    return SubjectCoverageState.COMPLETE_CLEAN, None


def _model_health_reason(model_health: SubjectModelHealth) -> str:
    reasons = (*model_health.parser_warnings, *model_health.unmapped_fields)
    return "; ".join(reasons)


def _reason(prefix: str, validator_ids: frozenset[str]) -> str:
    return f"{prefix}: {', '.join(sorted(validator_ids))}"


def _map_observations_to_subjects(
    *,
    subjects: tuple[ValidationSubject, ...],
    observations: tuple[Observation, ...],
    validator_subject_selectors: Mapping[str, SubjectSelector],
) -> tuple[dict[str, list[Observation]], list[str]]:
    by_key: dict[str, list[Observation]] = {}
    warnings: list[str] = []
    exact_index: dict[str, str] = {}
    path_subjects: list[tuple[str, ValidationSubject]] = []
    subjects_by_key = {subject.subject_key: subject for subject in subjects}

    for subject in subjects:
        for candidate in _location_candidates(subject):
            exact_index.setdefault(candidate, subject.subject_key)
        if subject.location:
            path_subjects.append((normalize_semantic_location(subject.location), subject))

    for observation in observations:
        normalized_location = normalize_semantic_location(observation.location)
        subject_key = exact_index.get(normalized_location)
        if subject_key is None and normalized_location:
            subject_key = _longest_prefix_match(normalized_location, path_subjects)
        if subject_key is None:
            selector = validator_subject_selectors.get(observation.validator_id)
            if selector is not None:
                matches = [
                    subject.subject_key
                    for subject in subjects
                    if matches_subject_selector(selector, subject, subjects_by_key)
                ]
                if len(matches) == 1:
                    subject_key = matches[0]
        if subject_key is None:
            warnings.append(f"unmapped observation {observation.issue_fingerprint}")
            continue
        by_key.setdefault(subject_key, []).append(observation)

    return by_key, warnings


def _location_candidates(subject: ValidationSubject) -> set[str]:
    candidates = {
        normalize_semantic_location(subject.subject_key),
        normalize_semantic_location(subject.location),
    }
    file_path = subject.metadata.get("file_path")
    if isinstance(file_path, str):
        candidates.add(normalize_semantic_location(file_path))
    return {candidate for candidate in candidates if candidate}


def _longest_prefix_match(
    normalized_location: str,
    path_subjects: list[tuple[str, ValidationSubject]],
) -> str | None:
    matches: list[tuple[int, str]] = []
    for path, subject in path_subjects:
        if not path:
            continue
        if normalized_location == path or normalized_location.startswith(f"{path}/"):
            matches.append((len(path), subject.subject_key))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]
