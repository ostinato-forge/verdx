"""Tests for the example gate.

Call validator logic via .fn (the wrapper carries the raw function as .fn,
matching the markdown gate convention established in Task 4b).
"""

from verdx.domains.example.fixers import fix_trailing_blank_lines
from verdx.domains.example.validators import check_trailing_blank_lines
from verdx.types import Observation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obs(message: str) -> Observation:
    """Construct a minimal valid Observation for fixer tests."""
    return Observation(
        severity="medium",
        category="example",
        location=None,
        message=message,
        fixable=True,
        signature="test-sig",
    )


# ---------------------------------------------------------------------------
# Validator unit tests (call raw logic via .fn)
# ---------------------------------------------------------------------------


def test_flags_file_ending_with_blank_lines(tmp_path):
    (tmp_path / "a.txt").write_text("content\n\n\n")
    issues = check_trailing_blank_lines.fn(tmp_path)
    assert any("trailing blank line" in i for i in issues)


def test_flags_single_trailing_blank_line(tmp_path):
    (tmp_path / "a.txt").write_text("content\n\n")
    issues = check_trailing_blank_lines.fn(tmp_path)
    assert any("trailing blank line" in i for i in issues)


def test_clean_file_passes(tmp_path):
    (tmp_path / "a.txt").write_text("content\n")
    assert check_trailing_blank_lines.fn(tmp_path) == []


def test_empty_file_passes(tmp_path):
    # An empty file has no lines, so no trailing blank — passes.
    (tmp_path / "a.txt").write_text("")
    assert check_trailing_blank_lines.fn(tmp_path) == []


def test_only_non_txt_files_are_ignored(tmp_path):
    # The validator only checks .txt files; .md files are not its concern.
    (tmp_path / "README.md").write_text("# Title\n\n\n")
    assert check_trailing_blank_lines.fn(tmp_path) == []


def test_flags_nested_txt_file(tmp_path):
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("line\n\n")
    issues = check_trailing_blank_lines.fn(tmp_path)
    assert any("trailing blank line" in i for i in issues)


def test_single_file_target_is_checked(tmp_path):
    # A single-FILE target must actually be checked, not silently pass.
    f = tmp_path / "a.txt"
    f.write_text("content\n\n")
    issues = check_trailing_blank_lines.fn(f)
    assert any("trailing blank line" in i for i in issues)


# ---------------------------------------------------------------------------
# Fixer unit tests
# ---------------------------------------------------------------------------


def test_fixer_removes_trailing_blank_lines(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("content\n\n\n")
    # The observation message format mirrors what the validator produces:
    # "<rel_path> ends with a trailing blank line"
    obs = _obs("a.txt ends with a trailing blank line")
    result = fix_trailing_blank_lines.fn(tmp_path, obs)
    assert result is not None
    assert f.read_text() == "content\n"


def test_fixer_is_idempotent(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("content\n")
    # File is already clean (no trailing blanks); fixer should still run safely.
    obs = _obs("a.txt ends with a trailing blank line")
    fix_trailing_blank_lines.fn(tmp_path, obs)
    assert f.read_text() == "content\n"


def test_fixer_ignores_unrelated_observation(tmp_path):
    (tmp_path / "a.txt").write_text("content\n\n")
    obs = _obs("a.txt has some other problem")
    result = fix_trailing_blank_lines.fn(tmp_path, obs)
    assert result is None


def test_fixer_refuses_to_write_outside_target(tmp_path):
    # A crafted observation naming ../evil.txt must not let the fixer write
    # outside the target root.
    target = tmp_path / "target"
    target.mkdir()
    evil = tmp_path / "evil.txt"
    evil.write_text("original\n\n")
    obs = _obs("../evil.txt ends with a trailing blank line")
    result = fix_trailing_blank_lines.fn(target, obs)
    assert result is None
    assert evil.read_text() == "original\n\n"
