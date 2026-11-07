"""Regression tests for content identity across files and checkouts."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdx._content import compute_content_hash, content_manifest


@pytest.mark.parametrize("directory", [False, True])
def test_same_size_large_file_edits_change_identity(tmp_path: Path, directory: bool) -> None:
    file = tmp_path / "large.bin"
    file.write_bytes(b"a" * 10_485_761)
    target = tmp_path if directory else file
    before = compute_content_hash(target)
    with file.open("r+b") as stream:
        stream.write(b"b")
    assert compute_content_hash(target) != before


def test_hash_is_portable_and_delimits_file_names_and_contents(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a").write_text("bc")
    (second / "ab").write_text("c")
    assert compute_content_hash(first) != compute_content_hash(second)
    (second / "ab").unlink()
    (second / "a").write_text("bc")
    assert compute_content_hash(first) == compute_content_hash(second)


def test_scope_matches_root_and_nested_files_and_keeps_hidden_source(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("root")
    nested = tmp_path / ".github"
    nested.mkdir()
    (nested / "guide.md").write_text("nested")
    (tmp_path / "skip.txt").write_text("skip")
    assert [row["path"] for row in content_manifest(tmp_path, include=("**/*.md",))] == [
        ".github/guide.md",
        "README.md",
    ]


def test_receipts_and_caches_do_not_change_source_identity(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("source")
    before = compute_content_hash(tmp_path)
    for name in (".verdx", ".pytest_cache", "__pycache__"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "result").write_text("generated")
    assert compute_content_hash(tmp_path) == before


def test_unreadable_directory_does_not_silently_disappear(tmp_path: Path, monkeypatch) -> None:
    def failed_walk(target, *, onerror):
        onerror(PermissionError("cannot read source directory"))
        return iter(())

    monkeypatch.setattr("verdx._content.os.walk", failed_walk)
    with pytest.raises(PermissionError, match="cannot read"):
        content_manifest(tmp_path)
