"""Tests for diagnostic log storage."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from verdx.contracts.diagnostic import DiagnosticEvent
from verdx.engine.runtime.store.diagnostic import DiagnosticLog

if TYPE_CHECKING:
    from pathlib import Path


class TestDiagnosticLog:
    def test_emit_and_read_roundtrip(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-start",
            validator_id="yaml-syntax",
            status="started",
            duration_ms=None,
            message="Starting validator",
            context={"target": "test.yaml"},
        )
        log.emit(event)

        events = log.read_events()
        assert len(events) == 1
        assert events[0].timestamp == "2026-05-02T10:00:00Z"
        assert events[0].run_id == "run-123"
        assert events[0].phase == "validator-start"
        assert events[0].validator_id == "yaml-syntax"
        assert events[0].status == "started"
        assert events[0].duration_ms is None

    def test_emit_multiple_events(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)

        event1 = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="run-start",
            validator_id=None,
            status="started",
            duration_ms=None,
            message="Run started",
            context={},
        )
        event2 = DiagnosticEvent(
            timestamp="2026-05-02T10:00:10Z",
            run_id="run-123",
            phase="validator-start",
            validator_id="yaml-syntax",
            status="started",
            duration_ms=None,
            message="Validator started",
            context={},
        )
        event3 = DiagnosticEvent(
            timestamp="2026-05-02T10:00:20Z",
            run_id="run-123",
            phase="validator-complete",
            validator_id="yaml-syntax",
            status="completed",
            duration_ms=250,
            message="Validator completed",
            context={"checks_run": 10},
        )

        log.emit(event1)
        log.emit(event2)
        log.emit(event3)

        events = log.read_events()
        assert len(events) == 3
        assert events[0].phase == "run-start"
        assert events[1].phase == "validator-start"
        assert events[2].phase == "validator-complete"
        assert events[2].duration_ms == 250

    def test_emit_never_raises(self, tmp_path: Path):
        # Create log with invalid path that will fail on write
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)

        # Make the log file directory read-only after creation
        log_file = tmp_path / "diagnostic" / "run-123.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.parent.chmod(0o000)

        try:
            event = DiagnosticEvent(
                timestamp="2026-05-02T10:00:00Z",
                run_id="run-123",
                phase="test",
                validator_id=None,
                status="started",
                duration_ms=None,
                message="Test",
                context={},
            )
            # This should not raise, even though the write will fail
            log.emit(event)
        finally:
            # Restore permissions for cleanup
            log_file.parent.chmod(0o700)

    def test_disabled_log_is_noop(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=False)

        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-start",
            validator_id="yaml-syntax",
            status="started",
            duration_ms=None,
            message="Starting validator",
            context={},
        )
        log.emit(event)

        # No file should be created
        assert not (tmp_path / "diagnostic" / "run-123.jsonl").exists()

        # Read should return empty list
        events = log.read_events()
        assert events == []

    def test_read_events_nonexistent_file(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)
        events = log.read_events()
        assert events == []

    def test_read_events_skips_malformed_lines(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)

        # Write one good event
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="test",
            validator_id=None,
            status="started",
            duration_ms=None,
            message="Test",
            context={},
        )
        log.emit(event)

        # Manually append malformed line
        log_file = tmp_path / "diagnostic" / "run-123.jsonl"
        with open(log_file, "a") as f:
            f.write("{malformed\n")

        # Should still read the good event, skip the malformed one
        events = log.read_events()
        assert len(events) == 1
        assert events[0].phase == "test"

    def test_cleanup_removes_old_files(self, tmp_path: Path):
        # Create multiple log files with different ages
        diag_dir = tmp_path / "diagnostic"
        diag_dir.mkdir(parents=True, exist_ok=True)

        old_file = diag_dir / "old-run.jsonl"
        recent_file = diag_dir / "recent-run.jsonl"

        old_file.write_text('{"test": 1}\n')
        recent_file.write_text('{"test": 2}\n')

        # Make old file look 10 days old
        old_time = time.time() - (10 * 86400)
        old_file.stat()
        import os

        os.utime(old_file, (old_time, old_time))

        # Create a log and clean up files older than 7 days
        log = DiagnosticLog(tmp_path, "current-run", enabled=True)
        removed = log.cleanup(max_age_days=7)

        assert removed == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_cleanup_preserves_recent_files(self, tmp_path: Path):
        diag_dir = tmp_path / "diagnostic"
        diag_dir.mkdir(parents=True, exist_ok=True)

        recent_file = diag_dir / "recent-run.jsonl"
        recent_file.write_text('{"test": 1}\n')

        log = DiagnosticLog(tmp_path, "current-run", enabled=True)
        removed = log.cleanup(max_age_days=7)

        assert removed == 0
        assert recent_file.exists()

    def test_cleanup_disabled_log(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=False)
        removed = log.cleanup(max_age_days=7)
        assert removed == 0

    def test_atomic_append(self, tmp_path: Path):
        """Multiple events should all be readable."""
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)

        for i in range(5):
            event = DiagnosticEvent(
                timestamp=f"2026-05-02T10:00:{i:02d}Z",
                run_id="run-123",
                phase=f"phase-{i}",
                validator_id=None,
                status="started",
                duration_ms=None,
                message=f"Message {i}",
                context={},
            )
            log.emit(event)

        events = log.read_events()
        assert len(events) == 5
        for i, event in enumerate(events):
            assert event.phase == f"phase-{i}"
            assert event.message == f"Message {i}"

    def test_log_file_path_structure(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="test",
            validator_id=None,
            status="started",
            duration_ms=None,
            message="Test",
            context={},
        )
        log.emit(event)

        expected_path = tmp_path / "diagnostic" / "run-123.jsonl"
        assert expected_path.exists()
        assert expected_path.parent.name == "diagnostic"

    def test_read_events_with_io_error_returns_empty(self, tmp_path: Path):
        log = DiagnosticLog(tmp_path, "run-123", enabled=True)

        # Emit an event first
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="test",
            validator_id=None,
            status="started",
            duration_ms=None,
            message="Test",
            context={},
        )
        log.emit(event)

        # Make the log file unreadable
        log_file = tmp_path / "diagnostic" / "run-123.jsonl"
        log_file.chmod(0o000)

        try:
            # Should return empty list instead of raising
            events = log.read_events()
            assert events == []
        finally:
            # Restore permissions for cleanup
            log_file.chmod(0o600)

    def test_cleanup_handles_permission_errors(self, tmp_path: Path):
        diag_dir = tmp_path / "diagnostic"
        diag_dir.mkdir(parents=True, exist_ok=True)

        old_file = diag_dir / "old-run.jsonl"
        old_file.write_text('{"test": 1}\n')

        # Make file old
        old_time = time.time() - (10 * 86400)
        import os

        os.utime(old_file, (old_time, old_time))

        # Make directory read-only so deletion fails
        diag_dir.chmod(0o500)

        try:
            log = DiagnosticLog(tmp_path, "current-run", enabled=True)
            # Should not raise, even if cleanup fails
            log.cleanup(max_age_days=7)
            # File should still exist because deletion failed
            assert old_file.exists()
        finally:
            # Restore permissions
            diag_dir.chmod(0o700)
