"""Tests for changed-files filtering in the planner."""

from __future__ import annotations

from verdx.contracts.check import CheckSpec, PythonAdapter
from verdx.contracts.dimensions import DimLevel
from verdx.contracts.plan import ResolvedCheck
from verdx.engine.runtime.planner import _filter_by_changed_files


def _make_check(
    check_id: str,
    file_domains: tuple[str, ...] | None = None,
    skip_code: str | None = None,
    skip_reason: str | None = None,
) -> ResolvedCheck:
    spec = CheckSpec(
        check_id=check_id,
        domain="test",
        title=check_id,
        category="test",
        version="1.0",
        adapter=PythonAdapter(import_path="test.mod:fn"),
        dimensions={"correctness": DimLevel.MEDIUM},
        file_domains=file_domains,
    )
    return ResolvedCheck(
        domain="test",
        check_id=check_id,
        check_version="1.0",
        spec=spec,
        effective_config={},
        config_hash="sha256:0000",
        capabilities=frozenset(),
        critical=False,
        cacheable=True,
        skip_code=skip_code,
        skip_reason=skip_reason,
    )


class TestFilterByChangedFiles:
    def test_skips_when_no_overlap(self):
        checks = [_make_check("content-check", file_domains=("content/",))]
        changed = frozenset({"classroom/grading/lab.py"})
        filtered, warns = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code == "no-changed-overlap"
        assert len(warns) == 1

    def test_runs_when_overlap(self):
        checks = [_make_check("content-check", file_domains=("content/",))]
        changed = frozenset({"content/ch01/lecture.adoc"})
        filtered, warns = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code is None
        assert len(warns) == 0

    def test_runs_when_no_file_domains(self):
        checks = [_make_check("structure-check", file_domains=None)]
        changed = frozenset({"classroom/grading/lab.py"})
        filtered, _ = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code is None

    def test_preserves_already_skipped(self):
        checks = [
            _make_check(
                "disabled-check",
                file_domains=("content/",),
                skip_code="config-disabled",
                skip_reason="disabled by configuration",
            )
        ]
        changed = frozenset({"content/ch01/lecture.adoc"})
        filtered, _ = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code == "config-disabled"

    def test_multiple_prefixes(self):
        checks = [_make_check("exercise-check", file_domains=("content/", "classroom/grading/"))]
        changed = frozenset({"classroom/grading/lab.py"})
        filtered, _ = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code is None

    def test_mixed_checks(self):
        checks = [
            _make_check("content-check", file_domains=("content/",)),
            _make_check("structure-check", file_domains=None),
            _make_check("grading-check", file_domains=("classroom/grading/",)),
        ]
        changed = frozenset({"content/ch01/lecture.adoc"})
        filtered, warns = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code is None
        assert filtered[1].skip_code is None
        assert filtered[2].skip_code == "no-changed-overlap"
        assert len(warns) == 1

    def test_empty_changed_files_skips_all_with_domains(self):
        checks = [
            _make_check("content-check", file_domains=("content/",)),
            _make_check("any-check", file_domains=None),
        ]
        changed: frozenset[str] = frozenset()
        filtered, _ = _filter_by_changed_files(checks, changed)
        assert filtered[0].skip_code == "no-changed-overlap"
        assert filtered[1].skip_code is None
