"""Tests for run storage lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from verdx.engine.runtime.store.runs import RunStatus, RunStore

if TYPE_CHECKING:
    from pathlib import Path


class TestRunStatus:
    def test_valid_construction(self):
        s = RunStatus(
            run_id="run-001",
            status="started",
            timestamp="2026-04-30T12:00:00Z",
        )
        assert s.status == "started"

    def test_invalid_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            RunStatus(
                run_id="run-001",
                status="invalid",
                timestamp="2026-04-30T12:00:00Z",
            )

    def test_empty_run_id(self):
        with pytest.raises(ValueError, match="run_id"):
            RunStatus(
                run_id="",
                status="started",
                timestamp="2026-04-30T12:00:00Z",
            )

    def test_round_trip(self):
        s = RunStatus(
            run_id="run-001",
            status="crashed",
            timestamp="2026-04-30T12:00:00Z",
            error="OOM killed",
        )
        restored = RunStatus.from_dict(s.to_dict())
        assert restored == s


class TestRunStore:
    def test_create_run(self, tmp_path: Path):
        store = RunStore(tmp_path)
        status = store.create_run("run-001", "2026-04-30T12:00:00Z")
        assert status.status == "started"
        assert status.run_id == "run-001"

    def test_create_duplicate_raises(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        with pytest.raises(ValueError, match="already exists"):
            store.create_run("run-001", "2026-04-30T12:01:00Z")

    def test_get_status(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        status = store.get_status("run-001")
        assert status is not None
        assert status.status == "started"

    def test_get_status_nonexistent(self, tmp_path: Path):
        store = RunStore(tmp_path)
        assert store.get_status("missing") is None

    def test_complete_run(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        status = store.complete_run(
            "run-001",
            "2026-04-30T12:05:00Z",
            assessment_json='{"score": 100}',
            report_md="# Report\n\nAll clean.",
        )
        assert status.status == "completed"

    def test_complete_writes_artifacts(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        store.complete_run(
            "run-001",
            "2026-04-30T12:05:00Z",
            assessment_json='{"score": 100}',
            report_md="# Report",
        )
        assert store.get_assessment("run-001") == '{"score": 100}'
        assert store.get_report("run-001") == "# Report"

    def test_complete_writes_subject_coverage_artifact(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        store.complete_run(
            "run-001",
            "2026-04-30T12:05:00Z",
            assessment_json='{"score": 100}',
            report_md="# Report",
            subject_coverage_json='[{"subject": "outline.yml"}]',
        )
        assert store.get_subject_coverage("run-001") == '[{"subject": "outline.yml"}]'

    def test_get_subject_coverage_before_complete(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        assert store.get_subject_coverage("run-001") is None

    def test_crash_run(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        status = store.crash_run(
            "run-001",
            "2026-04-30T12:01:00Z",
            "OOM killed",
        )
        assert status.status == "crashed"
        assert status.error == "OOM killed"

    def test_crash_nonexistent_raises(self, tmp_path: Path):
        store = RunStore(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            store.crash_run("missing", "2026-04-30T12:00:00Z", "error")

    def test_complete_nonexistent_raises(self, tmp_path: Path):
        store = RunStore(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            store.complete_run("missing", "t", "{}", "md")

    def test_get_assessment_before_complete(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        assert store.get_assessment("run-001") is None

    def test_get_report_before_complete(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        assert store.get_report("run-001") is None

    def test_list_runs(self, tmp_path: Path):
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")
        store.create_run("run-002", "2026-04-30T12:01:00Z")
        assert store.list_runs() == ["run-001", "run-002"]

    def test_list_runs_empty(self, tmp_path: Path):
        store = RunStore(tmp_path)
        assert store.list_runs() == []

    def test_status_survives_crash_recovery(self, tmp_path: Path):
        """Started run with no completion remains in started state."""
        store = RunStore(tmp_path)
        store.create_run("run-001", "2026-04-30T12:00:00Z")

        store2 = RunStore(tmp_path)
        status = store2.get_status("run-001")
        assert status is not None
        assert status.status == "started"
