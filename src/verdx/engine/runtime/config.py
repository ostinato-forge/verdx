"""Domain configuration loader.

Loads YAML config, validates all fields, and produces typed objects.
Invalid config fails before any run starts.

Schema types (EvidenceLimits, ScoringConfig, ConfigError, DomainConfig) live
in contracts/config.py — this module handles loading and validation only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from verdx.contracts.config import ConfigError, DomainConfig, EvidenceLimits, ScoringConfig
from verdx.contracts.escalation import EscalationPolicy

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "DomainConfig",
    "load_config",
    "load_domain_config",
    "load_escalation_policy",
    "parse_escalation_policy",
]

CONFIG_SCHEMA_VERSION = 1


def load_config(path: Path) -> DomainConfig:
    """Load and validate a domain configuration file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to read config: {exc}") from exc

    return _validate(raw, source=str(path))


def load_domain_config(domain: str) -> DomainConfig:
    """Load a domain's default configuration via its entry point.

    The engine names no domain.  The caller passes the registered
    domain name; this function resolves it through the domain registry
    and returns the ``DomainConfig`` the domain publishes, so the engine
    never imports any domain package directly.
    """
    from verdx.engine.runtime.domains import get_domain

    return get_domain(domain).load_default_config()


def load_escalation_policy(path: str) -> EscalationPolicy:
    """Load an escalation policy from a standalone YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return parse_escalation_policy(raw)


def parse_escalation_policy(raw: Any) -> EscalationPolicy:
    """Parse an escalation policy from a parsed YAML dict."""
    if not isinstance(raw, dict):
        raise ValueError("escalation policy must be a YAML mapping")

    policy_data = raw.get("escalation_policy", raw)
    if not isinstance(policy_data, dict):
        raise ValueError("escalation_policy must be a mapping")
    if "version" not in policy_data:
        raise ValueError("escalation policy must include 'version'")
    if "rules" not in policy_data:
        raise ValueError("escalation policy must include 'rules'")

    return EscalationPolicy.from_dict(policy_data)


def _validate(raw: Any, source: str) -> DomainConfig:
    """Validate raw config dict and return typed DomainConfig."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: config must be a YAML mapping")

    version = raw.get("version")
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{source}: unsupported config version {version!r}, expected {CONFIG_SCHEMA_VERSION}"
        )

    policy = _validate_policy(raw.get("escalation_policy"), source)
    critical = _validate_critical_validators(raw.get("critical_validators", []), source)
    dimensions = _validate_dimensions(raw.get("dimensions", []), source)
    limits = _validate_evidence_limits(raw.get("evidence_limits", {}), source)
    timeout = _validate_timeout(raw.get("timeout_seconds"), source)
    dimension_map = _validate_dimension_map(raw.get("dimension_map"), dimensions, source)
    scoring_config = _validate_scoring(raw.get("scoring"), source)

    return DomainConfig(
        version=version,
        policy=policy,
        critical_validators=critical,
        dimensions=dimensions,
        evidence_limits=limits,
        timeout_seconds=timeout,
        dimension_map=dimension_map,
        scoring_config=scoring_config,
    )


