"""Tests for the fast-path eligibility classifier."""

from verdx.engine.lifecycle.fast_path import (
    ChangeDescription,
    classify,
)


def _change(
    *,
    files: tuple[str, ...] = ("README.md",),
    lines: int = 1,
    types: frozenset[str] = frozenset({"text"}),
    diff_digest: str = "sha256:abc123",
    base_commit: str = "abc1234",
) -> ChangeDescription:
    return ChangeDescription(
        files_changed=files,
        lines_modified=lines,
        change_types=types,
        diff_digest=diff_digest,
        base_commit=base_commit,
    )


class TestEligible:
    def test_single_file_text_change(self):
        result = classify(_change())
        assert result.eligible
        assert len(result.reasons) > 0
        assert result.denylist_hits == ()

    def test_boundary_10_lines(self):
        result = classify(_change(lines=10))
        assert result.eligible

    def test_multiple_safe_types(self):
        result = classify(_change(types=frozenset({"text", "comment", "formatting"})))
        assert result.eligible

    def test_metadata_type(self):
        result = classify(_change(types=frozenset({"metadata"})))
        assert result.eligible


class TestIneligible:
    def test_multiple_files(self):
        result = classify(_change(files=("a.txt", "b.txt")))
        assert not result.eligible
        assert any("single file" in r.lower() or "2 files" in r.lower() for r in result.reasons)

    def test_boundary_11_lines(self):
        result = classify(_change(lines=11))
        assert not result.eligible
        assert any("10" in r for r in result.reasons)

    def test_code_change_type(self):
        result = classify(_change(types=frozenset({"code"})))
        assert not result.eligible
        assert any("code" in r.lower() for r in result.reasons)

    def test_mixed_types_text_and_code(self):
        result = classify(_change(types=frozenset({"text", "code"})))
        assert not result.eligible

    def test_empty_files(self):
        result = classify(_change(files=()))
        assert not result.eligible

    def test_zero_lines(self):
        result = classify(_change(lines=0))
        assert not result.eligible


class TestDenylist:
    def test_dockerfile(self):
        result = classify(_change(files=("Dockerfile",)))
        assert not result.eligible
        assert len(result.denylist_hits) > 0

    def test_dockerfile_variant(self):
        result = classify(_change(files=("Dockerfile.dev",)))
        assert not result.eligible

    def test_nested_dockerfile(self):
        result = classify(_change(files=("src/Dockerfile",)))
        assert not result.eligible

    def test_pyproject_toml(self):
        result = classify(_change(files=("pyproject.toml",)))
        assert not result.eligible

    def test_lockfile(self):
        result = classify(_change(files=("poetry.lock",)))
        assert not result.eligible
        assert any("lock" in h.lower() for h in result.denylist_hits)

    def test_github_workflows(self):
        result = classify(_change(files=(".github/workflows/ci.yml",)))
        assert not result.eligible

    def test_nested_migrations(self):
        result = classify(_change(files=("src/db/migrations/001.sql",)))
        assert not result.eligible

    def test_root_migrations(self):
        result = classify(_change(files=("migrations/001.sql",)))
        assert not result.eligible

    def test_extra_denylist_pattern(self):
        result = classify(
            _change(files=("guides/ch03.adoc",)),
            extra_denylist=("guides/**",),
        )
        assert not result.eligible
        assert any("guides" in h for h in result.denylist_hits)

    def test_safe_file_not_denied(self):
        result = classify(_change(files=("docs/notes.txt",)))
        assert result.eligible


class TestUnchecked:
    def test_unchecked_always_present(self):
        result = classify(_change())
        assert len(result.unchecked) > 0
        assert any("test" in u.lower() for u in result.unchecked)
        assert any("cross-file" in u.lower() for u in result.unchecked)
