"""Tests for disposition storage with expiry and lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.disposition import Disposition
from verdx.engine.runtime.store.dispositions import (
    DispositionStore,
    compute_disposition_summary,
)
from verdx.engine.runtime.store.history import _target_key_hash

if TYPE_CHECKING:
    from pathlib import Path


def _disp(**overrides) -> Disposition:
    from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION

    defaults = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "issue_fingerprint": "fp-001",
        "decision": "accepted-risk",
        "reason": "Known limitation",
        "team": "training-content",
        "timestamp": "2026-04-30T12:00:00Z",
    }
    return Disposition(**(defaults | overrides))


class TestDispositionStore:
    def test_append_and_read(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(tkh, _disp())
        all_disps = store.get_all(tkh)
        assert len(all_disps) == 1
        assert all_disps[0].decision == "accepted-risk"

    def test_empty_returns_empty(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        assert store.get_all(tkh) == []

    def test_active_excludes_expired(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-expired",
                expires="2026-04-01T00:00:00Z",
            ),
        )
        store.append(
            tkh,
            _disp(issue_fingerprint="fp-active"),
        )
        active = store.get_active(tkh, "2026-04-30T12:00:00Z")
        assert "fp-active" in active
        assert "fp-expired" not in active

    def test_active_includes_no_expiry(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(tkh, _disp(issue_fingerprint="fp-forever"))
        active = store.get_active(tkh, "2099-01-01T00:00:00Z")
        assert "fp-forever" in active

    def test_latest_disposition_wins(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-1",
                decision="accepted-risk",
                timestamp="2026-04-01T00:00:00Z",
            ),
        )
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-1",
                decision="false-positive",
                timestamp="2026-04-15T00:00:00Z",
            ),
        )
        active = store.get_active(tkh, "2026-04-30T12:00:00Z")
        assert active["fp-1"].decision == "false-positive"


class TestTimestampComparison:
    """Test timezone and malformed timestamp handling in _is_expired()."""

    def test_z_and_offset_compare_correctly(self, tmp_path: Path):
        """Verify Z and +00:00 timestamps compare as equal (both UTC)."""
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        # Disposition expires at midnight UTC using Z notation
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-z-notation",
                expires="2026-05-01T00:00:00Z",
            ),
        )
        # Current time is also midnight UTC using +00:00 notation
        active = store.get_active(tkh, "2026-05-01T00:00:00+00:00")
        # Should be expired (equal timestamps = expired)
        assert "fp-z-notation" not in active

    def test_z_and_offset_not_expired(self, tmp_path: Path):
        """Verify Z and +00:00 timestamps compare correctly when not expired."""
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        # Disposition expires tomorrow using +00:00 notation
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-offset-notation",
                expires="2026-05-02T00:00:00+00:00",
            ),
        )
        # Current time is today using Z notation
        active = store.get_active(tkh, "2026-05-01T00:00:00Z")
        # Should still be active
        assert "fp-offset-notation" in active

    def test_malformed_expires_does_not_crash(self, tmp_path: Path):
        """Malformed expires timestamp returns False (not expired)."""
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        # Manually create a disposition with malformed expires
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-malformed",
                expires="not-a-timestamp",
            ),
        )
        # Should not crash, should treat as active (not expired)
        active = store.get_active(tkh, "2026-05-01T00:00:00Z")
        assert "fp-malformed" in active

    def test_malformed_current_timestamp_does_not_crash(self, tmp_path: Path):
        """Malformed current_timestamp returns False (not expired)."""
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-valid",
                expires="2026-05-01T00:00:00Z",
            ),
        )
        # Should not crash with malformed current_timestamp
        active = store.get_active(tkh, "bad-timestamp")
        # Treats disposition as active (not expired) on parse failure
        assert "fp-valid" in active


class TestExpiringDispositions:
    def test_expiring_soon(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-1",
                expires="2026-05-15T00:00:00Z",
            ),
        )
        expiring = store.get_expiring_soon(
            tkh,
            "2026-04-30T12:00:00Z",
            horizon_days=30,
        )
        assert len(expiring) == 1

    def test_not_expiring_soon(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-1",
                expires="2026-12-31T00:00:00Z",
            ),
        )
        expiring = store.get_expiring_soon(
            tkh,
            "2026-04-30T12:00:00Z",
            horizon_days=30,
        )
        assert len(expiring) == 0

    def test_already_expired_not_in_expiring_soon(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(
            tkh,
            _disp(
                issue_fingerprint="fp-1",
                expires="2026-04-01T00:00:00Z",
            ),
        )
        expiring = store.get_expiring_soon(
            tkh,
            "2026-04-30T12:00:00Z",
            horizon_days=30,
        )
        assert len(expiring) == 0

    def test_no_expiry_not_in_expiring_soon(self, tmp_path: Path):
        store = DispositionStore(tmp_path)
        tkh = _target_key_hash("target-A")
        store.append(tkh, _disp(issue_fingerprint="fp-1"))
        expiring = store.get_expiring_soon(
            tkh,
            "2026-04-30T12:00:00Z",
            horizon_days=30,
        )
        assert len(expiring) == 0


class TestDispositionSummary:
    def test_empty(self):
        summary = compute_disposition_summary({}, [])
        assert summary["active_count"] == 0
        assert summary["by_decision"] == {}
        assert summary["expiring_soon_count"] == 0

    def test_with_active(self):
        active = {
            "fp-1": _disp(decision="accepted-risk"),
            "fp-2": _disp(decision="false-positive"),
            "fp-3": _disp(decision="accepted-risk"),
        }
        summary = compute_disposition_summary(active, [_disp()])
        assert summary["active_count"] == 3
        assert summary["by_decision"]["accepted-risk"] == 2
        assert summary["by_decision"]["false-positive"] == 1
        assert summary["expiring_soon_count"] == 1
