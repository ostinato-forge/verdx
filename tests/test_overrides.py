"""Tests for override loading — file parsing, expiry, validation."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from verdx.overrides import OverrideError, load_overrides


def _write_overrides(data_root, entries: list[dict]) -> None:
    repo_dir = data_root / "repos" / "test-repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "overrides.json").write_text(json.dumps(entries))


class TestLoadOverrides:
    def test_missing_file_returns_empty(self, tmp_path) -> None:
        assert load_overrides("test-repo", data_root=tmp_path) == []

    def test_single_override(self, tmp_path) -> None:
        _write_overrides(
            tmp_path,
            [
                {
                    "signature": "abc123",
                    "decision": "false-positive",
                    "justification": "Not applicable here",
                    "by": "eparenti",
                    "date": "2026-06-19",
                }
            ],
        )
        result = load_overrides("test-repo", data_root=tmp_path)
        assert len(result) == 1
        assert result[0].signature == "abc123"
        assert result[0].decision == "false-positive"

    def test_expired_override_filtered(self, tmp_path) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _write_overrides(
            tmp_path,
            [
                {
                    "signature": "expired",
                    "decision": "deferred",
                    "justification": "next sprint",
                    "by": "eparenti",
                    "date": "2026-01-01",
                    "expires": yesterday,
                }
            ],
        )
        assert load_overrides("test-repo", data_root=tmp_path) == []

    def test_future_expiry_kept(self, tmp_path) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        _write_overrides(
            tmp_path,
            [
                {
                    "signature": "future",
                    "decision": "accepted-risk",
                    "justification": "acceptable for now",
                    "by": "eparenti",
                    "date": "2026-06-19",
                    "expires": tomorrow,
                }
            ],
        )
        result = load_overrides("test-repo", data_root=tmp_path)
        assert len(result) == 1

    def test_no_expiry_kept(self, tmp_path) -> None:
        _write_overrides(
            tmp_path,
            [
                {
                    "signature": "permanent",
                    "decision": "false-positive",
                    "justification": "Known false positive",
                    "by": "eparenti",
                    "date": "2026-06-19",
                }
            ],
        )
        result = load_overrides("test-repo", data_root=tmp_path)
        assert len(result) == 1

    def test_invalid_decision(self, tmp_path) -> None:
        _write_overrides(
            tmp_path,
            [
                {
                    "signature": "sig",
                    "decision": "wontfix",
                    "justification": "j",
                    "by": "me",
                    "date": "2026-01-01",
                }
            ],
        )
        with pytest.raises(OverrideError, match="invalid decision"):
            load_overrides("test-repo", data_root=tmp_path)

    def test_missing_fields(self, tmp_path) -> None:
        _write_overrides(tmp_path, [{"signature": "sig"}])
        with pytest.raises(OverrideError, match="missing fields"):
            load_overrides("test-repo", data_root=tmp_path)

    def test_not_a_list(self, tmp_path) -> None:
        repo_dir = tmp_path / "repos" / "test-repo"
        repo_dir.mkdir(parents=True)
        (repo_dir / "overrides.json").write_text('{"key": "value"}')
        with pytest.raises(OverrideError, match="expected a JSON array"):
            load_overrides("test-repo", data_root=tmp_path)

    def test_malformed_json(self, tmp_path) -> None:
        repo_dir = tmp_path / "repos" / "test-repo"
        repo_dir.mkdir(parents=True)
        (repo_dir / "overrides.json").write_text("not json")
        with pytest.raises(OverrideError, match="cannot read"):
            load_overrides("test-repo", data_root=tmp_path)

    def test_multiple_overrides(self, tmp_path) -> None:
        _write_overrides(
            tmp_path,
            [
                {
                    "signature": "sig1",
                    "decision": "false-positive",
                    "justification": "FP",
                    "by": "a",
                    "date": "2026-01-01",
                },
                {
                    "signature": "sig2",
                    "decision": "accepted-risk",
                    "justification": "Risk accepted",
                    "by": "b",
                    "date": "2026-01-01",
                },
            ],
        )
        result = load_overrides("test-repo", data_root=tmp_path)
        assert len(result) == 2
        assert {r.signature for r in result} == {"sig1", "sig2"}
