"""Disposition storage: append-only with expiry and lookup.

Dispositions are stored per target_key in JSONL format.
Active disposition lookup filters by expiry and returns
the most recent non-expired entry per fingerprint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from verdx.contracts.disposition import Disposition
from verdx.engine.runtime.store.jsonl import append_jsonl, read_jsonl

if TYPE_CHECKING:
    from pathlib import Path


class DispositionStore:
    """File-backed disposition storage."""

    def __init__(self, state_root: Path) -> None:
        self._history_dir = state_root / "history"

    def _dispositions_path(self, target_key_hash: str) -> Path:
        return self._history_dir / target_key_hash / "dispositions.jsonl"

    def append(
        self,
        target_key_hash: str,
        disposition: Disposition,
    ) -> None:
        """Append a disposition record."""
        path = self._dispositions_path(target_key_hash)
        append_jsonl(path, [disposition.to_dict()])

    def get_all(self, target_key_hash: str) -> list[Disposition]:
        """Read all dispositions for a target (including expired)."""
        path = self._dispositions_path(target_key_hash)
        return [Disposition.from_dict(d) for d in read_jsonl(path)]

    def get_active(
        self,
        target_key_hash: str,
        current_timestamp: str,
    ) -> dict[str, Disposition]:
        """Get active (non-expired) dispositions keyed by fingerprint.

        For each fingerprint, returns the most recent non-expired
        disposition. Later entries override earlier ones.
        """
        all_disps = self.get_all(target_key_hash)
        active: dict[str, Disposition] = {}

        for disp in all_disps:
            if _is_expired(disp, current_timestamp):
                continue
            active[disp.issue_fingerprint] = disp

        return active

    def get_expiring_soon(
        self,
        target_key_hash: str,
        current_timestamp: str,
        horizon_days: int = 30,
    ) -> list[Disposition]:
        """Get dispositions that will expire within horizon_days."""
        all_disps = self.get_all(target_key_hash)
        result: list[Disposition] = []

        for disp in all_disps:
            if disp.expires is None:
                continue
            if _is_expired(disp, current_timestamp):
                continue
            if _expires_within(disp, current_timestamp, horizon_days):
                result.append(disp)

        return result


def _is_expired(disp: Disposition, current_timestamp: str) -> bool:
    """Check if a disposition has expired using proper datetime comparison."""
    if disp.expires is None:
        return False

    try:
        from datetime import datetime

        current = datetime.fromisoformat(
            current_timestamp.replace("Z", "+00:00"),
        )
        expires = datetime.fromisoformat(
            disp.expires.replace("Z", "+00:00"),
        )
        return expires <= current
    except (ValueError, TypeError):
        return False


def _expires_within(
    disp: Disposition,
    current_timestamp: str,
    horizon_days: int,
) -> bool:
    """Check if disposition expires within horizon_days of current_timestamp.

    Uses simple date arithmetic on ISO 8601 date strings.
    """
    if disp.expires is None:
        return False

    try:
        from datetime import datetime, timedelta

        current = datetime.fromisoformat(
            current_timestamp.replace("Z", "+00:00"),
        )
        expires = datetime.fromisoformat(
            disp.expires.replace("Z", "+00:00"),
        )
        horizon = current + timedelta(days=horizon_days)
        return expires <= horizon
    except (ValueError, TypeError):
        return False


def compute_disposition_summary(
    active: dict[str, Disposition],
    expiring_soon: list[Disposition],
) -> dict[str, Any]:
    """Compute a summary of disposition state for reporting."""
    by_decision: dict[str, int] = {}
    for disp in active.values():
        by_decision[disp.decision] = by_decision.get(disp.decision, 0) + 1

    return {
        "active_count": len(active),
        "by_decision": by_decision,
        "expiring_soon_count": len(expiring_soon),
    }
