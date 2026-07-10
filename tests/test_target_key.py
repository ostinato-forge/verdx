"""Target-key path-safety tests.

The flywheel target key is derived from the validation target path and then
used to build filesystem paths under the flywheel data root. A key that is
empty, contains a path separator, or is ``..`` could escape the data root or
collide across distinct targets. These tests pin the sanitization.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from verdx.api import _resolve_target_key
from verdx.engine.flywheel.store import FlywheelStore


class TestResolveTargetKey:
    def test_normal_directory_name_is_kept_as_prefix(self, tmp_path: Path) -> None:
        d = tmp_path / "myrepo"
        d.mkdir()
        key = _resolve_target_key(d.resolve())
        assert key.startswith("myrepo-")
        assert "/" not in key and os.sep not in key
        assert ".." not in key

    def test_dotdot_basename_yields_safe_key(self, tmp_path: Path) -> None:
        # A target whose basename resolves to ".." must not produce a key with
        # a path separator or "..".
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        target = (sub / "..").resolve()  # -> tmp_path/a
        key = _resolve_target_key(target)
        assert key
        assert "/" not in key and os.sep not in key
        assert ".." not in key

    def test_root_path_yields_safe_nonempty_key(self) -> None:
        # Path("/").name == "" — must still yield a non-empty, safe key.
        key = _resolve_target_key(Path("/").resolve())
        assert key
        assert "/" not in key and os.sep not in key
        assert ".." not in key

    def test_distinct_paths_same_basename_get_distinct_keys(self, tmp_path: Path) -> None:
        a = tmp_path / "one" / "docs"
        b = tmp_path / "two" / "docs"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        key_a = _resolve_target_key(a.resolve())
        key_b = _resolve_target_key(b.resolve())
        assert key_a != key_b
        assert key_a.startswith("docs-")
        assert key_b.startswith("docs-")

    def test_same_path_is_stable(self, tmp_path: Path) -> None:
        d = tmp_path / "repo"
        d.mkdir()
        assert _resolve_target_key(d.resolve()) == _resolve_target_key(d.resolve())


class TestStoreKeyDefense:
    def test_rejects_separator_in_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="target_key"):
            FlywheelStore("../escape", data_root=tmp_path)

    def test_rejects_dotdot_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="target_key"):
            FlywheelStore("..", data_root=tmp_path)

    def test_rejects_empty_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="target_key"):
            FlywheelStore("", data_root=tmp_path)

    def test_bad_key_does_not_write_outside_data_root(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        outside = tmp_path / "outside.json"
        with pytest.raises(ValueError):
            store = FlywheelStore("../outside", data_root=data_root)
            store.save_overrides([])
        assert not outside.exists()

    def test_valid_key_writes_inside_data_root(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        store = FlywheelStore("repo-abcd1234", data_root=data_root)
        dest = store.save_overrides([])
        assert dest.resolve().is_relative_to(data_root.resolve())
