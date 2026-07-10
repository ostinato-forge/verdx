import os
import sys

import pytest

from verdx.domains.markdown.validators.headings import check_headings


def test_flags_heading_level_jump(tmp_path):
    (tmp_path / "doc.md").write_text("# Title\n### Too deep\n")
    issues = check_headings.fn(tmp_path)
    assert any("jumps from h1 to h3" in i for i in issues)


def test_flags_multiple_h1(tmp_path):
    (tmp_path / "doc.md").write_text("# One\n# Two\n")
    assert any("level-1 headings" in i for i in check_headings.fn(tmp_path))


def test_clean_headings_pass(tmp_path):
    (tmp_path / "doc.md").write_text("# Title\n## Section\n### Sub\n")
    assert check_headings.fn(tmp_path) == []


def test_single_file_target_is_checked(tmp_path):
    # A single-FILE target must actually be checked, not silently pass.
    f = tmp_path / "doc.md"
    f.write_text("# Title\n### Too deep\n")
    issues = check_headings.fn(f)
    assert any("jumps from h1 to h3" in i for i in issues)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink perms on Windows")
def test_symlinked_file_is_skipped(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("# Title\n### Too deep\n")
    link = tmp_path / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks not permitted")
    # The symlink escapes the target root; the validator must not read through it.
    assert check_headings.fn(tmp_path) == []
