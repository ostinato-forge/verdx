"""Tests for subject coverage history storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    SUBJECT_SCHEMA_VERSION,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
)
from verdx.engine.runtime.store.subject_history import (
    SubjectCoverageHistoryStore,
    SubjectCoverageSnapshot,
)

if TYPE_CHECKING:
    from pathlib import Path


def _snapshot(**overrides) -> SubjectCoverageSnapshot:
    defaults = {
        "run_id": "run-001",
        "timestamp": "2026-05-01T12:00:00Z",
        "target_key": "sample/ch04/ge01",
        "target_content_hash": "sha256:abc123",
        "subject_key": "exercise:ch04/ge01",
        "subject_kind": "exercise",
        "subject_label": "GE: Configuring Automation Controller",
        "state": "complete_clean",
        "criticality": "critical",
        "observation_count": 0,
        "checks_run": 5,
        "checks_passed": 5,
        "coverage_gap_validator_ids": (),
        "blocking_reason": None,
        "has_model_debt": False,
    }
    return SubjectCoverageSnapshot(**(defaults | overrides))


def _coverage_record(**overrides) -> SubjectCoverageRecord:
    """Build a full SubjectCoverageRecord for from_record tests."""
    defaults = {
        "schema_version": SUBJECT_COVERAGE_SCHEMA_VERSION,
        "run_id": "run-042",
        "target_key": "sample/ch04/ge01",
        "target_content_hash": "sha256:deadbeef",
        "subject": ValidationSubject(
            schema_version=SUBJECT_SCHEMA_VERSION,
            subject_key="exercise:ch04/ge01",
            target_key="sample/ch04/ge01",
            kind="exercise",
            label="GE: Configuring Automation Controller",
            selector=SubjectSelector(kind="all"),
        ),
        "state": SubjectCoverageState.COMPLETE_WITH_OBSERVATIONS,
        "criticality": "critical",
        "required_validator_ids": ("v-yaml", "v-adoc"),
        "completed_validator_ids": ("v-yaml",),
        "failed_validator_ids": (),
        "skipped_validator_ids": ("v-adoc",),
        "checks_run": 10,
        "checks_passed": 8,
        "observation_fingerprints": ("fp-1", "fp-2", "fp-3"),
        "coverage_gap_validator_ids": ("v-adoc",),
        "blocking_reason": "missing content",
        "model_health": SubjectModelHealth(
            parser_warnings=("warn-1",),
        ),
        "metadata": {},
    }
    return SubjectCoverageRecord(**(defaults | overrides))


class TestSubjectCoverageSnapshot:
    def test_round_trip(self):
        s = _snapshot()
        assert SubjectCoverageSnapshot.from_dict(s.to_dict()) == s

    def test_round_trip_with_blocking_reason(self):
        s = _snapshot(blocking_reason="content not found")
        restored = SubjectCoverageSnapshot.from_dict(s.to_dict())
        assert restored == s
        assert restored.blocking_reason == "content not found"

    def test_to_dict_omits_none_blocking_reason(self):
        s = _snapshot(blocking_reason=None)
        d = s.to_dict()
        assert "blocking_reason" not in d

    def test_to_dict_includes_blocking_reason_when_set(self):
        s = _snapshot(blocking_reason="deadline")
        d = s.to_dict()
        assert d["blocking_reason"] == "deadline"

    def test_from_record(self):
        record = _coverage_record()
        snap = SubjectCoverageSnapshot.from_record(record, "2026-05-01T14:00:00Z")
        assert snap.run_id == "run-042"
        assert snap.timestamp == "2026-05-01T14:00:00Z"
        assert snap.target_key == "sample/ch04/ge01"
        assert snap.target_content_hash == "sha256:deadbeef"
        assert snap.subject_key == "exercise:ch04/ge01"
        assert snap.subject_kind == "exercise"
        assert snap.subject_label == "GE: Configuring Automation Controller"
        assert snap.state == "complete_with_observations"
        assert snap.criticality == "critical"
        assert snap.observation_count == 3
        assert snap.checks_run == 10
        assert snap.checks_passed == 8
        assert snap.coverage_gap_validator_ids == ("v-adoc",)
        assert snap.blocking_reason == "missing content"
        assert snap.has_model_debt is True

    def test_from_record_no_debt(self):
        record = _coverage_record(
            model_health=SubjectModelHealth(),
            blocking_reason=None,
        )
        snap = SubjectCoverageSnapshot.from_record(record, "2026-05-01T14:00:00Z")
        assert snap.has_model_debt is False
        assert snap.blocking_reason is None


class TestSubjectCoverageHistoryStore:
    def test_append_and_read_round_trip(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        snap = _snapshot()
        store.append("sample/ch04/ge01", [snap])
        result = store.get_all("sample/ch04/ge01")
        assert len(result) == 1
        assert result[0] == snap

    def test_empty_target_returns_empty(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        assert store.get_all("nonexistent") == []

    def test_append_empty_list_is_no_op(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append("target-A", [])
        assert store.get_all("target-A") == []

    def test_multiple_appends_accumulate(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append("target-A", [_snapshot(run_id="run-001")])
        store.append("target-A", [_snapshot(run_id="run-002")])
        result = store.get_all("target-A")
        assert len(result) == 2
        assert result[0].run_id == "run-001"
        assert result[1].run_id == "run-002"

    def test_different_targets_isolated(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append("target-A", [_snapshot(run_id="run-001")])
        store.append("target-B", [_snapshot(run_id="run-002")])
        assert len(store.get_all("target-A")) == 1
        assert len(store.get_all("target-B")) == 1

    def test_get_by_subject(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append(
            "target-A",
            [
                _snapshot(subject_key="exercise:ch04/ge01", run_id="run-001"),
                _snapshot(subject_key="chapter:ch04", run_id="run-001"),
                _snapshot(subject_key="exercise:ch04/ge01", run_id="run-002"),
            ],
        )
        result = store.get_by_subject("target-A", "exercise:ch04/ge01")
        assert len(result) == 2
        assert all(s.subject_key == "exercise:ch04/ge01" for s in result)

    def test_get_by_subject_empty(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append("target-A", [_snapshot(subject_key="exercise:ch04/ge01")])
        result = store.get_by_subject("target-A", "nonexistent")
        assert result == []

    def test_get_by_subject_missing_target(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        assert store.get_by_subject("nonexistent", "exercise:ch04/ge01") == []

    def test_get_latest_by_subject(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append(
            "target-A",
            [
                _snapshot(
                    subject_key="exercise:ch04/ge01",
                    run_id="run-001",
                    state="partial",
                ),
                _snapshot(
                    subject_key="chapter:ch04",
                    run_id="run-001",
                    state="missing",
                ),
            ],
        )
        store.append(
            "target-A",
            [
                _snapshot(
                    subject_key="exercise:ch04/ge01",
                    run_id="run-002",
                    state="complete_clean",
                ),
            ],
        )
        latest = store.get_latest_by_subject("target-A")
        assert len(latest) == 2
        assert latest["exercise:ch04/ge01"].run_id == "run-002"
        assert latest["exercise:ch04/ge01"].state == "complete_clean"
        assert latest["chapter:ch04"].run_id == "run-001"
        assert latest["chapter:ch04"].state == "missing"

    def test_get_latest_by_subject_empty(self, tmp_path: Path):
        store = SubjectCoverageHistoryStore(tmp_path)
        assert store.get_latest_by_subject("nonexistent") == {}

    def test_multiple_runs_show_state_changes(self, tmp_path: Path):
        """Cross-run history tracks state transitions for trending."""
        store = SubjectCoverageHistoryStore(tmp_path)
        subject_key = "exercise:ch04/ge01"
        states = ["missing", "partial", "complete_with_observations", "complete_clean"]
        for i, state in enumerate(states):
            store.append(
                "target-A",
                [_snapshot(subject_key=subject_key, run_id=f"run-{i:03d}", state=state)],
            )
        history = store.get_by_subject("target-A", subject_key)
        assert [s.state for s in history] == states

    def test_batch_append_multiple_subjects(self, tmp_path: Path):
        """A single append can write snapshots for multiple subjects at once."""
        store = SubjectCoverageHistoryStore(tmp_path)
        store.append(
            "target-A",
            [
                _snapshot(subject_key="exercise:ch04/ge01"),
                _snapshot(subject_key="exercise:ch04/ge02"),
                _snapshot(subject_key="chapter:ch04"),
            ],
        )
        assert len(store.get_all("target-A")) == 3


class TestPrivacySafeSerialization:
    """Verify snapshots contain no individual-identifying information."""

    def test_snapshot_fields_contain_no_producer_id(self):
        snap = _snapshot()
        d = snap.to_dict()
        assert "producer_id" not in d
        assert "worker" not in d
        assert "author" not in d
        assert "user" not in d

    def test_from_record_strips_to_safe_fields(self):
        """from_record produces only the declared lightweight fields."""
        record = _coverage_record()
        snap = SubjectCoverageSnapshot.from_record(record, "2026-05-01T14:00:00Z")
        d = snap.to_dict()
        allowed_keys = {
            "run_id",
            "timestamp",
            "target_key",
            "target_content_hash",
            "subject_key",
            "subject_kind",
            "subject_label",
            "state",
            "criticality",
            "observation_count",
            "checks_run",
            "checks_passed",
            "coverage_gap_validator_ids",
            "blocking_reason",
            "has_model_debt",
        }
        assert set(d.keys()) <= allowed_keys

    def test_no_metadata_dict_in_snapshot(self):
        """Snapshots intentionally omit free-form metadata to prevent data leakage."""
        snap = _snapshot()
        d = snap.to_dict()
        assert "metadata" not in d
