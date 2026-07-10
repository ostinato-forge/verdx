"""Tests for CacheStore — per-validator result caching."""

from __future__ import annotations

from verdx.contracts.dimensions import Confidence, DimLevel, Severity
from verdx.contracts.observation import Evidence, ObservationDraft
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.store.cache import CacheStore


def _draft(**overrides) -> ObservationDraft:
    defaults = {
        "target_key": "sample/ch04/ge01",
        "location": "step:1",
        "rule_id": "yaml-syntax",
        "category": "correctness",
        "severity": Severity.HIGH,
        "confidence": Confidence.CONFIRMED,
        "dimensions": {"correctness": DimLevel.HIGH},
        "summary": "YAML syntax error",
    }
    return ObservationDraft(**(defaults | overrides))


def _result(**overrides) -> ValidatorResult:
    defaults = {
        "validator_id": "yaml-lint",
        "validator_version": "1.0.0",
        "drafts": [],
        "checks_run": 10,
        "checks_passed": 9,
        "duration_ms": 150,
        "status": "completed",
    }
    return ValidatorResult(**(defaults | overrides))


class TestCacheStore:
    def test_get_returns_none_when_empty(self, tmp_path):
        store = CacheStore(tmp_path)
        assert store.get("sha256:abc", "yaml-lint", "1.0.0") is None

    def test_put_and_get_round_trip(self, tmp_path):
        store = CacheStore(tmp_path)
        result = _result()
        store.put("sha256:abc", "yaml-lint", "1.0.0", result)
        cached = store.get("sha256:abc", "yaml-lint", "1.0.0")
        assert cached is not None
        assert cached.validator_id == "yaml-lint"
        assert cached.checks_run == 10
        assert cached.status == "completed"

    def test_round_trip_with_drafts(self, tmp_path):
        """Drafts with evidence survive serialization round-trip."""
        store = CacheStore(tmp_path)
        draft = _draft(
            evidence=(
                Evidence(
                    source="lint",
                    kind="error",
                    details="bad YAML",
                    data={"line": 42},
                ),
            )
        )
        result = _result(drafts=[draft])
        store.put("sha256:abc", "yaml-lint", "1.0.0", result)
        cached = store.get("sha256:abc", "yaml-lint", "1.0.0")
        assert cached is not None
        assert len(cached.drafts) == 1
        assert cached.drafts[0].rule_id == "yaml-syntax"
        assert len(cached.drafts[0].evidence) == 1
        assert cached.drafts[0].evidence[0].data == {"line": 42}

    def test_different_keys_are_independent(self, tmp_path):
        store = CacheStore(tmp_path)
        r1 = _result(validator_id="lint-a", checks_run=5)
        r2 = _result(validator_id="lint-b", checks_run=15)
        store.put("sha256:aaa", "lint-a", "1.0.0", r1)
        store.put("sha256:aaa", "lint-b", "1.0.0", r2)
        assert store.get("sha256:aaa", "lint-a", "1.0.0").checks_run == 5
        assert store.get("sha256:aaa", "lint-b", "1.0.0").checks_run == 15

    def test_different_content_hash_is_independent(self, tmp_path):
        store = CacheStore(tmp_path)
        r1 = _result(checks_run=5)
        r2 = _result(checks_run=15)
        store.put("sha256:v1", "yaml-lint", "1.0.0", r1)
        store.put("sha256:v2", "yaml-lint", "1.0.0", r2)
        assert store.get("sha256:v1", "yaml-lint", "1.0.0").checks_run == 5
        assert store.get("sha256:v2", "yaml-lint", "1.0.0").checks_run == 15

    def test_different_version_is_independent(self, tmp_path):
        store = CacheStore(tmp_path)
        r1 = _result(validator_version="1.0.0", checks_run=5)
        r2 = _result(validator_version="2.0.0", checks_run=15)
        store.put("sha256:abc", "yaml-lint", "1.0.0", r1)
        store.put("sha256:abc", "yaml-lint", "2.0.0", r2)
        assert store.get("sha256:abc", "yaml-lint", "1.0.0").checks_run == 5
        assert store.get("sha256:abc", "yaml-lint", "2.0.0").checks_run == 15

    def test_overwrite_replaces_value(self, tmp_path):
        store = CacheStore(tmp_path)
        r1 = _result(checks_run=5)
        r2 = _result(checks_run=15)
        store.put("sha256:abc", "yaml-lint", "1.0.0", r1)
        store.put("sha256:abc", "yaml-lint", "1.0.0", r2)
        cached = store.get("sha256:abc", "yaml-lint", "1.0.0")
        assert cached.checks_run == 15

    def test_get_returns_none_for_corrupted_json(self, tmp_path):
        store = CacheStore(tmp_path)
        store.put("sha256:abc", "yaml-lint", "1.0.0", _result())
        path = store._key_path("sha256:abc", "yaml-lint", "1.0.0")
        path.write_text("not json", encoding="utf-8")
        assert store.get("sha256:abc", "yaml-lint", "1.0.0") is None

    def test_clear_removes_all_entries(self, tmp_path):
        store = CacheStore(tmp_path)
        store.put("sha256:aaa", "lint-a", "1.0.0", _result(validator_id="lint-a"))
        store.put("sha256:bbb", "lint-b", "1.0.0", _result(validator_id="lint-b"))
        count = store.clear()
        assert count == 2
        assert store.get("sha256:aaa", "lint-a", "1.0.0") is None
        assert store.get("sha256:bbb", "lint-b", "1.0.0") is None

    def test_clear_returns_zero_when_empty(self, tmp_path):
        store = CacheStore(tmp_path)
        assert store.clear() == 0

    def test_round_trip_preserves_metadata(self, tmp_path):
        store = CacheStore(tmp_path)
        result = _result(metadata={"content_id": "SAMPLE01"})
        store.put("sha256:abc", "yaml-lint", "1.0.0", result)
        cached = store.get("sha256:abc", "yaml-lint", "1.0.0")
        assert cached.metadata == {"content_id": "SAMPLE01"}

    def test_round_trip_preserves_subject_keys(self, tmp_path):
        store = CacheStore(tmp_path)
        result = _result(
            subject_keys_checked=("key1", "key2"),
            subject_keys_skipped=("key3",),
        )
        store.put("sha256:abc", "yaml-lint", "1.0.0", result)
        cached = store.get("sha256:abc", "yaml-lint", "1.0.0")
        assert cached.subject_keys_checked == ("key1", "key2")
        assert cached.subject_keys_skipped == ("key3",)
