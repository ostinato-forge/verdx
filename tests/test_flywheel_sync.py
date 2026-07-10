"""Tests for flywheel auto-commit sync."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from verdx._flywheel_sync import sync_flywheel


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True
    )
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


def _write_flywheel_data(repo: Path) -> None:
    fw_dir = repo / "repos" / "test-repo"
    fw_dir.mkdir(parents=True, exist_ok=True)
    (fw_dir / "patterns.json").write_text(json.dumps({"patterns": []}))


def _commit_count(repo: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def _last_commit_msg(repo: Path) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestSyncFlywheel:
    def test_commits_new_flywheel_data(self, git_repo: Path) -> None:
        _write_flywheel_data(git_repo)
        with patch("verdx._flywheel_sync._find_data_repo", return_value=git_repo):
            assert sync_flywheel() is True
        assert _commit_count(git_repo) == 2
        assert "flywheel" in _last_commit_msg(git_repo)

    def test_noop_when_no_changes(self, git_repo: Path) -> None:
        _write_flywheel_data(git_repo)
        with patch("verdx._flywheel_sync._find_data_repo", return_value=git_repo):
            sync_flywheel()
            assert sync_flywheel() is False
        assert _commit_count(git_repo) == 2

    def test_noop_when_no_data_repo(self) -> None:
        with patch("verdx._flywheel_sync._find_data_repo", return_value=None):
            assert sync_flywheel() is False

    def test_noop_when_not_git_repo(self, tmp_path: Path) -> None:
        _write_flywheel_data(tmp_path)
        with patch("verdx._flywheel_sync._find_data_repo", return_value=None):
            assert sync_flywheel() is False

    def test_commits_updated_data(self, git_repo: Path) -> None:
        _write_flywheel_data(git_repo)
        with patch("verdx._flywheel_sync._find_data_repo", return_value=git_repo):
            sync_flywheel()

        corr_dir = git_repo / "repos" / "test-repo" / "corrections"
        corr_dir.mkdir(parents=True, exist_ok=True)
        (corr_dir / "test.jsonl").write_text(
            '{"observation_signature":"s1","target_key":"X",'
            '"initial_severity":"low","final_decision":"fp",'
            '"description":"","domain":"training","timestamp":""}\n'
        )

        with patch("verdx._flywheel_sync._find_data_repo", return_value=git_repo):
            assert sync_flywheel() is True
        assert _commit_count(git_repo) == 3

    def test_respects_env_var_disable(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_flywheel_data(git_repo)
        monkeypatch.setenv("VERDX_FLYWHEEL_SYNC", "0")
        with patch("verdx._flywheel_sync._find_data_repo", return_value=git_repo):
            assert sync_flywheel() is True

    def test_push_after_commit(self, git_repo: Path, tmp_path: Path) -> None:
        bare = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", str(bare)],
            cwd=git_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "master"],
            cwd=git_repo,
            capture_output=True,
            check=True,
        )

        _write_flywheel_data(git_repo)
        with patch("verdx._flywheel_sync._find_data_repo", return_value=git_repo):
            assert sync_flywheel(push=True) is True

        remote_log = subprocess.run(
            ["git", "log", "--oneline", "master"],
            cwd=bare,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "flywheel" in remote_log.stdout
