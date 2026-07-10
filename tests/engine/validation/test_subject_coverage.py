"""Tests for pure subject coverage derivation."""

from __future__ import annotations

from tests.contracts.test_models import _make_observation, _make_validator_status
from verdx.contracts.dimensions import CoverageStatus
from verdx.contracts.subjects import (
    SUBJECT_SCHEMA_VERSION,
    ExpectedCoverageSubject,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
)
from verdx.engine.validation.coverage import (
    derive_subject_coverage,
    rollup_coverage_status,
    subject_coverage_penalty,
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
        "metadata": {"file_path": "content/ch01/setup/ge.adoc"},
    }
    return ValidationSubject(**(defaults | overrides))


def _expected(subject, *, validator_ids=("training-structure",), criticality="critical"):
    return ExpectedCoverageSubject(
        subject_selector=SubjectSelector(kind="subject", values=(subject.subject_key,)),
        required_validator_ids=validator_ids,
        criticality=criticality,
        dimensions_affected=("structural-integrity",),
        reason="must cover subject",
    )


def _derive(subjects, expected, **overrides):
    defaults = {
        "run_id": "run-001",
        "target_key": "target",
        "target_content_hash": "sha256:abc",
        "subjects": tuple(subjects),
        "expected_subjects": tuple(expected),
        "validator_statuses": (_make_validator_status(validator_id="training-structure"),),
        "observations": (),
    }
    return derive_subject_coverage(**(defaults | overrides))


class TestSubjectCoverageDerivation:
    def test_complete_clean(self):
        subject = _subject()
        result = _derive([subject], [_expected(subject)])
        assert result.records[0].state == SubjectCoverageState.COMPLETE_CLEAN

    def test_complete_with_observations(self):
        subject = _subject()
        obs = _make_observation(
            location="content/ch01/setup/ge.adoc:42",
            validator_id="training-structure",
        )
        result = _derive([subject], [_expected(subject)], observations=(obs,))
        assert result.records[0].state == SubjectCoverageState.COMPLETE_WITH_OBSERVATIONS
        assert result.records[0].observation_fingerprints == (obs.issue_fingerprint,)

    def test_failed(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject)],
            validator_statuses=(
                _make_validator_status(
                    validator_id="training-structure",
                    status="failed",
                    error="crash",
                ),
            ),
        )
        assert result.records[0].state == SubjectCoverageState.FAILED
        assert result.records[0].coverage_gap_validator_ids == ("training-structure",)

    def test_skipped(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject)],
            validator_statuses=(
                _make_validator_status(
                    validator_id="training-structure",
                    status="skipped",
                    checks_run=0,
                    checks_passed=0,
                    observation_count=0,
                    skip_reason="not applicable",
                ),
            ),
        )
        assert result.records[0].state == SubjectCoverageState.SKIPPED

    def test_missing(self):
        subject = _subject()
        result = _derive([subject], [_expected(subject)], validator_statuses=())
        assert result.records[0].state == SubjectCoverageState.MISSING

    def test_partial(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject, validator_ids=("training-structure", "training-content"))],
            validator_statuses=(_make_validator_status(validator_id="training-structure"),),
        )
        assert result.records[0].state == SubjectCoverageState.PARTIAL

    def test_unsupported(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject)],
            model_health_by_subject_key={
                subject.subject_key: SubjectModelHealth(unsupported_features=("images",)),
            },
        )
        assert result.records[0].state == SubjectCoverageState.UNSUPPORTED

    def test_model_limited(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject)],
            model_health_by_subject_key={
                subject.subject_key: SubjectModelHealth(parser_warnings=("outline partial",)),
            },
        )
        assert result.records[0].state == SubjectCoverageState.MODEL_LIMITED

    def test_unmapped_fields_are_model_limited(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject)],
            model_health_by_subject_key={
                subject.subject_key: SubjectModelHealth(unmapped_fields=("content.custom",)),
            },
        )
        assert result.records[0].state == SubjectCoverageState.MODEL_LIMITED
        assert result.records[0].blocking_reason == "content.custom"

    def test_not_applicable_is_explicit_only(self):
        subject = _subject()
        result = _derive(
            [subject],
            [_expected(subject)],
            not_applicable_subject_keys=frozenset({subject.subject_key}),
        )
        assert result.records[0].state == SubjectCoverageState.NOT_APPLICABLE

    def test_unmappable_observation_warns_without_error(self):
        subject = _subject()
        obs = _make_observation(location="unrelated/path.adoc", validator_id="unknown-validator")
        result = _derive([subject], [_expected(subject)], observations=(obs,))
        assert result.warnings == (f"unmapped observation {obs.issue_fingerprint}",)
        assert result.records[0].observation_fingerprints == ()


class TestSubjectCoverageRollup:
    def test_critical_debt_degrades(self):
        subject = _subject()
        records = _derive([subject], [_expected(subject)], validator_statuses=()).records
        assert rollup_coverage_status(records, (), frozenset()) == CoverageStatus.DEGRADED

    def test_standard_debt_is_partial(self):
        subject = _subject()
        records = _derive(
            [subject],
            [_expected(subject, criticality="standard")],
            validator_statuses=(),
        ).records
        assert rollup_coverage_status(records, (), frozenset()) == CoverageStatus.PARTIAL

    def test_subject_penalty_applies_with_zero_observations(self):
        subject = _subject()
        records = _derive([subject], [_expected(subject)], validator_statuses=()).records
        assert subject_coverage_penalty(records) == 20
