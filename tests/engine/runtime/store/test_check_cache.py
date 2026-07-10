"""Tests for CheckCacheStore — 4-part-key validator result cache."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.validator import ValidatorResult

if TYPE_CHECKING:
    from pathlib import Path
from verdx.engine.runtime.store.check_cache import CheckCacheStore


def _result(**overrides: object) -> ValidatorResult:
    defaults: dict[str, object] = {
        "validator_id": "yaml-syntax",
        "validator_version": "1.0.0",
        "drafts": [],
        "checks_run": 3,
        "checks_passed": 2,
        "duration_ms": 150,
        "status": "completed",
    }
    return ValidatorResult(**(defaults | overrides))


class TestCheckCacheStore:
    def test_miss_returns_none(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        assert store.get("sha256:abc", "check-1", "1.0.0", "sha256:cfg1") is None

    def test_round_trip(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        result = _result()
        store.put("sha256:abc", "yaml-syntax", "1.0.0", "sha256:cfg1", result)
        hit = store.get("sha256:abc", "yaml-syntax", "1.0.0", "sha256:cfg1")
        assert hit is not None
        assert hit.validator_id == "yaml-syntax"
        assert hit.checks_run == 3
        assert hit.status == "completed"

    def test_config_hash_independence(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        r1 = _result(checks_run=1)
        r2 = _result(checks_run=2)
        store.put("sha256:abc", "check", "1.0.0", "sha256:cfg1", r1)
        store.put("sha256:abc", "check", "1.0.0", "sha256:cfg2", r2)
        assert store.get("sha256:abc", "check", "1.0.0", "sha256:cfg1").checks_run == 1
        assert store.get("sha256:abc", "check", "1.0.0", "sha256:cfg2").checks_run == 2

    def test_content_hash_independence(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        r1 = _result(checks_run=10)
        store.put("sha256:aaa", "check", "1.0.0", "sha256:cfg", r1)
        assert store.get("sha256:bbb", "check", "1.0.0", "sha256:cfg") is None

    def test_clear_removes_all(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        store.put("sha256:a", "c1", "1.0.0", "sha256:x", _result())
        store.put("sha256:b", "c2", "1.0.0", "sha256:y", _result())
        assert store.clear() == 2
        assert store.get("sha256:a", "c1", "1.0.0", "sha256:x") is None

    def test_clear_empty_returns_zero(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        assert store.clear() == 0

    def test_corrupted_json_returns_none(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        store.put("sha256:abc", "check", "1.0.0", "sha256:cfg", _result())
        cache_dir = tmp_path / "check-cache"
        for f in cache_dir.glob("*.json"):
            f.write_text("not valid json")
        assert store.get("sha256:abc", "check", "1.0.0", "sha256:cfg") is None

    def test_slash_in_check_id_sanitized(self, tmp_path: Path) -> None:
        store = CheckCacheStore(tmp_path)
        store.put("sha256:abc", "domain/check", "1.0.0", "sha256:cfg", _result())
        hit = store.get("sha256:abc", "domain/check", "1.0.0", "sha256:cfg")
        assert hit is not None
