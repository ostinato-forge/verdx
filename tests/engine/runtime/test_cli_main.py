"""Tests for CLI functionality."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from verdx.engine.runtime.cli import _MAX_HASH_FILE_SIZE, _compute_content_hash, main
from verdx.engine.runtime.store.history import HistoryStore, RunRecord
from verdx.engine.runtime.store.runs import RunStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner():
    return CliRunner()


class TestComputeContentHash:
    """Tests for _compute_content_hash function."""

    def test_directory_with_normal_files_stable_hash(self, tmp_path: Path) -> None:
        """Test that a directory with normal-sized files produces a stable hash."""
        # Create a small directory structure
        (tmp_path / "file1.txt").write_text("Hello world")
        (tmp_path / "file2.txt").write_text("Another file")
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        (sub_dir / "file3.txt").write_text("Nested file")

        # Compute hash twice
        hash1 = _compute_content_hash(tmp_path)
        hash2 = _compute_content_hash(tmp_path)

        # Should be stable
        assert hash1 == hash2
        assert hash1.startswith("sha256:")
        assert len(hash1) == 23  # "sha256:" + 16 hex chars

    def test_file_exceeding_size_limit_handled_without_reading_content(
        self, tmp_path: Path
    ) -> None:
        """Test that a file exceeding the size limit is handled without reading full content."""
        # Create a file larger than the limit
        large_file = tmp_path / "large.bin"
        file_size = _MAX_HASH_FILE_SIZE + 1

        # Write just enough to exceed the limit
        with large_file.open("wb") as f:
            f.write(b"x" * file_size)

        # Compute hash - should not crash
        hash_result = _compute_content_hash(large_file)

        assert hash_result.startswith("sha256:")
        assert len(hash_result) == 23

    def test_single_large_file_produces_hash(self, tmp_path: Path) -> None:
        """Test that a single large file still produces a hash (doesn't crash)."""
        large_file = tmp_path / "huge.bin"
        file_size = _MAX_HASH_FILE_SIZE + 1000

        with large_file.open("wb") as f:
            f.write(b"y" * file_size)

        # Should produce a hash without crashing
        hash_result = _compute_content_hash(large_file)

        assert hash_result.startswith("sha256:")
        assert len(hash_result) == 23

    def test_symlinks_skipped(self, tmp_path: Path) -> None:
        """Test that symlinks are still skipped."""
        # Create a file and a symlink
        real_file = tmp_path / "real.txt"
        real_file.write_text("real content")
        symlink = tmp_path / "link.txt"
        symlink.symlink_to(real_file)

        # Compute hash
        hash_result = _compute_content_hash(tmp_path)

        # Should produce a hash
        assert hash_result.startswith("sha256:")

        # Verify symlink is not hashed by comparing to directory without symlink
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        (other_dir / "real.txt").write_text("real content")

        hash_without_symlink = _compute_content_hash(other_dir)
        assert hash_result == hash_without_symlink

    def test_directory_with_mixed_file_sizes(self, tmp_path: Path) -> None:
        """Test directory with both small and large files."""
        # Small file
        (tmp_path / "small.txt").write_text("small content")

        # Large file
        large_file = tmp_path / "large.bin"
        with large_file.open("wb") as f:
            f.write(b"z" * (_MAX_HASH_FILE_SIZE + 100))

        # Should produce a hash without crashing
        hash_result = _compute_content_hash(tmp_path)

        assert hash_result.startswith("sha256:")
        assert len(hash_result) == 23

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Test that an empty directory produces a hash."""
        hash_result = _compute_content_hash(tmp_path)

        assert hash_result.startswith("sha256:")
        assert len(hash_result) == 23

    def test_small_file_content_matters(self, tmp_path: Path) -> None:
        """Test that for small files, content changes produce different hashes."""
        file1 = tmp_path / "dir1"
        file1.mkdir()
        (file1 / "test.txt").write_text("content A")

        file2 = tmp_path / "dir2"
        file2.mkdir()
        (file2 / "test.txt").write_text("content B")

        hash1 = _compute_content_hash(file1)
        hash2 = _compute_content_hash(file2)

        # Different content should produce different hashes
        assert hash1 != hash2

    def test_large_file_size_matters(self, tmp_path: Path) -> None:
        """Test that for large files, size changes produce different hashes."""
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        file1 = dir1 / "large.bin"
        with file1.open("wb") as f:
            f.write(b"x" * (_MAX_HASH_FILE_SIZE + 100))

        dir2 = tmp_path / "dir2"
        dir2.mkdir()
        file2 = dir2 / "large.bin"
        with file2.open("wb") as f:
            f.write(b"y" * (_MAX_HASH_FILE_SIZE + 200))

        hash1 = _compute_content_hash(dir1)
        hash2 = _compute_content_hash(dir2)

        # Different sizes should produce different hashes (even if content differs)
        assert hash1 != hash2

    def test_hidden_files_skipped(self, tmp_path: Path) -> None:
        """Test that files starting with . are skipped."""
        (tmp_path / "visible.txt").write_text("visible")
        (tmp_path / ".hidden").write_text("hidden")

        hash_result = _compute_content_hash(tmp_path)

        # Compare to directory without hidden file
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        (other_dir / "visible.txt").write_text("visible")

        hash_without_hidden = _compute_content_hash(other_dir)
        assert hash_result == hash_without_hidden

    def test_single_large_file_path_affects_hash(self, tmp_path: Path) -> None:
        """Two large files of the same size at different paths produce different hashes."""
        file1 = tmp_path / "path1.bin"
        file2 = tmp_path / "path2.bin"
        size = _MAX_HASH_FILE_SIZE + 100

        with file1.open("wb") as f:
            f.write(b"x" * size)
        with file2.open("wb") as f:
            f.write(b"x" * size)

        hash1 = _compute_content_hash(file1)
        hash2 = _compute_content_hash(file2)

        assert hash1 != hash2

    def test_file_at_exactly_size_limit(self, tmp_path: Path) -> None:
        """Test file exactly at the size limit is still read."""
        exact_file = tmp_path / "exact.bin"
        with exact_file.open("wb") as f:
            f.write(b"a" * _MAX_HASH_FILE_SIZE)

        hash_result = _compute_content_hash(exact_file)

        assert hash_result.startswith("sha256:")
        assert len(hash_result) == 23

        # Verify content is actually hashed (not just size)
        other_file = tmp_path / "other.bin"
        with other_file.open("wb") as f:
            f.write(b"b" * _MAX_HASH_FILE_SIZE)

        hash_other = _compute_content_hash(other_file)

        # Different content at same size should produce different hashes
        assert hash_result != hash_other


class TestActorCLI:
    """Tests for actor CLI commands."""

    def test_actor_create_basic(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test creating a basic actor."""
        result = runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "human",
                "--state-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        assert "Actor created: developer-1" in result.output
        assert "Kind: human" in result.output

    def test_actor_create_duplicate_error(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test creating duplicate actor returns error."""
        runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "human",
                "--state-dir",
                str(tmp_path),
            ],
        )

        result = runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "agent",
                "--state-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_actor_create_with_team(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test creating actor with team."""
        result = runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "human",
                "--team",
                "content-team",
                "--state-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        assert "Actor created: developer-1" in result.output
        assert "Kind: human" in result.output
        assert "Team: content-team" in result.output

    def test_actor_delete_basic(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test deleting an actor."""
        runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "human",
                "--state-dir",
                str(tmp_path),
            ],
        )

        result = runner.invoke(
            main, ["actor", "delete", "developer-1", "--state-dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "Actor deleted: developer-1" in result.output

    def test_actor_delete_nonexistent(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test deleting nonexistent actor returns error."""
        result = runner.invoke(
            main, ["actor", "delete", "nonexistent", "--state-dir", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_actor_list_empty(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test listing actors when directory is empty."""
        result = runner.invoke(main, ["actor", "list", "--state-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No actors registered." in result.output

    def test_actor_list_table(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test listing actors in table format."""
        runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "human",
                "--team",
                "content-team",
                "--state-dir",
                str(tmp_path),
            ],
        )
        runner.invoke(
            main,
            [
                "actor",
                "create",
                "bot-1",
                "--kind",
                "agent",
                "--state-dir",
                str(tmp_path),
            ],
        )

        result = runner.invoke(main, ["actor", "list", "--state-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "opaque_id" in result.output
        assert "role" in result.output
        assert "kind" in result.output
        assert "team" in result.output
        assert "developer-1" in result.output
        assert "human" in result.output
        assert "content-team" in result.output
        assert "bot-1" in result.output
        assert "agent" in result.output

    def test_actor_list_json(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test listing actors in JSON format."""
        runner.invoke(
            main,
            [
                "actor",
                "create",
                "developer-1",
                "--kind",
                "human",
                "--team",
                "content-team",
                "--state-dir",
                str(tmp_path),
            ],
        )
        runner.invoke(
            main,
            [
                "actor",
                "create",
                "bot-1",
                "--kind",
                "agent",
                "--state-dir",
                str(tmp_path),
            ],
        )

        result = runner.invoke(main, ["actor", "list", "--json", "--state-dir", str(tmp_path)])

        assert result.exit_code == 0
        actors = json.loads(result.output)

        assert len(actors) == 2
        assert any(
            a["opaque_id"] == "developer-1" and a["kind"] == "human" and a["team"] == "content-team"
            for a in actors
        )
        assert any(a["opaque_id"] == "bot-1" and a["kind"] == "agent" for a in actors)

    def test_actor_list_json_empty(self, tmp_path: Path, runner: CliRunner) -> None:
        """Test listing actors in JSON format when empty."""
        result = runner.invoke(main, ["actor", "list", "--json", "--state-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No actors registered." in result.output


class TestPruneCommand:
    """Tests for `verdx prune` command."""

    def test_prune_removes_old_runs(self, tmp_path: Path) -> None:
        """Prune removes runs older than the cutoff date."""
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)

        now = datetime.now(UTC)
        old_date = (now - timedelta(days=100)).isoformat()
        recent_date = (now - timedelta(days=10)).isoformat()

        old_run = store.create_run("run-old", old_date)
        recent_run = store.create_run("run-recent", recent_date)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--days",
                "90",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Pruned 1 runs (1 kept, 0 errors)" in result.stdout

        assert store.get_status(old_run.run_id) is None
        assert store.get_status(recent_run.run_id) is not None

    def test_prune_dry_run(self, tmp_path: Path) -> None:
        """Dry run prints what would be deleted without deleting."""
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)

        old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        old_run = store.create_run("run-old", old_date)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--days",
                "90",
                "--dry-run",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Would delete: run-old" in result.stdout
        assert "Dry run: 1 runs would be pruned" in result.stdout

        assert store.get_status(old_run.run_id) is not None

    def test_prune_keep_last(self, tmp_path: Path) -> None:
        """Keep-last retains N most recent runs regardless of age."""
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)

        now = datetime.now(UTC)
        dates = [(now - timedelta(days=100 + i)).isoformat() for i in range(5)]

        for idx, date in enumerate(dates):
            store.create_run(f"run-{idx}", date)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--days",
                "90",
                "--keep-last",
                "2",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Pruned 3 runs (2 kept, 0 errors)" in result.stdout

        assert store.get_status("run-0") is not None
        assert store.get_status("run-1") is not None
        assert store.get_status("run-4") is None

    def test_prune_empty_state(self, tmp_path: Path) -> None:
        """Prune on empty state directory shows zero counts."""
        state_dir = tmp_path / "state"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Pruned 0 runs (0 kept, 0 errors)" in result.stdout


class TestExportCommand:
    """Tests for `verdx export` command."""

    def test_export_json(self, tmp_path: Path) -> None:
        """Export produces valid JSON array."""
        state_dir = tmp_path / "state"
        history = HistoryStore(state_dir)

        target_key = "test-target"
        records = [
            RunRecord(
                run_id="run-1",
                timestamp="2026-05-01T12:00:00Z",
                target_content_hash="hash1",
                score=100,
                observation_count=0,
            ),
            RunRecord(
                run_id="run-2",
                timestamp="2026-05-02T12:00:00Z",
                target_content_hash="hash2",
                score=95,
                observation_count=2,
            ),
        ]

        for record in records:
            history.append_run(target_key, record)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "export",
                target_key,
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["run_id"] == "run-1"
        assert data[1]["score"] == 95

    def test_export_csv(self, tmp_path: Path) -> None:
        """Export CSV has header and data rows."""
        state_dir = tmp_path / "state"
        history = HistoryStore(state_dir)

        target_key = "test-target"
        record = RunRecord(
            run_id="run-1",
            timestamp="2026-05-01T12:00:00Z",
            target_content_hash="hash1",
            score=100,
            observation_count=5,
        )
        history.append_run(target_key, record)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "export",
                target_key,
                "--format",
                "csv",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0

        reader = csv.reader(io.StringIO(result.stdout))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0] == ["run_id", "timestamp", "score", "observation_count"]
        assert rows[1] == ["run-1", "2026-05-01T12:00:00Z", "100", "5"]

    def test_export_since_filter(self, tmp_path: Path) -> None:
        """--since filters out older runs."""
        state_dir = tmp_path / "state"
        history = HistoryStore(state_dir)

        target_key = "test-target"
        records = [
            RunRecord(
                run_id="run-old",
                timestamp="2026-04-01T12:00:00Z",
                target_content_hash="hash1",
                score=100,
                observation_count=0,
            ),
            RunRecord(
                run_id="run-new",
                timestamp="2026-05-01T12:00:00Z",
                target_content_hash="hash2",
                score=95,
                observation_count=2,
            ),
        ]

        for record in records:
            history.append_run(target_key, record)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "export",
                target_key,
                "--since",
                "2026-04-15T00:00:00Z",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["run_id"] == "run-new"

    def test_export_no_history(self, tmp_path: Path) -> None:
        """Export with no history exits with error."""
        state_dir = tmp_path / "state"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "export",
                "nonexistent-target",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "No history found" in result.stderr


class TestInspectCommand:
    """Tests for `verdx inspect` command."""

    def test_inspect_basic(self, tmp_path: Path) -> None:
        """Inspect shows run count and size."""
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)

        now = datetime.now(UTC).isoformat()
        store.create_run("run-1", now)
        store.create_run("run-2", now)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "inspect",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Total runs: 2" in result.stdout
        assert "Total size:" in result.stdout

    def test_inspect_json(self, tmp_path: Path) -> None:
        """Inspect --json produces valid JSON."""
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)

        now = datetime.now(UTC).isoformat()
        store.create_run("run-1", now)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "inspect",
                "--json",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["total_runs"] == 1
        assert isinstance(data["total_size_bytes"], int)
        assert isinstance(data["targets"], dict)

    def test_inspect_empty(self, tmp_path: Path) -> None:
        """Inspect on empty directory shows zero counts."""
        state_dir = tmp_path / "state"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "inspect",
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Total runs: 0" in result.stdout
        assert "Total size: 0 bytes" in result.stdout


class TestDeleteRunSecurity:
    """Tests for RunStore.delete_run path traversal protection."""

    def test_delete_run_rejects_path_traversal(self, tmp_path: Path) -> None:
        """delete_run rejects run IDs with path traversal components."""
        store = RunStore(tmp_path)
        with pytest.raises(ValueError, match="Invalid run_id"):
            store.delete_run("../../etc")

    def test_delete_run_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = RunStore(tmp_path)
        assert store.delete_run("nonexistent-run") is False


class TestPruneValidation:
    """Tests for prune argument validation."""

    def test_prune_negative_days_rejected(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--days",
                "-1",
                "--state-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "--days must be a positive integer" in result.stderr

    def test_prune_zero_days_rejected(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--days",
                "0",
                "--state-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "--days must be a positive integer" in result.stderr

    def test_prune_negative_keep_last_rejected(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "verdx.engine",
                "prune",
                "--keep-last",
                "-1",
                "--state-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "--keep-last must be non-negative" in result.stderr
