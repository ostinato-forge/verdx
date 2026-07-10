"""Tests for the CLI commands (report, history, disposition)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION
from verdx.engine.runtime.cli import main
from verdx.engine.runtime.store.dispositions import DispositionStore
from verdx.engine.runtime.store.history import HistoryStore, RunRecord, _target_key_hash
from verdx.engine.runtime.store.runs import RunStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path / "state"


@pytest.fixture()
def populated_run_store(state_dir):
    store = RunStore(state_dir)
    store.create_run("run-001", "2026-04-30T12:00:00Z")
    store.complete_run(
        "run-001",
        "2026-04-30T12:01:00Z",
        assessment_json='{"score": 85}',
        report_md="# Assessment: run-001\nScore: 85/100\n",
    )
    return store


@pytest.fixture()
def populated_history(state_dir):
    history = HistoryStore(state_dir)
    for i in range(3):
        history.append_run(
            "my/target",
            RunRecord(
                run_id=f"run-{i:03d}",
                timestamp=f"2026-04-30T12:{i:02d}:00Z",
                target_content_hash=f"sha256:abc{i}",
                score=100 - i * 10,
                observation_count=i,
            ),
        )
    return history


# ===========================================================================
# No command → help
# ===========================================================================


class TestMainDispatch:
    def test_no_command_prints_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "verdx" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output


# ===========================================================================
# report
# ===========================================================================


class TestCmdReport:
    def test_report_markdown(self, runner, state_dir, populated_run_store):
        result = runner.invoke(main, ["report", "run-001", "--state-dir", str(state_dir)])
        assert result.exit_code == 0
        assert "Assessment" in result.output
        assert "85" in result.output

    def test_report_json(self, runner, state_dir, populated_run_store):
        result = runner.invoke(main, ["report", "run-001", "--json", "--state-dir", str(state_dir)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["score"] == 85

    def test_report_missing_run(self, runner, state_dir):
        result = runner.invoke(main, ["report", "nonexistent", "--state-dir", str(state_dir)])
        assert result.exit_code == 1
        assert "No report found" in result.output

    def test_report_json_missing(self, runner, state_dir):
        result = runner.invoke(
            main, ["report", "nonexistent", "--json", "--state-dir", str(state_dir)]
        )
        assert result.exit_code == 1
        assert "No assessment found" in result.output


# ===========================================================================
# history
# ===========================================================================


class TestCmdHistory:
    def test_history_text(self, runner, state_dir, populated_history):
        result = runner.invoke(main, ["history", "my/target", "--state-dir", str(state_dir)])
        assert result.exit_code == 0
        assert "History for: my/target" in result.output
        assert "Total runs: 3" in result.output
        assert "run-000" in result.output
        assert "score=100" in result.output

    def test_history_json(self, runner, state_dir, populated_history):
        result = runner.invoke(
            main, ["history", "my/target", "--json", "--state-dir", str(state_dir)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 3
        assert data[0]["run_id"] == "run-000"

    def test_history_missing_target(self, runner, state_dir):
        result = runner.invoke(main, ["history", "no/such/target", "--state-dir", str(state_dir)])
        assert result.exit_code == 1
        assert "No history" in result.output

    def test_history_target_key_override(self, runner, state_dir, populated_history):
        result = runner.invoke(
            main,
            [
                "history",
                "ignored-positional",
                "--target-key",
                "my/target",
                "--state-dir",
                str(state_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Total runs: 3" in result.output

    def test_history_shows_last_10(self, runner, state_dir):
        history = HistoryStore(state_dir)
        for i in range(15):
            history.append_run(
                "big/target",
                RunRecord(
                    run_id=f"run-{i:03d}",
                    timestamp=f"2026-04-30T12:{i:02d}:00Z",
                    target_content_hash=f"sha256:x{i}",
                    score=90,
                    observation_count=0,
                ),
            )
        result = runner.invoke(main, ["history", "big/target", "--state-dir", str(state_dir)])
        assert result.exit_code == 0
        assert "Total runs: 15" in result.output
        assert "run-005" in result.output
        assert "run-004" not in result.output


# ===========================================================================
# disposition
# ===========================================================================


class TestCmdDisposition:
    def test_add_disposition(self, runner, state_dir):
        result = runner.invoke(
            main,
            [
                "disposition",
                "add",
                "fp-abc123",
                "--target-key",
                "my/target",
                "--decision",
                "accepted-risk",
                "--reason",
                "Known issue",
                "--team",
                "training",
                "--state-dir",
                str(state_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Disposition added for fp-abc123" in result.output
        assert "accepted-risk" in result.output
        assert "training" in result.output

        tkh = _target_key_hash("my/target")
        store = DispositionStore(state_dir)
        all_disps = store.get_all(tkh)
        assert len(all_disps) == 1
        assert all_disps[0].issue_fingerprint == "fp-abc123"
        assert all_disps[0].decision == "accepted-risk"

    def test_add_disposition_with_expiry(self, runner, state_dir):
        result = runner.invoke(
            main,
            [
                "disposition",
                "add",
                "fp-xyz",
                "--target-key",
                "t",
                "--decision",
                "deferred",
                "--reason",
                "Later",
                "--team",
                "ops",
                "--expires",
                "2026-12-31T23:59:59Z",
                "--state-dir",
                str(state_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Expires: 2026-12-31T23:59:59Z" in result.output

    def test_disposition_no_subcommand(self, runner):
        result = runner.invoke(main, ["disposition"])
        assert result.exit_code != 0

    def test_disposition_schema_version(self, runner, state_dir):
        result = runner.invoke(
            main,
            [
                "disposition",
                "add",
                "fp-v",
                "--target-key",
                "t",
                "--decision",
                "false-positive",
                "--reason",
                "Not real",
                "--team",
                "qa",
                "--state-dir",
                str(state_dir),
            ],
        )
        assert result.exit_code == 0
        tkh = _target_key_hash("t")
        store = DispositionStore(state_dir)
        disp = store.get_all(tkh)[0]
        assert disp.schema_version == DISPOSITION_SCHEMA_VERSION
