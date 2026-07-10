"""Tests for fixer protocol, registry, and discovery."""

from __future__ import annotations

from pathlib import Path

from verdx.fixers import FixerSpec, discover_fixers, find_fixer
from verdx.types import FixAction, Observation


def _obs(category: str = "structure", signature: str = "sig1") -> Observation:
    return Observation(
        severity="high",
        category=category,
        location=None,
        message="test issue",
        fixable=False,
        signature=signature,
    )


class _StubFixer:
    def can_fix(self, observation: Observation) -> bool:
        return True

    def fix(self, target: Path, observation: Observation) -> FixAction | None:
        return FixAction(
            observation_signature=observation.signature,
            fixer_id="stub",
            description="fixed it",
        )


class _SelectiveFixer:
    def __init__(self, keyword: str) -> None:
        self._keyword = keyword

    def can_fix(self, observation: Observation) -> bool:
        return self._keyword in observation.message

    def fix(self, target: Path, observation: Observation) -> FixAction | None:
        return FixAction(
            observation_signature=observation.signature,
            fixer_id="selective",
            description=f"fixed {self._keyword}",
        )


class TestFixerSpec:
    def test_matches_by_category(self) -> None:
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_StubFixer(),
        )
        assert spec.matches(_obs(category="structure"))
        assert not spec.matches(_obs(category="style"))

    def test_matches_checks_can_fix(self) -> None:
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_SelectiveFixer("missing"),
        )
        obs_match = Observation(
            severity="high",
            category="structure",
            location=None,
            message="missing file",
            fixable=False,
            signature="s1",
        )
        obs_no_match = Observation(
            severity="high",
            category="structure",
            location=None,
            message="bad format",
            fixable=False,
            signature="s2",
        )
        assert spec.matches(obs_match)
        assert not spec.matches(obs_no_match)


class TestFindFixer:
    def test_finds_matching(self) -> None:
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_StubFixer(),
        )
        result = find_fixer(_obs(category="structure"), [spec])
        assert result is spec

    def test_returns_none_when_no_match(self) -> None:
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"style"}),
            fixer=_StubFixer(),
        )
        assert find_fixer(_obs(category="structure"), [spec]) is None

    def test_returns_first_match(self) -> None:
        s1 = FixerSpec(
            fixer_id="f1",
            description="first",
            categories=frozenset({"structure"}),
            fixer=_StubFixer(),
        )
        s2 = FixerSpec(
            fixer_id="f2",
            description="second",
            categories=frozenset({"structure"}),
            fixer=_StubFixer(),
        )
        assert find_fixer(_obs(), [s1, s2]) is s1


class TestDiscoverFixers:
    def test_returns_list(self) -> None:
        result = discover_fixers()
        assert isinstance(result, list)
