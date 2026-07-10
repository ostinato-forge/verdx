"""Configuration schemas for domain gates.

Pure data types only — loading and validation logic lives in runtime/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdx.contracts.dimensions import Confidence, Severity
    from verdx.contracts.escalation import EscalationPolicy


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    max_evidence_bytes: int
    max_string_length: int
    max_data_entries: int


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Configurable scoring parameters. None means use hardcoded defaults."""

    severity_penalties: dict[Severity, dict[Confidence, int]] | None = None
    coverage_penalties: dict[str, int] | None = None
    decay_factor: float | None = None
    min_fraction: float | None = None
    cap_multiplier: float | None = None
    independence_penalty: int | None = None
    dimension_weights: dict[str, float] = field(default_factory=dict)


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(frozen=True, slots=True)
class DomainConfig:
    """Validated domain configuration."""

    version: int
    policy: EscalationPolicy
    critical_validators: frozenset[str]
    dimensions: tuple[str, ...]
    evidence_limits: EvidenceLimits
    timeout_seconds: int
    dimension_map: dict[str, list[str]] | None
    scoring_config: ScoringConfig | None
