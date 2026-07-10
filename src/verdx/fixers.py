"""Fixer protocol and registry — deterministic pattern-matched fixes.

Domain gates register fixers via ``verdx.fixers`` entry points. Each entry
point is a callable returning a tuple of FixerSpec objects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

from verdx.types import FixAction, Observation

log = logging.getLogger(__name__)


class Fixer(Protocol):
    """Protocol for deterministic fixers."""

    def can_fix(self, observation: Observation) -> bool: ...
    def fix(self, target: Path, observation: Observation) -> FixAction | None: ...


@dataclass(frozen=True, slots=True)
class FixerSpec:
    """A registered fixer with its metadata."""

    fixer_id: str
    description: str
    categories: frozenset[str]
    fixer: Fixer

    def matches(self, observation: Observation) -> bool:
        return observation.category in self.categories and self.fixer.can_fix(observation)


def discover_fixers() -> list[FixerSpec]:
    """Load all fixers from ``verdx.fixers`` entry points."""
    specs: list[FixerSpec] = []
    for ep in entry_points(group="verdx.fixers"):
        try:
            factory = ep.load()
            result = factory()
            if isinstance(result, FixerSpec):
                specs.append(result)
            else:
                specs.extend(result)
        except Exception:
            log.warning("failed to load fixer entry point %s", ep.name, exc_info=True)
    return specs


def find_fixer(observation: Observation, fixers: list[FixerSpec]) -> FixerSpec | None:
    """Find the first fixer that can handle an observation."""
    for spec in fixers:
        if spec.matches(observation):
            return spec
    return None
