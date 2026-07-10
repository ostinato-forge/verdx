"""Tests for atomic file write operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.engine.runtime.store.atomic import atomic_write, atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


class TestAtomicWrite:
    def test_creates_file(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        atomic_write(target, b"hello world")
        assert target.read_bytes() == b"hello world"

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        target.write_text("old content")
        atomic_write(target, b"new content")
        assert target.read_bytes() == b"new content"

    def test_creates_parent_directories(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "output.txt"
        atomic_write(target, b"data")
        assert target.read_bytes() == b"data"

    def test_no_temp_file_left_on_success(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        atomic_write(target, b"data")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "output.txt"

    def test_preserves_old_file_on_write_error(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        target.write_text("original")

        class FakeError(Exception):
            pass

        try:
            import unittest.mock as mock

            with mock.patch("os.write", side_effect=FakeError("boom")):
                atomic_write(target, b"new data")
        except FakeError:
            pass

        assert target.read_text() == "original"


class TestAtomicWriteText:
    def test_writes_utf8(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        atomic_write_text(target, "café résumé")
        assert target.read_text(encoding="utf-8") == "café résumé"

    def test_deterministic(self, tmp_path: Path):
        t1 = tmp_path / "a.txt"
        t2 = tmp_path / "b.txt"
        atomic_write_text(t1, "same content")
        atomic_write_text(t2, "same content")
        assert t1.read_bytes() == t2.read_bytes()
