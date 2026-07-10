"""Tests for CheckContext — the validator's execution view."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdx.contracts.context import CheckContext
from verdx.contracts.domain import SubjectResolver
from verdx.contracts.subjects import SUBJECT_SCHEMA_VERSION, SubjectSelector, ValidationSubject

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _subject(key: str, location: str) -> ValidationSubject:
    return ValidationSubject(
        schema_version=SUBJECT_SCHEMA_VERSION,
        subject_key=key,
        target_key="sample/ch04/ge01",
        kind="file",
        label=key,
        selector=SubjectSelector(kind="all"),
        location=location,
        metadata={},
    )


def _context(**overrides) -> CheckContext:
    defaults = {
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "target": Path("/tmp/course"),
        "target_content_hash": "sha256:abc123",
        "domain": "training",
        "check_id": "training.yaml-lint",
        "check_version": "1.0.0",
        "config": {"strict": True},
        "timeout_seconds": 60,
    }
    return CheckContext(**(defaults | overrides))


# ===========================================================================
# Construction and properties
# ===========================================================================


class TestCheckContext:
    def test_valid(self):
        ctx = _context()
        assert ctx.run_id == "run-001"
        assert ctx.domain == "training"
        assert ctx.config == {"strict": True}

    def test_target_path_property(self):
        ctx = _context(target=Path("/tmp/course"))
        assert ctx.target_path == "/tmp/course"

    def test_relpath_absolute(self):
        ctx = _context(target=Path("/tmp/course"))
        assert ctx.relpath("/tmp/course/ch01/ge01") == "ch01/ge01"

    def test_relpath_relative(self):
        ctx = _context()
        assert ctx.relpath("ch01/ge01") == "ch01/ge01"

    def test_frozen(self):
        ctx = _context()
        with pytest.raises(AttributeError):
            ctx.run_id = "other"  # type: ignore[misc]


# ===========================================================================
# Subject resolution
# ===========================================================================


class TestSubjectResolution:
    def test_subject_for_with_match(self):
        s = _subject("ch01/ge01", "/tmp/course/ch01/ge01")
        resolver = SubjectResolver(subjects={"ch01/ge01": s})
        ctx = _context(subjects={"ch01/ge01": s}, subject_resolver=resolver)
        found = ctx.subject_for("/tmp/course/ch01/ge01")
        assert found is not None
        assert found.subject_key == "ch01/ge01"

    def test_subject_for_no_match(self):
        ctx = _context()
        assert ctx.subject_for("/nonexistent") is None

    def test_subject_key_for_with_match(self):
        s = _subject("ch01/ge01", "/tmp/course/ch01/ge01")
        resolver = SubjectResolver(subjects={"ch01/ge01": s})
        ctx = _context(subjects={"ch01/ge01": s}, subject_resolver=resolver)
        key = ctx.subject_key_for("/tmp/course/ch01/ge01")
        assert key == "ch01/ge01"

    def test_subject_key_for_fallback(self):
        ctx = _context()
        key = ctx.subject_key_for("/nonexistent/file.yml")
        assert key  # deterministic fallback, non-empty


# ===========================================================================
# Worker serialization
# ===========================================================================


class TestWorkerSerialization:
    def test_roundtrip(self):
        s = _subject("ch01/ge01", "/tmp/course/ch01/ge01")
        ctx = _context(
            subjects={"ch01/ge01": s},
            subject_resolver=SubjectResolver(subjects={"ch01/ge01": s}),
            scratch_dir=Path("/tmp/scratch"),
        )
        worker_dict = ctx.to_worker_dict()
        rebuilt = CheckContext.from_worker_dict(worker_dict)

        assert rebuilt.run_id == ctx.run_id
        assert rebuilt.target_key == ctx.target_key
        assert rebuilt.target == ctx.target
        assert rebuilt.domain == ctx.domain
        assert rebuilt.config == ctx.config
        assert rebuilt.scratch_dir == Path("/tmp/scratch")
        assert "ch01/ge01" in rebuilt.subjects

    def test_roundtrip_no_scratch(self):
        ctx = _context(scratch_dir=None)
        worker_dict = ctx.to_worker_dict()
        rebuilt = CheckContext.from_worker_dict(worker_dict)
        assert rebuilt.scratch_dir is None

    def test_resolver_rebuilt_from_subjects(self):
        s = _subject("ch01/ge01", "/tmp/course/ch01/ge01")
        ctx = _context(subjects={"ch01/ge01": s})
        worker_dict = ctx.to_worker_dict()
        rebuilt = CheckContext.from_worker_dict(worker_dict)
        found = rebuilt.subject_for("/tmp/course/ch01/ge01")
        assert found is not None
        assert found.subject_key == "ch01/ge01"
