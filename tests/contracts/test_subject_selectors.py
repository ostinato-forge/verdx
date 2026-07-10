"""Tests for subject and coverage selector matching."""

from __future__ import annotations

from tests.contracts.test_models import _make_observation
from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    SUBJECT_SCHEMA_VERSION,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
    matches_coverage_selector,
    matches_subject_selector,
)


def _subject(**overrides):
    defaults = {
        "schema_version": SUBJECT_SCHEMA_VERSION,
        "subject_key": "section:ch01/setup/ge",
        "target_key": "target",
        "kind": "section",
        "label": "GE",
        "selector": SubjectSelector(kind="subject", values=("section:ch01/setup/ge",)),
        "location": "content/ch01/setup/ge.adoc",
        "parent_subject_key": "topic:ch01/setup",
        "metadata": {"file_path": "content/ch01/setup/ge.adoc"},
    }
    return ValidationSubject(**(defaults | overrides))


def _record(subject, **overrides):
    defaults = {
        "schema_version": SUBJECT_COVERAGE_SCHEMA_VERSION,
        "run_id": "run-001",
        "target_key": "target",
        "target_content_hash": "sha256:abc",
        "subject": subject,
        "state": SubjectCoverageState.COMPLETE_WITH_OBSERVATIONS,
        "criticality": "standard",
        "required_validator_ids": ("training-content",),
        "completed_validator_ids": ("training-content",),
        "failed_validator_ids": (),
        "skipped_validator_ids": (),
        "checks_run": 1,
        "checks_passed": 1,
        "observation_fingerprints": ("fp-abc123",),
        "coverage_gap_validator_ids": (),
        "blocking_reason": None,
        "model_health": SubjectModelHealth(),
    }
    return SubjectCoverageRecord(**(defaults | overrides))


class TestSubjectSelectors:
    def test_all_matches(self):
        assert matches_subject_selector(SubjectSelector(kind="all"), _subject()) is True

    def test_kind_matches(self):
        assert (
            matches_subject_selector(SubjectSelector(kind="kind", values=("section",)), _subject())
            is True
        )

    def test_path_matches_file_path(self):
        assert (
            matches_subject_selector(
                SubjectSelector(kind="path", values=("content/ch01/setup/ge.adoc",)),
                _subject(),
            )
            is True
        )

    def test_subject_matches_key(self):
        assert (
            matches_subject_selector(
                SubjectSelector(kind="subject", values=("section:ch01/setup/ge",)),
                _subject(),
            )
            is True
        )

    def test_include_children_matches_parent(self):
        parent = _subject(
            subject_key="topic:ch01/setup",
            kind="topic",
            label="Setup",
            parent_subject_key=None,
        )
        child = _subject()
        subjects = {parent.subject_key: parent, child.subject_key: child}
        assert (
            matches_subject_selector(
                SubjectSelector(kind="kind", values=("topic",), include_children=True),
                child,
                subjects,
            )
            is True
        )

    def test_rule_and_category_do_not_match_bare_subjects(self):
        subject = _subject()
        assert (
            matches_subject_selector(SubjectSelector(kind="rule", values=("r1",)), subject) is False
        )
        assert (
            matches_subject_selector(
                SubjectSelector(kind="category", values=("correctness",)),
                subject,
            )
            is False
        )

    def test_unknown_custom_schema_no_match(self):
        assert (
            matches_subject_selector(
                SubjectSelector(kind="custom", values=("x",), metadata={"schema": "unknown.v1"}),
                _subject(),
            )
            is False
        )


class TestCoverageSelectors:
    def test_rule_matches_observation(self):
        obs = _make_observation(rule_id="training-image-ref")
        subject = _subject()
        record = _record(subject, observation_fingerprints=(obs.issue_fingerprint,))
        assert (
            matches_coverage_selector(
                SubjectSelector(kind="rule", values=("training-image-ref",)),
                record,
                {obs.issue_fingerprint: obs},
            )
            is True
        )

    def test_category_matches_observation(self):
        obs = _make_observation(category="correctness")
        subject = _subject()
        record = _record(subject, observation_fingerprints=(obs.issue_fingerprint,))
        assert (
            matches_coverage_selector(
                SubjectSelector(kind="category", values=("correctness",)),
                record,
                {obs.issue_fingerprint: obs},
            )
            is True
        )

    def test_coverage_selector_delegates_subject_kinds(self):
        subject = _subject()
        record = _record(subject)
        assert (
            matches_coverage_selector(
                SubjectSelector(kind="kind", values=("section",)),
                record,
                {},
            )
            is True
        )
