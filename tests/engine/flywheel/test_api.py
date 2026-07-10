"""Tests for flywheel API — log, search, annotate, review, promote, compact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from verdx.engine.flywheel.api import (
    Flywheel,
    _compact_corrections,
    _compact_patterns,
    _relevance_score,
    categorize_resolution,
    detect_contradictions,
)
from verdx.engine.flywheel.store import FlywheelStore
from verdx.engine.flywheel.types import Correction, Pattern


@dataclass(frozen=True)
class FakeObservation:
    signature: str
    message: str
    severity: str = "low"
    category: str = "content"


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


class TestLogCorrection:
    def test_logs_when_different(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.log_correction(
            observation_signature="sig-001",
            target_key="target-0033",
            initial_severity="low",
            final_decision="false-positive",
            description="not a real issue",
            domain="training",
        )
        assert result["logged"] is True
        assert result["confirmed"] is False
        assert result["total_corrections"] >= 1

    def test_logs_confirmation_when_same(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.log_correction(
            observation_signature="sig-001",
            target_key="target-0033",
            initial_severity="low",
            final_decision="low",
        )
        assert result["logged"] is True
        assert result["confirmed"] is True
        assert result["total_corrections"] >= 1


class TestLogPattern:
    def test_creates_new_pattern(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.log_pattern(
            target_key="target-0033",
            symptoms="SSL verification disabled insecure practice",
            diagnosis="Exercise uses verify=false intentionally",
            resolution="Override as accepted-risk",
            category="content-false-positive",
            domain="training",
        )
        assert result["action"] == "created"
        assert result["seen_count"] == 1
        assert result["pattern_id"].startswith("pat-")

    def test_merges_similar_pattern(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled", "insecure"],
                    "diagnosis": "Exercise context",
                    "resolution": "Override",
                    "category": "content-fp",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": ["sig-old"],
                    "targets": ["target-0032"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.log_pattern(
            target_key="target-0033",
            symptoms="SSL verification disabled insecure practice content",
            diagnosis="Same pattern",
            resolution="Same resolution",
            domain="training",
            observation_signatures=["sig-new"],
        )
        assert result["action"] == "updated"
        assert result["pattern_id"] == "pat-001"
        assert result["seen_count"] == 2

    def test_creates_new_when_dissimilar(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["image", "broken", "reference", "missing"],
                    "diagnosis": "Image file missing",
                    "resolution": "Add the image",
                    "category": "structure",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": [],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.log_pattern(
            target_key="target-0033",
            symptoms="SSL verification disabled insecure practice",
            diagnosis="Different pattern",
            resolution="Different resolution",
            domain="training",
        )
        assert result["action"] == "created"
        assert result["pattern_id"].startswith("pat-")

    def test_skips_when_no_tokens(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.log_pattern(
            target_key="target-0033",
            symptoms="a the is",
            diagnosis="d",
            resolution="r",
        )
        assert result["action"] == "skipped"


class TestSearch:
    def test_finds_matching_pattern(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled"],
                    "diagnosis": "Exercise context",
                    "resolution": "Override as accepted-risk",
                    "category": "content-fp",
                    "domain": "training",
                    "seen_count": 3,
                    "observation_signatures": [],
                    "targets": ["target-0032"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        results = fw.search("SSL verification disabled")
        assert len(results) == 1
        assert results[0]["pattern_id"] == "pat-001"
        assert results[0]["score"] > 0.2

    def test_no_match_when_dissimilar(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["image", "broken", "reference"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": [],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        results = fw.search("SSL verification disabled")
        assert len(results) == 0

    def test_empty_keywords_returns_empty(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        assert fw.search("") == []


class TestAnnotate:
    def test_annotates_matching_observation(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled", "insecure"],
                    "diagnosis": "Exercise intentionally disables TLS",
                    "resolution": "Override as accepted-risk",
                    "category": "content-fp",
                    "domain": "training",
                    "seen_count": 3,
                    "observation_signatures": ["sig-old-1", "sig-old-2"],
                    "targets": ["target-0032", "target-0036"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        obs = FakeObservation(
            signature="sig-new",
            message="Insecure practice: SSL/TLS verification disabled",
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        annotations = fw.annotate([obs], domain="training")
        assert len(annotations) == 1
        assert annotations[0].pattern_id == "pat-001"
        assert annotations[0].match_score > 0.2
        assert annotations[0].confidence == "high"

    def test_no_annotation_when_no_patterns(self, tmp_path: Path) -> None:
        obs = FakeObservation(signature="sig-001", message="some finding")
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        annotations = fw.annotate([obs])
        assert annotations == []

    def test_suggests_false_positive_from_corrections(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled", "insecure"],
                    "diagnosis": "Exercise context",
                    "resolution": "Override",
                    "category": "content-fp",
                    "domain": "training",
                    "seen_count": 5,
                    "observation_signatures": ["sig-a", "sig-b", "sig-c"],
                    "targets": ["target-0032"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )
        _seed_corrections(
            tmp_path,
            [
                {
                    "observation_signature": "sig-a",
                    "target_key": "target-0032",
                    "initial_severity": "low",
                    "final_decision": "false-positive",
                    "description": "exercise context",
                    "domain": "training",
                    "timestamp": "2026-06-01T00:00:00Z",
                },
                {
                    "observation_signature": "sig-b",
                    "target_key": "target-0032",
                    "initial_severity": "low",
                    "final_decision": "false-positive",
                    "description": "exercise context",
                    "domain": "training",
                    "timestamp": "2026-06-02T00:00:00Z",
                },
            ],
        )

        obs = FakeObservation(
            signature="sig-new",
            message="Insecure practice: SSL/TLS verification disabled",
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        annotations = fw.annotate([obs], domain="training")
        assert len(annotations) == 1
        assert annotations[0].suggested_decision == "false-positive"

    def test_annotates_from_direct_correction(self, tmp_path: Path) -> None:
        _seed_corrections(
            tmp_path,
            [
                {
                    "observation_signature": "sig-exact",
                    "target_key": "target-0033",
                    "initial_severity": "low",
                    "final_decision": "accepted-risk",
                    "description": "known issue",
                    "domain": "training",
                    "timestamp": "2026-06-01T00:00:00Z",
                }
            ],
        )

        obs = FakeObservation(
            signature="sig-exact",
            message="xyz qqq zzz unique words no pattern match",
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        annotations = fw.annotate([obs])
        assert len(annotations) == 1
        assert annotations[0].suggested_decision == "accepted-risk"
        assert annotations[0].pattern_id is None


class TestReview:
    def test_review_with_data(self, tmp_path: Path) -> None:
        _seed_corrections(
            tmp_path,
            [
                {
                    "observation_signature": "sig-1",
                    "target_key": "target-0033",
                    "initial_severity": "low",
                    "final_decision": "false-positive",
                    "description": "fp",
                    "domain": "training",
                    "timestamp": "2026-06-01T00:00:00Z",
                },
                {
                    "observation_signature": "sig-2",
                    "target_key": "target-0033",
                    "initial_severity": "medium",
                    "final_decision": "accepted-risk",
                    "description": "ar",
                    "domain": "training",
                    "timestamp": "2026-06-02T00:00:00Z",
                },
            ],
        )
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "disabled"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 3,
                    "observation_signatures": [],
                    "targets": [],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                },
            ],
        )

        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        summary = fw.review()

        assert summary["corrections_summary"]["total"] == 2
        assert "low→false-positive" in summary["corrections_summary"]["transitions"]
        assert summary["patterns_summary"]["total"] >= 1

    def test_review_empty(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        summary = fw.review()
        assert summary["corrections_summary"]["total"] == 0
        assert summary["patterns_summary"]["total"] >= 0


class TestPromote:
    def _make_repo(self, base: Path, name: str, patterns: list[dict]) -> None:
        """Seed patterns for a repo under the shared data_root."""
        d = base / "repos" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "patterns.json").write_text(json.dumps({"patterns": patterns}), encoding="utf-8")

    def test_promotes_cross_repo_pattern(self, tmp_path: Path) -> None:
        ssl_pattern = {
            "symptoms": ["ssl", "verification", "disabled"],
            "diagnosis": "Exercise context",
            "resolution": "Override as accepted-risk",
            "category": "content-fp",
            "domain": "training",
            "seen_count": 1,
            "observation_signatures": ["sig-a"],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-06-01T00:00:00Z",
        }

        for name in ["target-0033", "target-0034", "target-0036"]:
            pat = {**ssl_pattern, "id": "pat-001", "targets": [name]}
            self._make_repo(tmp_path, name, [pat])

        store = FlywheelStore("target-0033", data_root=tmp_path)
        fw = Flywheel(store)
        result = fw.promote(min_repos=3)
        assert result["promoted"] == 1
        assert result["total_org_patterns"] == 1

        org_patterns = store.load_org_patterns()
        assert len(org_patterns) == 1
        assert org_patterns[0].id.startswith("org-")
        assert org_patterns[0].seen_count == 3

    def test_no_promotion_when_too_few_repos(self, tmp_path: Path) -> None:
        ssl_pattern = {
            "id": "pat-001",
            "symptoms": ["ssl", "disabled"],
            "diagnosis": "d",
            "resolution": "r",
            "category": "c",
            "domain": "training",
            "seen_count": 2,
            "observation_signatures": [],
            "targets": ["target-0033"],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }

        self._make_repo(tmp_path, "target-0033", [ssl_pattern])

        store = FlywheelStore("target-0033", data_root=tmp_path)
        fw = Flywheel(store)
        result = fw.promote(min_repos=3)
        assert result["promoted"] == 0

    def test_merges_into_existing_org_pattern(self, tmp_path: Path) -> None:
        store = FlywheelStore("target-0033", data_root=tmp_path)
        existing_org = Pattern(
            id="org-001",
            symptoms=["ssl", "verification", "disabled"],
            diagnosis="Known FP",
            resolution="Override",
            category="content-fp",
            domain="training",
            seen_count=3,
            observation_signatures=["sig-old"],
            targets=["target-0032"],
            created="2026-01-01T00:00:00Z",
            updated="2026-01-01T00:00:00Z",
        )
        store.save_org_pattern(existing_org)

        ssl_pattern = {
            "id": "pat-001",
            "symptoms": ["ssl", "verification", "disabled", "insecure"],
            "diagnosis": "Exercise context",
            "resolution": "Override",
            "category": "content-fp",
            "domain": "training",
            "seen_count": 5,
            "observation_signatures": ["sig-new"],
            "targets": [],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-06-01T00:00:00Z",
        }

        for name in ["target-0033", "target-0034", "target-0036"]:
            pat = {**ssl_pattern, "targets": [name]}
            self._make_repo(tmp_path, name, [pat])

        fw = Flywheel(store)
        result = fw.promote(min_repos=3)
        assert result["promoted"] == 0
        assert result["total_org_patterns"] == 1

        org_patterns = store.load_org_patterns()
        assert org_patterns[0].id == "org-001"
        assert org_patterns[0].seen_count == 18
        assert "target-0033" in org_patterns[0].targets

    def test_dissimilar_patterns_not_grouped(self, tmp_path: Path) -> None:
        self._make_repo(
            tmp_path,
            "target-0033",
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": ["target-0033"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )
        self._make_repo(
            tmp_path,
            "target-0034",
            [
                {
                    "id": "pat-001",
                    "symptoms": ["image", "broken", "reference", "missing"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": ["target-0034"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )
        self._make_repo(
            tmp_path,
            "target-0036",
            [
                {
                    "id": "pat-001",
                    "symptoms": ["module", "unknown", "module"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": ["target-0036"],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        store = FlywheelStore("target-0033", data_root=tmp_path)
        fw = Flywheel(store)
        result = fw.promote(min_repos=3)
        assert result["promoted"] == 0

    def test_empty_repos_handled(self, tmp_path: Path) -> None:
        self._make_repo(tmp_path, "target-0033", [])
        self._make_repo(tmp_path, "target-0034", [])

        store = FlywheelStore("target-0033", data_root=tmp_path)
        fw = Flywheel(store)
        result = fw.promote(min_repos=3)
        assert result["promoted"] == 0
        assert result["repos_scanned"] == 2


def _make_pattern(
    pid: str,
    seen_count: int = 1,
    updated: str = "",
    targets: list[str] | None = None,
) -> Pattern:
    now = updated or datetime.now(tz=UTC).isoformat()
    return Pattern(
        id=pid,
        symptoms=["test", pid],
        diagnosis="d",
        resolution="r",
        category="c",
        domain="training",
        seen_count=seen_count,
        targets=targets or ["target-0033"],
        created=now,
        updated=now,
    )


def _make_correction(
    sig: str,
    target: str = "target-0033",
    timestamp: str = "",
) -> Correction:
    ts = timestamp or datetime.now(tz=UTC).isoformat()
    return Correction(
        observation_signature=sig,
        target_key=target,
        initial_severity="low",
        final_decision="false-positive",
        description="test",
        domain="training",
        timestamp=ts,
    )


class TestRelevanceScore:
    def test_recent_high_count_scores_high(self) -> None:
        now = datetime.now(tz=UTC)
        p = _make_pattern(
            "p1", seen_count=10, updated=now.isoformat(), targets=["A", "B", "C", "D", "E"]
        )
        score = _relevance_score(p, now)
        assert score > 5.0

    def test_old_low_count_scores_low(self) -> None:
        now = datetime.now(tz=UTC)
        old = (now - timedelta(days=180)).isoformat()
        p = _make_pattern("p1", seen_count=1, updated=old)
        score = _relevance_score(p, now)
        assert score < 1.0


class TestCompactPatterns:
    def test_under_cap_unchanged(self) -> None:
        patterns = [_make_pattern(f"p{i}") for i in range(5)]
        result, pruned = _compact_patterns(patterns, max_patterns=10)
        assert len(result) == 5
        assert pruned == 0

    def test_over_cap_keeps_highest_relevance(self) -> None:
        now = datetime.now(tz=UTC)
        old = (now - timedelta(days=180)).isoformat()
        patterns = [
            _make_pattern("high", seen_count=10, updated=now.isoformat(), targets=["A", "B", "C"]),
            _make_pattern("low1", seen_count=1, updated=old),
            _make_pattern("low2", seen_count=1, updated=old),
        ]
        result, pruned = _compact_patterns(patterns, max_patterns=1)
        assert len(result) == 1
        assert pruned == 2
        assert result[0].id == "high"

    def test_stale_pruned_before_recent(self) -> None:
        now = datetime.now(tz=UTC)
        recent = now.isoformat()
        stale = (now - timedelta(days=180)).isoformat()
        patterns = [
            _make_pattern("recent", seen_count=2, updated=recent),
            _make_pattern("stale", seen_count=2, updated=stale),
        ]
        result, pruned = _compact_patterns(patterns, max_patterns=1)
        assert result[0].id == "recent"
        assert pruned == 1


class TestCompactCorrections:
    def test_deduplicates_keeping_latest(self) -> None:
        corrections = [
            _make_correction("sig-1", timestamp="2026-01-01T00:00:00Z"),
            _make_correction("sig-1", timestamp="2026-06-01T00:00:00Z"),
        ]
        result, pruned = _compact_corrections(corrections, max_corrections=100)
        assert len(result) == 1
        assert result[0].timestamp == "2026-06-01T00:00:00Z"
        assert pruned == 1

    def test_caps_by_recency(self) -> None:
        corrections = [
            _make_correction(f"sig-{i}", timestamp=f"2026-01-{i + 1:02d}T00:00:00Z")
            for i in range(10)
        ]
        result, pruned = _compact_corrections(corrections, max_corrections=3)
        assert len(result) == 3
        assert pruned == 7
        assert result[0].timestamp > result[1].timestamp

    def test_under_cap_still_deduplicates(self) -> None:
        corrections = [
            _make_correction("sig-1", timestamp="2026-01-01T00:00:00Z"),
            _make_correction("sig-1", timestamp="2026-06-01T00:00:00Z"),
            _make_correction("sig-2", timestamp="2026-03-01T00:00:00Z"),
        ]
        result, pruned = _compact_corrections(corrections, max_corrections=100)
        assert len(result) == 2
        assert pruned == 1


class TestFlywheelCompact:
    def test_compacts_repo_layer(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        patterns = [
            Pattern(
                id=f"pat-{i:03d}",
                symptoms=["test", f"word{i}"],
                diagnosis="d",
                resolution="r",
                category="c",
                domain="training",
                seen_count=1,
                updated=(datetime.now(tz=UTC) - timedelta(days=200)).isoformat(),
            )
            for i in range(10)
        ]
        store.save_patterns(patterns)

        fw = Flywheel(store)
        result = fw.compact(max_patterns=3)
        assert result["patterns_pruned"] == 7

        remaining = store.load_repo_patterns()
        assert len(remaining) == 3

    def test_skips_when_under_cap(self, tmp_path: Path) -> None:
        store = FlywheelStore("test-repo", data_root=tmp_path)
        patterns = [
            Pattern(
                id=f"pat-{i:03d}",
                symptoms=["test", f"w{i}"],
                diagnosis="d",
                resolution="r",
                category="c",
                domain="training",
                seen_count=1,
            )
            for i in range(3)
        ]
        store.save_patterns(patterns)

        fw = Flywheel(store)
        result = fw.compact(max_patterns=200)
        assert result["patterns_pruned"] == 0
        assert result["corrections_pruned"] == 0


class TestBlocklist:
    def test_block_pattern(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.block("org-001", reason="bad pattern")
        assert result["action"] == "blocked"

    def test_block_already_blocked(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.block("org-001", reason="bad")
        result = fw.block("org-001", reason="bad again")
        assert result["action"] == "already_blocked"

    def test_unblock_pattern(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.block("org-001", reason="bad")
        result = fw.unblock("org-001")
        assert result["action"] == "unblocked"

    def test_unblock_not_found(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        result = fw.unblock("org-999")
        assert result["action"] == "not_found"

    def test_blocked_excluded_from_search(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled"],
                    "diagnosis": "Exercise context",
                    "resolution": "Override",
                    "category": "content-fp",
                    "domain": "training",
                    "seen_count": 3,
                    "observation_signatures": [],
                    "targets": [],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.block("pat-001", reason="test")
        results = fw.search("SSL verification disabled")
        assert len(results) == 0

    def test_blocked_excluded_from_annotate(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "verification", "disabled", "insecure"],
                    "diagnosis": "Exercise context",
                    "resolution": "Override",
                    "category": "content-fp",
                    "domain": "training",
                    "seen_count": 3,
                    "observation_signatures": [],
                    "targets": [],
                    "created": "2026-01-01T00:00:00Z",
                    "updated": "2026-01-01T00:00:00Z",
                }
            ],
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.block("pat-001", reason="test")
        obs = FakeObservation(
            signature="sig-new",
            message="Insecure practice: SSL/TLS verification disabled",
        )
        annotations = fw.annotate([obs], domain="training")
        assert len(annotations) == 0


class TestSource:
    def test_source_stored_on_new_pattern(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.log_pattern(
            target_key="target-0033",
            symptoms="SSL verification disabled insecure practice",
            diagnosis="Exercise context",
            resolution="Override",
            source="example-source:REF-001",
        )
        patterns = FlywheelStore("test-repo", data_root=tmp_path).load_patterns()
        repo_pats = [p for p in patterns if p.id.startswith("pat-")]
        assert repo_pats[0].source == "example-source:REF-001"

    def test_source_default_empty(self, tmp_path: Path) -> None:
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.log_pattern(
            target_key="target-0033",
            symptoms="SSL verification disabled insecure practice",
            diagnosis="d",
            resolution="r",
        )
        patterns = FlywheelStore("test-repo", data_root=tmp_path).load_patterns()
        repo_pats = [p for p in patterns if p.id.startswith("pat-")]
        assert repo_pats[0].source == ""


class TestCategorizeResolution:
    """Tests for the resolution categorizer."""

    def test_fix_keywords(self) -> None:
        assert categorize_resolution("fix the module name") == "fix"
        assert categorize_resolution("Update the image ref") == "fix"
        assert categorize_resolution("Replace deprecated tag") == "fix"

    def test_accept_keywords(self) -> None:
        assert categorize_resolution("Override as accepted-risk") == "accept"
        assert categorize_resolution("This is by design") == "accept"
        assert categorize_resolution("Lab convention, won't fix") == "accept"

    def test_platform_keywords(self) -> None:
        assert categorize_resolution("Flamel strips the class") == "platform"
        assert categorize_resolution("ROL display bug") == "platform"
        assert categorize_resolution("Video encoding issue") == "platform"

    def test_returns_none_for_unknown(self) -> None:
        assert categorize_resolution("do something unusual") is None


class TestDetectContradictions:
    def test_no_contradictions_when_empty(self) -> None:
        assert detect_contradictions([]) == []

    def test_detects_fix_vs_accept_contradiction(self) -> None:
        p1 = _make_pattern("p1", seen_count=5)
        p1.symptoms = ["ssl", "verification", "disabled"]
        p1.resolution = "Override as accepted-risk"
        p2 = _make_pattern("p2", seen_count=3)
        p2.symptoms = ["ssl", "verification", "disabled", "insecure"]
        p2.resolution = "Fix the module name"
        result = detect_contradictions([p1, p2])
        assert len(result) == 1
        assert result[0]["pattern_ids"] == ["p1", "p2"]

    def test_no_contradiction_for_same_category(self) -> None:
        p1 = _make_pattern("p1", seen_count=5)
        p1.symptoms = ["ssl", "verification", "disabled"]
        p1.resolution = "This is by design"
        p2 = _make_pattern("p2", seen_count=3)
        p2.symptoms = ["ssl", "verification", "disabled", "insecure"]
        p2.resolution = "Lab convention, expected behavior"
        result = detect_contradictions([p1, p2])
        assert len(result) == 0


class TestReviewIssues:
    def test_detects_stale_pattern(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "disabled"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": [],
                    "created": "2025-01-01T00:00:00Z",
                    "updated": "2025-01-01T00:00:00Z",
                }
            ],
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        summary = fw.review()
        stale_issues = [i for i in summary["issues"] if i["type"] == "stale"]
        assert len(stale_issues) == 1
        assert stale_issues[0]["pattern_id"] == "pat-001"

    def test_detects_low_evidence(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "disabled"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 1,
                    "observation_signatures": [],
                    "targets": ["target-0033"],
                    "created": "2026-06-01T00:00:00Z",
                    "updated": "2026-06-01T00:00:00Z",
                }
            ],
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        summary = fw.review()
        low_ev = [i for i in summary["issues"] if i["type"] == "low-evidence"]
        assert len(low_ev) == 1

    def test_review_includes_blocked_count(self, tmp_path: Path) -> None:
        _seed_patterns(
            tmp_path,
            [
                {
                    "id": "pat-001",
                    "symptoms": ["ssl", "disabled"],
                    "diagnosis": "d",
                    "resolution": "r",
                    "category": "c",
                    "domain": "training",
                    "seen_count": 3,
                    "observation_signatures": [],
                    "targets": ["A", "B"],
                    "created": "2026-06-01T00:00:00Z",
                    "updated": "2026-06-01T00:00:00Z",
                }
            ],
        )
        fw = Flywheel(FlywheelStore("test-repo", data_root=tmp_path))
        fw.block("pat-001", reason="test")
        summary = fw.review()
        assert summary["patterns_summary"]["blocked"] == 1
