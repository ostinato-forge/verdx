"""Diagnostic log: append-only event log with lifecycle controls.

The diagnostic log tracks validation run progress for operators.
It must never fail the validation run, even on I/O errors.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from verdx.contracts.diagnostic import DiagnosticEvent


class DiagnosticLog:
    """Append-only diagnostic event log with lifecycle controls."""

    def __init__(self, state_dir: Path, run_id: str, *, enabled: bool = True):
        """Create diagnostic log.

        Creates log file at state_dir/diagnostic/<run_id>.jsonl
        If not enabled, all methods are no-ops.
        """
        self._enabled = enabled
        self._run_id = run_id
        self._log_path: Path | None = None
        if enabled:
            self._log_path = state_dir / "diagnostic" / f"{run_id}.jsonl"
            self._log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def emit(self, event: DiagnosticEvent) -> None:
        """Append event atomically. Never raises — failures are silently swallowed."""
        if not self._enabled or self._log_path is None:
            return

        try:
            line = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except Exception:
            # Diagnostic logging failures must never fail the validation run
            pass

    def read_events(self) -> list[DiagnosticEvent]:
        """Read all events for this run."""
        if not self._enabled or self._log_path is None:
            return []

        try:
            from verdx.contracts.diagnostic import DiagnosticEvent

            if not self._log_path.exists():
                return []

            events = []
            with open(self._log_path, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        data = json.loads(stripped)
                        events.append(DiagnosticEvent.from_dict(data))
                    except (json.JSONDecodeError, KeyError, ValueError):
                        # Skip malformed lines
                        continue
            return events
        except Exception:
            return []

    def cleanup(self, *, max_age_days: int = 7) -> int:
        """Remove diagnostic logs older than max_age_days. Returns count removed."""
        if not self._enabled or self._log_path is None:
            return 0

        try:
            diagnostic_dir = self._log_path.parent
            if not diagnostic_dir.exists():
                return 0

            cutoff_time = time.time() - (max_age_days * 86400)
            removed = 0

            for log_file in diagnostic_dir.glob("*.jsonl"):
                try:
                    mtime = log_file.stat().st_mtime
                    if mtime < cutoff_time:
                        with contextlib.suppress(OSError):
                            log_file.unlink()
                            removed += 1
                except OSError:
                    continue

            return removed
        except Exception:
            return 0