def _validate_policy(raw: Any, source: str) -> EscalationPolicy:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: escalation_policy must be a mapping")

    try:
        return EscalationPolicy.from_dict(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise ConfigError(f"{source}: invalid escalation_policy: {exc}") from exc


def _validate_critical_validators(raw: Any, source: str) -> frozenset[str]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: critical_validators must be a list")

    for item in raw:
        if not isinstance(item, str) or not item:
            raise ConfigError(f"{source}: critical_validators entries must be non-empty strings")

    return frozenset(raw)


def _validate_dimensions(raw: Any, source: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: dimensions must be a list")

    for item in raw:
        if not isinstance(item, str) or not item:
            raise ConfigError(f"{source}: dimension names must be non-empty strings")

    return tuple(raw)


def _validate_evidence_limits(raw: Any, source: str) -> EvidenceLimits:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: evidence_limits must be a mapping")

    max_bytes = raw.get("max_evidence_bytes", 65536)
    max_string = raw.get("max_string_length", 4096)
    max_entries = raw.get("max_data_entries", 50)

    for name, val in [
        ("max_evidence_bytes", max_bytes),
        ("max_string_length", max_string),
        ("max_data_entries", max_entries),
    ]:
        if not isinstance(val, int) or val <= 0:
            raise ConfigError(f"{source}: evidence_limits.{name} must be a positive integer")

    return EvidenceLimits(
        max_evidence_bytes=max_bytes,
        max_string_length=max_string,
        max_data_entries=max_entries,
    )


def _validate_timeout(raw: Any, source: str) -> int:
    if raw is None:
        return 300
    if not isinstance(raw, int) or raw <= 0:
        raise ConfigError(f"{source}: timeout_seconds must be a positive integer")
    return raw


def _validate_dimension_map(
    raw: Any,
    dimensions: tuple[str, ...],
    source: str,
) -> dict[str, list[str]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: dimension_map must be a mapping")

    dim_set = set(dimensions)
    for category, dim_list in raw.items():
        if not isinstance(dim_list, list):
            raise ConfigError(f"{source}: dimension_map.{category} must be a list")
        for dim in dim_list:
            if dim not in dim_set:
                raise ConfigError(
                    f"{source}: dimension_map.{category} references unknown dimension '{dim}'"
                )

    return dict(raw)


def _validate_scoring(raw: Any, source: str) -> ScoringConfig | None:
    """Validate scoring configuration section.

    Returns None if section is absent. Validates:
    - severity_penalties: non-negative integers
    - coverage_penalties: non-negative integers
    - decay_factor: 0.0-1.0
    - min_fraction: 0.0-1.0
    - cap_multiplier: > 0.0
    """
    if raw is None:
        return None

    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: scoring must be a mapping")

    severity_penalties = None
    if "severity_penalties" in raw:
        severity_penalties = _validate_severity_penalties(
            raw["severity_penalties"],
            source,
        )

    coverage_penalties = None
    if "coverage_penalties" in raw:
        coverage_penalties = _validate_coverage_penalties(
            raw["coverage_penalties"],
            source,
        )

    decay_factor = None
    if "decay_factor" in raw:
        decay_factor = raw["decay_factor"]
        if not isinstance(decay_factor, (int, float)):
            raise ConfigError(f"{source}: scoring.decay_factor must be a number")
        if not 0.0 <= decay_factor <= 1.0:
            raise ConfigError(f"{source}: scoring.decay_factor must be between 0.0 and 1.0")

    min_fraction = None
    if "min_fraction" in raw:
        min_fraction = raw["min_fraction"]
        if not isinstance(min_fraction, (int, float)):
            raise ConfigError(f"{source}: scoring.min_fraction must be a number")
        if not 0.0 <= min_fraction <= 1.0:
            raise ConfigError(f"{source}: scoring.min_fraction must be between 0.0 and 1.0")

    cap_multiplier = None
    if "cap_multiplier" in raw:
        cap_multiplier = raw["cap_multiplier"]
        if not isinstance(cap_multiplier, (int, float)):
            raise ConfigError(f"{source}: scoring.cap_multiplier must be a number")
        if cap_multiplier <= 0.0:
            raise ConfigError(f"{source}: scoring.cap_multiplier must be greater than 0.0")

    return ScoringConfig(
        severity_penalties=severity_penalties,
        coverage_penalties=coverage_penalties,
        decay_factor=decay_factor,
        min_fraction=min_fraction,
        cap_multiplier=cap_multiplier,
    )


def _validate_severity_penalties(raw: Any, source: str) -> dict[Any, dict[Any, int]]:
    """Validate severity_penalties structure."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: scoring.severity_penalties must be a mapping")

    from verdx.contracts.dimensions import Confidence, Severity

    result = {}
    for severity_str, confidence_map in raw.items():
        try:
            severity = Severity[severity_str.upper()]
        except KeyError:
            raise ConfigError(
                f"{source}: scoring.severity_penalties contains unknown severity '{severity_str}'"
            ) from None

        if not isinstance(confidence_map, dict):
            raise ConfigError(
                f"{source}: scoring.severity_penalties.{severity_str} must be a mapping"
            )

        conf_result = {}
        for conf_str, penalty in confidence_map.items():
            try:
                confidence = Confidence[conf_str.upper()]
            except KeyError:
                raise ConfigError(
                    f"{source}: scoring.severity_penalties.{severity_str} "
                    f"contains unknown confidence '{conf_str}'"
                ) from None

            if not isinstance(penalty, int) or penalty < 0:
                raise ConfigError(
                    f"{source}: scoring.severity_penalties.{severity_str}.{conf_str} "
                    f"must be a non-negative integer"
                )

            conf_result[confidence] = penalty

        result[severity] = conf_result

    return result


def _validate_coverage_penalties(raw: Any, source: str) -> dict[str, int]:
    """Validate coverage_penalties structure."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: scoring.coverage_penalties must be a mapping")

    valid_keys = {
        "failed_critical",
        "skipped_critical",
        "failed_non_critical",
        "skipped_non_critical",
        "truncated_high_critical",
    }

    result = {}
    for key, value in raw.items():
        if key not in valid_keys:
            raise ConfigError(f"{source}: scoring.coverage_penalties contains unknown key '{key}'")

        if not isinstance(value, int) or value < 0:
            raise ConfigError(
                f"{source}: scoring.coverage_penalties.{key} must be a non-negative integer"
            )

        result[key] = value

    return result
