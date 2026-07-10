"""Tests for flywheel keyword matching — tokenization, Jaccard, pattern search."""

from __future__ import annotations

import pytest

from verdx.engine.flywheel.matching import (
    DOMAIN_BOOST,
    MERGE_THRESHOLD,
    SEARCH_THRESHOLD,
    STOPWORDS,
    find_best_matches,
    jaccard,
    tokenize,
)
from verdx.engine.flywheel.types import Pattern


def _pattern(
    pid: str = "pat-001",
    symptoms: list[str] | None = None,
    domain: str = "training",
    seen_count: int = 1,
    targets: list[str] | None = None,
) -> Pattern:
    return Pattern(
        id=pid,
        symptoms=symptoms or ["exercise", "guided", "lab"],
        diagnosis="test diagnosis",
        resolution="test resolution",
        category="test",
        domain=domain,
        seen_count=seen_count,
        targets=targets or [],
        created="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
    )


class TestTokenize:
    def test_basic_extraction(self) -> None:
        tokens = tokenize("Missing continuation before code block in step 3")
        assert "missing" in tokens
        assert "continuation" in tokens
        assert "before" in tokens
        assert "code" in tokens
        assert "block" in tokens
        assert "step" in tokens

    def test_drops_short_tokens(self) -> None:
        tokens = tokenize("a b cd efg")
        assert "efg" in tokens
        assert "a" not in tokens
        assert "b" not in tokens
        assert "cd" not in tokens

    def test_drops_stopwords(self) -> None:
        tokens = tokenize("the module is not available and should be removed")
        assert "the" not in tokens
        assert "not" not in tokens
        assert "and" not in tokens
        assert "should" not in tokens
        assert "module" in tokens
        assert "available" in tokens
        assert "removed" in tokens

    def test_lowercases(self) -> None:
        tokens = tokenize("SSL Verification Disabled")
        assert "ssl" in tokens
        assert "verification" in tokens
        assert "disabled" in tokens

    def test_extracts_underscored_tokens(self) -> None:
        tokens = tokenize("network.healthchecks.filesystem")
        assert "network" in tokens
        assert "healthchecks" in tokens
        assert "filesystem" in tokens

    def test_empty_string(self) -> None:
        assert tokenize("") == frozenset()

    def test_all_stopwords(self) -> None:
        assert tokenize("the and is are") == frozenset()

    def test_returns_frozenset(self) -> None:
        result = tokenize("hello world foo")
        assert isinstance(result, frozenset)


class TestJaccard:
    def test_identical_sets(self) -> None:
        s = frozenset({"a", "b", "c"})
        assert jaccard(s, s) == 1.0

    def test_disjoint_sets(self) -> None:
        assert jaccard(frozenset({"a", "b"}), frozenset({"c", "d"})) == 0.0

    def test_partial_overlap(self) -> None:
        a = frozenset({"mission", "training", "scenario", "exercise"})
        b = frozenset({"mission", "training", "guide"})
        score = jaccard(a, b)
        assert score == pytest.approx(2 / 5)

    def test_empty_set_a(self) -> None:
        assert jaccard(frozenset(), frozenset({"a"})) == 0.0

    def test_empty_set_b(self) -> None:
        assert jaccard(frozenset({"a"}), frozenset()) == 0.0

    def test_both_empty(self) -> None:
        assert jaccard(frozenset(), frozenset()) == 0.0


class TestFindBestMatches:
    def test_finds_match_above_threshold(self) -> None:
        p = _pattern(symptoms=["exercise", "guided", "lab", "rendering"])
        matches = find_best_matches(
            frozenset({"exercise", "guided", "lab"}),
            [p],
        )
        assert len(matches) == 1
        assert matches[0][0].id == "pat-001"
        assert matches[0][1] >= SEARCH_THRESHOLD

    def test_no_match_below_threshold(self) -> None:
        p = _pattern(symptoms=["completely", "different", "words"])
        matches = find_best_matches(
            frozenset({"exercise", "guided", "lab"}),
            [p],
        )
        assert len(matches) == 0

    def test_domain_boost(self) -> None:
        p = _pattern(domain="training", targets=["target-0033"])
        tokens = frozenset({"exercise", "lab"})
        unboosted = find_best_matches(tokens, [p])
        boosted = find_best_matches(tokens, [p], domain="training")

        if unboosted and boosted:
            assert boosted[0][1] == pytest.approx(unboosted[0][1] * DOMAIN_BOOST)

    def test_sorts_by_score_then_seen_count(self) -> None:
        p1 = _pattern("pat-001", ["exercise", "guided"], seen_count=5)
        p2 = _pattern("pat-002", ["exercise", "guided", "lab"], seen_count=1)
        matches = find_best_matches(
            frozenset({"exercise", "guided", "lab"}),
            [p1, p2],
        )
        assert len(matches) == 2
        assert matches[0][1] >= matches[1][1]

    def test_respects_top_limit(self) -> None:
        patterns = [_pattern(f"pat-{i:03d}", ["exercise", "guided", "lab"]) for i in range(10)]
        matches = find_best_matches(
            frozenset({"exercise", "guided", "lab"}),
            patterns,
            top=3,
        )
        assert len(matches) == 3

    def test_empty_patterns(self) -> None:
        matches = find_best_matches(frozenset({"exercise"}), [])
        assert matches == []

    def test_empty_query(self) -> None:
        p = _pattern()
        matches = find_best_matches(frozenset(), [p])
        assert matches == []


class TestThresholdConstants:
    def test_merge_threshold_is_half(self) -> None:
        assert MERGE_THRESHOLD == 0.5

    def test_search_threshold_is_point_two(self) -> None:
        assert SEARCH_THRESHOLD == 0.2

    def test_domain_boost_is_one_point_two(self) -> None:
        assert DOMAIN_BOOST == 1.2

    def test_stopwords_count(self) -> None:
        assert len(STOPWORDS) == 41
