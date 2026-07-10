"""Tests for the gate-author path-safety helpers."""

from __future__ import annotations

import os
import sys

import pytest

from verdx.contracts import iter_target_files, resolve_within


class TestIterTargetFiles:
    def test_single_matching_file_yields_itself(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        assert list(iter_target_files(f, ".md")) == [f]

    def test_single_non_matching_file_yields_nothing(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi\n")
        assert list(iter_target_files(f, ".md")) == []

    def test_directory_walk_yields_matching_files_sorted(self, tmp_path):
        (tmp_path / "b.md").write_text("b\n")
        (tmp_path / "a.md").write_text("a\n")
        (tmp_path / "c.txt").write_text("c\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "d.md").write_text("d\n")
        result = list(iter_target_files(tmp_path, ".md"))
        assert result == [tmp_path / "a.md", tmp_path / "b.md", sub / "d.md"]

    def test_missing_path_yields_nothing(self, tmp_path):
        assert list(iter_target_files(tmp_path / "nope", ".md")) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink perms on Windows")
    def test_symlinked_file_is_skipped(self, tmp_path):
        real = tmp_path / "real.md"
        real.write_text("real\n")
        outside = tmp_path.parent / "outside.md"
        outside.write_text("outside\n")
        link = tmp_path / "link.md"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("symlinks not permitted")
        result = list(iter_target_files(tmp_path, ".md"))
        assert real in result
        assert link not in result

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink perms on Windows")
    def test_symlinked_dir_is_not_descended(self, tmp_path):
        outside_dir = tmp_path.parent / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "evil.md").write_text("evil\n")
        link = tmp_path / "linkdir"
        try:
            os.symlink(outside_dir, link)
        except OSError:
            pytest.skip("symlinks not permitted")
        result = list(iter_target_files(tmp_path, ".md"))
        assert all(p.name != "evil.md" for p in result)


class TestResolveWithin:
    def test_resolves_normal_relative_path(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("x\n")
        assert resolve_within(tmp_path, "a.md") == f.resolve()

    def test_rejects_parent_escape(self, tmp_path):
        (tmp_path.parent / "evil.md").write_text("evil\n")
        assert resolve_within(tmp_path, "../evil.md") is None

    def test_rejects_absolute_escape(self, tmp_path):
        assert resolve_within(tmp_path, "/etc/passwd") is None

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink perms on Windows")
    def test_rejects_symlink_final_component(self, tmp_path):
        real = tmp_path / "real.md"
        real.write_text("x\n")
        link = tmp_path / "link.md"
        try:
            os.symlink(real, link)
        except OSError:
            pytest.skip("symlinks not permitted")
        assert resolve_within(tmp_path, "link.md") is None
