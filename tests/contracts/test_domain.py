"""Tests for DomainSpec, SubjectResolver, and SubjectDiscoveryResult."""

from __future__ import annotations

import pytest

from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult, SubjectResolver
from verdx.contracts.subjects import (
    SUBJECT_SCHEMA_VERSION,
    SubjectSelector,
    ValidationSubject,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _selector(**overrides) -> SubjectSelector:
    defaults = {"kind": "all"}
    return SubjectSelector(**(defaults | overrides))


def _subject(key: str, location: str | None = None, **meta_overrides) -> ValidationSubject:
    metadata = {"file_path": location or f"/tmp/{key}"} | meta_overrides
    return ValidationSubject(
        schema_version=SUBJECT_SCHEMA_VERSION,
        subject_key=key,
        target_key="sample/ch04/ge01",
        kind="file",
        label=key,
        selector=_selector(),
        location=location or f"/tmp/{key}",
        metadata=metadata,
    )


def _resolver(*subjects: ValidationSubject) -> SubjectResolver:
    return SubjectResolver(subjects={s.subject_key: s for s in subjects})


# ===========================================================================
# SubjectDiscoveryResult
# ===========================================================================


class TestSubjectDiscoveryResult:
    def test_empty_defaults(self):
        r = SubjectDiscoveryResult()
        assert r.subjects == ()
        assert r.expected_subjects == ()
        assert r.discovery_warnings == ()
        assert r.model_health_by_subject_key == {}
        assert r.validator_subject_selectors == {}

    def test_with_subjects(self):
        s = _subject("ch01/ge01")
        r = SubjectDiscoveryResult(subjects=(s,))
        assert len(r.subjects) == 1


# ===========================================================================
# SubjectResolver
# ===========================================================================


class TestSubjectResolver:
    def test_empty_resolver(self):
        r = SubjectResolver.empty()
        assert r.subject_for("/tmp/nonexistent") is None

    def test_exact_key_match(self):
        s = _subject("ch01/ge01", location="/course/ch01/ge01")
        r = _resolver(s)
        found = r.subject_for("ch01/ge01")
        assert found is not None
        assert found.subject_key == "ch01/ge01"

    def test_exact_location_match(self):
        s = _subject("ch01/ge01", location="/course/ch01/ge01")
        r = _resolver(s)
        found = r.subject_for("/course/ch01/ge01")
        assert found is not None
        assert found.subject_key == "ch01/ge01"

    def test_metadata_file_path_match(self):
        s = _subject("ch01/ge01", location=None, file_path="/course/ch01/ge01/instructions.adoc")
        r = _resolver(s)
        found = r.subject_for("/course/ch01/ge01/instructions.adoc")
        assert found is not None

    def test_prefix_match(self):
        s = _subject("ch01", location="/course/ch01")
        r = _resolver(s)
        found = r.subject_for("/course/ch01/ge01/step3.yml")
        assert found is not None
        assert found.subject_key == "ch01"

    def test_longest_prefix_wins(self):
        chapter = _subject("ch01", location="/course/ch01")
        exercise = _subject("ch01/ge01", location="/course/ch01/ge01")
        r = _resolver(chapter, exercise)
        found = r.subject_for("/course/ch01/ge01/step3.yml")
        assert found is not None
        assert found.subject_key == "ch01/ge01"

    def test_no_match_returns_none(self):
        s = _subject("ch01", location="/course/ch01")
        r = _resolver(s)
        assert r.subject_for("/other/path") is None

    def test_subject_key_for_with_match(self):
        s = _subject("ch01/ge01", location="/course/ch01/ge01")
        r = _resolver(s)
        key = r.subject_key_for(
            "sample/ch04/ge01",
            "/course/ch01/ge01",
        )
        assert key == "ch01/ge01"

    def test_subject_key_for_fallback(self):
        r = SubjectResolver.empty()
        key = r.subject_key_for(
            "sample/ch04/ge01",
            "/course/ch01/ge01/step3.yml",
            fallback_kind="file",
        )
        assert key  # deterministic fallback, non-empty

    def test_from_discovery(self):
        s = _subject("ch01/ge01")
        result = SubjectDiscoveryResult(subjects=(s,))
        r = SubjectResolver.from_discovery(result)
        assert "ch01/ge01" in r.subjects


# ===========================================================================
# DomainSpec
# ===========================================================================


class TestDomainSpec:
    def test_valid(self):
        spec = DomainSpec(
            name="training",
            markers=("classroom.yml",),
            load_default_config=lambda: None,  # type: ignore[arg-type]
            load_checks=lambda: (),
            load_profiles=lambda: (),
            discover_subjects=lambda p, k: SubjectDiscoveryResult(),
        )
        assert spec.name == "training"

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must be non-empty"):
            DomainSpec(
                name="",
                markers=(),
                load_default_config=lambda: None,  # type: ignore[arg-type]
                load_checks=lambda: (),
                load_profiles=lambda: (),
                discover_subjects=lambda p, k: SubjectDiscoveryResult(),
            )

    def test_non_callable_rejected(self):
        with pytest.raises(ValueError, match="load_default_config must be callable"):
            DomainSpec(
                name="training",
                markers=(),
                load_default_config="not-callable",  # type: ignore[arg-type]
                load_checks=lambda: (),
                load_profiles=lambda: (),
                discover_subjects=lambda p, k: SubjectDiscoveryResult(),
            )
