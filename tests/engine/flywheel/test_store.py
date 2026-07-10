"""Tests for flywheel storage — layered loading, atomic writes, corrections, org layer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from verdx.engine.flywheel.store import FlywheelStore, _resolve_data_root
from verdx.engine.flywheel.types import Correction, Pattern


def _correction(
    sig: str = "sig-001",
    target: str = "target-0033",
    severity: str = "low",
    decision: str = "false-positive",
) -> Correction:
    return Correction(
        observation_signature=sig,
        target_key=target,
        initial_severity=severity,
        final_decision=decision,
        description="test correction",
        domain="training",
        timestamp="2026-06-21T00:00:00Z",
    )


def _pattern_dict(
    pid: str = "pat-001",
    symptoms: list[str] | None = None,
    seen_count: int = 1,
) -> dict:
    return {
        "id": pid,
        "symptoms": symptoms or ["exercise", "guided"],
        "diagnosis": "test",
        "resolution": "test",
        "category": "test",
        "domain": "training",
        "seen_count": seen_count,
        "observation_signatures": [],
        "targets": ["target-0033"],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }


def _repo_dir(tmp_path: Path) -> Path:
    return tmp_path / "repos" / "test-repo"


def _seed_patterns(tmp_path: Path, patterns: list[dict]) -> None:
    d = _repo_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / "patterns.json").write_text(json.dumps({"patterns": patterns}), encoding="utf-8")


def _seed_corrections(tmp_path: Path, corrections: list[dict]) -> None:
    d = _repo_dir(tmp_path) / "corrections"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "seed.jsonl").open("w", encoding="utf-8") as f:
        for c in corrections:
            f.write(json.dumps(c) + "\n")


class TestFlywheelStoreCorrections:
    def test_creates_file_on_first_write(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        path = store.append_correction(_correction())
        assert path.exists()
        assert path.suffix == ".jsonl"

    def test_appends_multiple_corrections(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        store.append_correction(_correction("sig-001"))
        store.append_correction(_correction("sig-002"))
        store.append_correction(_correction("sig-003"))
        corrections = store.load_corrections()
        assert len(corrections) >= 3

    def test_round_trips_correction_fields(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        original = _correction("sig-rt", "target-0036", "medium", "accepted-risk")
        store.append_correction(original)
        loaded = [c for c in store.load_corrections() if c.observation_signature == "sig-rt"]
        assert len(loaded) == 1
        assert loaded[0].target_key == "target-0036"
        assert loaded[0].initial_severity == "medium"
        assert loaded[0].final_decision == "accepted-risk"

    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        assert store.load_corrections() == []


class TestFlywheelStorePatterns:
    def test_loads_empty_patterns(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        assert store.load_patterns() == []

    def test_saves_and_loads_patterns(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        pattern = Pattern(
            id="pat-001",
            symptoms=["exercise", "guided"],
            diagnosis="test",
            resolution="test",
            category="test",
            domain="training",
            seen_count=1,
            targets=["target-0033"],
            created="2026-01-01T00:00:00Z",
            updated="2026-01-01T00:00:00Z",
        )
        store.save_patterns([pattern])
        loaded = store.load_patterns()
        assert len(loaded) >= 1
        p = next(x for x in loaded if x.id == "pat-001")
        assert p.symptoms == ["exercise", "guided"]
        assert p.domain == "training"

    def test_atomic_write_produces_valid_json(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        pattern = Pattern(
            id="pat-001",
            symptoms=["test"],
            diagnosis="d",
            resolution="r",
            category="c",
            domain="training",
            seen_count=1,
        )
        path = store.save_patterns([pattern])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "patterns" in data
        assert len(data["patterns"]) == 1


class TestFlywheelStoreLayering:
    def test_repo_shadows_global_pattern(self, tmp_path: Path) -> None:
        _seed_patterns(tmp_path, [_pattern_dict("pat-001", ["repo", "pattern"])])

        store = FlywheelStore("test-repo", data_root=tmp_path)
        patterns = store.load_patterns()
        pat_001 = [p for p in patterns if p.id == "pat-001"]
        assert len(pat_001) >= 1
        assert "repo" in pat_001[-1].symptoms

    def test_corrections_from_all_layers(self, tmp_path: Path) -> None:
        _seed_corrections(
            tmp_path,
            [
                {
                    "observation_signature": "sig-repo",
                    "target_key": "target-0033",
                    "initial_severity": "low",
                    "final_decision": "false-positive",
                    "description": "from repo",
                    "domain": "training",
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            ],
        )
        store = FlywheelStore("test-repo", data_root=tmp_path)
        corrections = store.load_corrections()
        sigs = [c.observation_signature for c in corrections]
        assert "sig-repo" in sigs


class TestFlywheelStoreRepoPatterns:
    def test_loads_repo_layer_only(self, tmp_path: Path) -> None:
        _seed_patterns(tmp_path, [_pattern_dict("pat-001")])
        store = FlywheelStore("test-repo", data_root=tmp_path)
        patterns = store.load_repo_patterns()
        assert len(patterns) == 1
        assert patterns[0].id == "pat-001"

    def test_empty_when_no_repo_patterns(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        assert store.load_repo_patterns() == []


class TestSaveCorrections:
    def test_round_trips_corrections(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        corrections = [
            _correction("sig-001", "target-0033"),
            _correction("sig-002", "target-0034"),
        ]
        path = store.save_corrections(corrections)
        assert path.exists()
        loaded = store.load_corrections()
        sigs = [c.observation_signature for c in loaded]
        assert "sig-001" in sigs
        assert "sig-002" in sigs

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        store.append_correction(_correction("sig-old"))
        store.save_corrections([_correction("sig-new")])
        loaded = store.load_corrections()
        sigs = [c.observation_signature for c in loaded]
        assert "sig-new" in sigs


class TestOrgLayerMethods:
    def test_save_and_load_org_patterns(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        pattern = Pattern(
            id="org-001",
            symptoms=["ssl", "disabled"],
            diagnosis="FP",
            resolution="Override",
            category="content-fp",
            domain="training",
            seen_count=5,
            targets=["target-0033", "target-0034"],
            created="2026-01-01T00:00:00Z",
            updated="2026-06-01T00:00:00Z",
        )
        path = store.save_org_pattern(pattern)
        assert path.exists()

        loaded = store.load_org_patterns()
        assert len(loaded) == 1
        assert loaded[0].id == "org-001"
        assert loaded[0].seen_count == 5

    def test_load_empty_org(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        assert store.load_org_patterns() == []

    def test_atomic_write_valid_json(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        pattern = Pattern(
            id="org-001",
            symptoms=["test"],
            diagnosis="d",
            resolution="r",
            category="c",
            domain="training",
            seen_count=1,
        )
        path = store.save_org_pattern(pattern)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "pattern" in data
        assert data["pattern"]["id"] == "org-001"


class TestSchemaValidation:
    def test_malformed_pattern_skipped(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {"id": "pat-001", "symptoms": ["ssl"]},
                {"id": "", "symptoms": ["bad"]},
                {"symptoms": ["no-id"]},
                {"id": "pat-002", "symptoms": []},
            ],
        )
        store = FlywheelStore("test-repo", data_root=tmp_path)
        patterns = store.load_repo_patterns()
        assert len(patterns) == 1
        assert patterns[0].id == "pat-001"

    def test_malformed_correction_skipped(self, tmp_path: Path) -> None:
        good = {
            "observation_signature": "sig-1",
            "target_key": "target-0033",
            "initial_severity": "low",
            "final_decision": "false-positive",
        }
        bad_missing_sig = {
            "target_key": "target-0033",
            "initial_severity": "low",
            "final_decision": "false-positive",
        }
        bad_empty_decision = {
            "observation_signature": "sig-2",
            "target_key": "target-0033",
            "initial_severity": "low",
            "final_decision": "",
        }
        _seed_corrections(tmp_path, [good, bad_missing_sig, bad_empty_decision])
        store = FlywheelStore("test-repo", data_root=tmp_path)
        corrections = store.load_corrections()
        assert len(corrections) == 1
        assert corrections[0].observation_signature == "sig-1"


class TestBlockedStorage:
    def test_save_and_load_blocked(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        entries = [{"pattern_id": "org-001", "reason": "test"}]
        store.save_blocked(entries)
        loaded = store.load_blocked()
        assert len(loaded) == 1
        assert loaded[0]["pattern_id"] == "org-001"

    def test_blocked_ids(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        store.save_blocked(
            [
                {"pattern_id": "org-001", "reason": "a"},
                {"pattern_id": "org-002", "reason": "b"},
            ]
        )
        ids = store.blocked_ids()
        assert ids == frozenset({"org-001", "org-002"})

    def test_empty_blocked(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        assert store.blocked_ids() == frozenset()


class TestResolveDataRoot:
    def test_explicit_param_wins(self, tmp_path: Path) -> None:
        result = _resolve_data_root(tmp_path)
        assert result == tmp_path

    def test_env_var_wins_over_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_dir = tmp_path / "from-env"
        env_dir.mkdir()
        monkeypatch.setenv("VERDX_FLYWHEEL_DATA", str(env_dir))
        result = _resolve_data_root(None)
        assert result == env_dir

    def test_sibling_detection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VERDX_FLYWHEEL_DATA", raising=False)
        repo_root = tmp_path / "verdx"
        repo_root.mkdir()
        data_repo = tmp_path / "verdx-data"
        (data_repo / "org").mkdir(parents=True)
        (data_repo / "repos").mkdir()

        with patch("verdx.engine.flywheel.store._find_repo_root", return_value=repo_root):
            result = _resolve_data_root(None)
        assert result == data_repo

    def test_local_fallback_when_no_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERDX_FLYWHEEL_DATA", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        with patch("verdx.engine.flywheel.store._find_repo_root", return_value=None):
            result = _resolve_data_root(None)
        assert result == tmp_path / ".local" / "share" / "verdx" / "data"
        assert result.is_dir()
