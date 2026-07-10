"""Tests for subject-grain coverage core models."""

from __future__ import annotations

import json

import pytest

from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    SUBJECT_SCHEMA_VERSION,
    ExpectedCoverageSubject,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
    derive_subject_key,
)


def _selector(**overrides):
    defaults = {"kind": "subject", "values": ("section:installing-rhel/storage/ge",)}
    return SubjectSelector(**(defaults | overrides))


def _subject(**overrides):
    defaults = {
        "schema_version": SUBJECT_SCHEMA_VERSION,
        "subject_key": "section:installing-rhel/storage/ge",
        "target_key": "sample/ch04",
        "kind": "section",
        "label": "GE",
        "selector": _selector(),
        "location": "content/installing-rhel/storage/ge.adoc",
    }
    return ValidationSubject(**(defaults | overrides))


def _record(**overrides):
    defaults = {
        "schema_version": SUBJECT_COVERAGE_SCHEMA_VERSION,
        "run_id": "run-001",
        "target_key": "sample/ch04",
        "target_content_hash": "sha256:abc",
        "subject": _subject(),
        "state": SubjectCoverageState.COMPLETE_CLEAN,
        "criticality": "standard",
        "required_validator_ids": ("training-structure",),
        "completed_validator_ids": ("training-structure",),
        "failed_validator_ids": (),
        "skipped_validator_ids": (),
        "checks_run": 1,
        "checks_passed": 1,
        "observation_fingerprints": (),
        "coverage_gap_validator_ids": (),
        "blocking_reason": None,
        "model_health": SubjectModelHealth(),
    }
    return SubjectCoverageRecord(**(defaults | overrides))


class TestSubjectSelector:
    def test_all_selector_has_no_values(self):
        selector = SubjectSelector(kind="all")
        assert selector.values == ()

    def test_non_all_requires_values(self):
        with pytest.raises(ValueError, match="must have at least one value"):
            SubjectSelector(kind="kind")

    def test_custom_requires_schema(self):
        with pytest.raises(ValueError, match="custom selector requires"):
            SubjectSelector(kind="custom", values=("x",))

    def test_round_trip(self):
        selector = SubjectSelector(
            kind="custom",
            values=("training",),
            metadata={"schema": "training.v1", "section_types": ["ge"]},
        )
        assert SubjectSelector.from_dict(selector.to_dict()) == selector


class TestValidationSubject:
    def test_round_trip(self):
        subject = _subject(metadata={"file_path": "content/ge.adoc"})
        restored = ValidationSubject.from_dict(json.loads(json.dumps(subject.to_dict())))
        assert restored == subject

    def test_invalid_kind(self):
        with pytest.raises(ValueError, match="subject kind"):
            _subject(kind="magic")

    def test_subject_key_helper_is_deterministic(self):
        first = derive_subject_key("target", "section", "Content/CH01/GE.adoc:42")
        second = derive_subject_key("target", "section", "content/ch01/ge.adoc:99")
        assert first == second
        assert first == "section:content/ch01/ge.adoc"


class TestExpectedCoverageSubject:
    def test_round_trip(self):
        expected = ExpectedCoverageSubject(
            subject_selector=SubjectSelector(kind="kind", values=("section",)),
            required_validator_ids=("training-structure",),
            criticality="critical",
            dimensions_affected=("structural-integrity",),
            reason="sections must exist",
        )
        assert ExpectedCoverageSubject.from_dict(expected.to_dict()) == expected

    def test_invalid_criticality(self):
        with pytest.raises(ValueError, match="criticality"):
            ExpectedCoverageSubject(
                subject_selector=SubjectSelector(kind="all"),
                required_validator_ids=(),
                criticality="urgent",
                dimensions_affected=(),
                reason="bad",
            )


class TestSubjectCoverageRecord:
    def test_round_trip(self):
        record = _record(
            state=SubjectCoverageState.FAILED,
            failed_validator_ids=("training-structure",),
            coverage_gap_validator_ids=("training-structure",),
            blocking_reason="failed: training-structure",
        )
        restored = SubjectCoverageRecord.from_dict(record.to_dict())
        assert restored == record

    def test_uses_coverage_gap_validator_ids(self):
        record = _record(coverage_gap_validator_ids=("training-structure",))
        assert record.to_dict()["coverage_gap_validator_ids"] == ["training-structure"]


class TestSubjectModelHealth:
    def test_unmapped_fields_round_trip_and_count_as_debt(self):
        health = SubjectModelHealth(unmapped_fields=("content.custom",))
        restored = SubjectModelHealth.from_dict(health.to_dict())
        assert restored == health
        assert restored.has_debt is True
