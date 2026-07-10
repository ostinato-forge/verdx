"""Tests for history storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.engine.runtime.store.history import (
    HistoryStore,
    ObservationRecord,
    RunRecord,
)

if TYPE_CHECKING:
    from pathlib import Path


def _run_record(**overrides) -> RunRecord:
    defaults = {
        "run_id": "run-001",
        "timestamp": "2026-04-30T12:00:00Z",
        "target_content_hash": "sha256:abc123",
        "score": 85,
        "observation_count": 3,
    }
    return RunRecord(**(defaults | overrides))


def _obs_record(**overrides) -> ObservationRecord:
    defaults = {
        "run_id": "run-001",
        "observation_id": "obs:run-001:abcdef",
        "issue_fingerprint": "qu:v1:abcdef",
        "target_content_hash": "sha256:abc123",
        "rule_id": "yaml-syntax",
        "category": "correctness",
        "severity": "high",
        "escalation": "L3",
    }
    return ObservationRecord(**(defaults | overrides))


class TestRunRecord:
    def test_round_trip(self):
        r = _run_record()
        assert RunRecord.from_dict(r.to_dict()) == r


class TestObservationRecord:
    def test_round_trip(self):
        o = _obs_record()
        assert ObservationRecord.from_dict(o.to_dict()) == o


class TestHistoryStore:
    def test_append_and_read_runs(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("sample/ch04/ge01", _run_record())
        runs = store.get_runs("sample/ch04/ge01")
        assert len(runs) == 1
        assert runs[0].run_id == "run-001"

    def test_append_multiple_runs(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("target-A", _run_record(run_id="run-001"))
        store.append_run("target-A", _run_record(run_id="run-002"))
        runs = store.get_runs("target-A")
        assert len(runs) == 2

    def test_different_targets_isolated(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("target-A", _run_record(run_id="run-001"))
        store.append_run("target-B", _run_record(run_id="run-002"))
        assert len(store.get_runs("target-A")) == 1
        assert len(store.get_runs("target-B")) == 1

    def test_empty_target_returns_empty(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        assert store.get_runs("nonexistent") == []
        assert store.get_observations("nonexistent") == []

    def test_append_and_read_observations(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_observations(
            "target-A",
            [_obs_record(issue_fingerprint="fp-1")],
        )
        obs = store.get_observations("target-A")
        assert len(obs) == 1
        assert obs[0].issue_fingerprint == "fp-1"

    def test_append_empty_observations_no_op(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_observations("target-A", [])
        assert store.get_observations("target-A") == []

    def test_history_keyed_by_target_key_not_content(self, tmp_path: Path):
        """Same target_key with different content hashes shares history."""
        store = HistoryStore(tmp_path)
        store.append_run(
            "target-A",
            _run_record(
                run_id="run-001",
                target_content_hash="sha256:v1",
            ),
        )
        store.append_run(
            "target-A",
            _run_record(
                run_id="run-002",
                target_content_hash="sha256:v2",
            ),
        )
        runs = store.get_runs("target-A")
        assert len(runs) == 2
        assert runs[0].target_content_hash == "sha256:v1"
        assert runs[1].target_content_hash == "sha256:v2"


class TestPreviousFingerprints:
    def test_no_history_returns_none(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        assert store.get_previous_fingerprints("target-A") is None

    def test_single_run_returns_fingerprints(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("target-A", _run_record(run_id="run-001"))
        store.append_observations(
            "target-A",
            [
                _obs_record(
                    run_id="run-001",
                    issue_fingerprint="fp-1",
                ),
                _obs_record(
                    run_id="run-001",
                    issue_fingerprint="fp-2",
                ),
            ],
        )
        fps = store.get_previous_fingerprints("target-A")
        assert fps == {"fp-1", "fp-2"}

    def test_returns_latest_run_fingerprints(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("target-A", _run_record(run_id="run-001"))
        store.append_observations(
            "target-A",
            [_obs_record(run_id="run-001", issue_fingerprint="fp-old")],
        )
        store.append_run("target-A", _run_record(run_id="run-002"))
        store.append_observations(
            "target-A",
            [_obs_record(run_id="run-002", issue_fingerprint="fp-new")],
        )
        fps = store.get_previous_fingerprints("target-A")
        assert fps == {"fp-new"}

    def test_exclude_current_run(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("target-A", _run_record(run_id="run-001"))
        store.append_observations(
            "target-A",
            [_obs_record(run_id="run-001", issue_fingerprint="fp-old")],
        )
        store.append_run("target-A", _run_record(run_id="run-002"))
        store.append_observations(
            "target-A",
            [_obs_record(run_id="run-002", issue_fingerprint="fp-new")],
        )
        fps = store.get_previous_fingerprints(
            "target-A",
            exclude_run_id="run-002",
        )
        assert fps == {"fp-old"}

    def test_exclude_only_run_returns_none(self, tmp_path: Path):
        store = HistoryStore(tmp_path)
        store.append_run("target-A", _run_record(run_id="run-001"))
        store.append_observations(
            "target-A",
            [_obs_record(run_id="run-001", issue_fingerprint="fp-1")],
        )
        fps = store.get_previous_fingerprints(
            "target-A",
            exclude_run_id="run-001",
        )
        assert fps is None
