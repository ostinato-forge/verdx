"""Tests for configuration loading: escalation policy and domain config."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from verdx.contracts.config import ConfigError
from verdx.contracts.dimensions import DimLevel, EscalationLevel
from verdx.engine.runtime.config import (
    DomainConfig,
    load_config,
    load_domain_config,
    load_escalation_policy,
    parse_escalation_policy,
)
from verdx.engine.validation.escalation import evaluate

# ---------------------------------------------------------------------------
# parse_escalation_policy (no filesystem)
# ---------------------------------------------------------------------------


class TestParseEscalationPolicy:
    def test_parse_minimal_policy(self):
        raw = {
            "escalation_policy": {
                "version": 1,
                "rules": [
                    {
                        "name": "fallback",
                        "match": {"quantifier": "all", "dimension": "*", "level": "LOW"},
                        "level": "L0",
                    }
                ],
            }
        }
        policy = parse_escalation_policy(raw)
        assert policy.version == 1
        assert len(policy.rules) == 1
        assert policy.rules[0].name == "fallback"

    def test_parse_full_default_policy(self):
        raw = {
            "escalation_policy": {
                "version": 1,
                "floors": [
                    {"category": "certification-alignment", "minimum": "L3"},
                    {"category": "external-commitment", "minimum": "L4"},
                ],
                "rules": [
                    {
                        "name": "blocking",
                        "match": {"quantifier": "any", "dimension": "*", "level": "BLOCKING"},
                        "level": "L4",
                    },
                    {
                        "name": "high-risk",
                        "match": {"quantifier": "any", "dimension": "*", "level": "HIGH"},
                        "level": "L3",
                    },
                    {
                        "name": "multi-medium",
                        "match": {
                            "quantifier": "count",
                            "dimension": "*",
                            "level": "MEDIUM",
                            "threshold": 2,
                        },
                        "level": "L2",
                    },
                    {
                        "name": "medium-risk",
                        "match": {"quantifier": "any", "dimension": "*", "level": "MEDIUM"},
                        "level": "L1",
                    },
                    {
                        "name": "low-risk",
                        "match": {"quantifier": "all", "dimension": "*", "level": "LOW"},
                        "level": "L0",
                    },
                ],
            }
        }
        policy = parse_escalation_policy(raw)
        assert policy.version == 1
        assert len(policy.floors) == 2
        assert len(policy.rules) == 5

        dims = {"correctness": DimLevel.HIGH}
        assert evaluate(policy, "general", dims) == EscalationLevel.L3
        assert evaluate(policy, "certification-alignment", dims) == EscalationLevel.L3

        dims_low = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "certification-alignment", dims_low) == EscalationLevel.L3
        assert evaluate(policy, "external-commitment", dims_low) == EscalationLevel.L4

    def test_parse_without_wrapper_key(self):
        raw = {
            "version": 1,
            "rules": [
                {
                    "name": "fallback",
                    "match": {"quantifier": "all", "dimension": "*", "level": "LOW"},
                    "level": "L0",
                }
            ],
        }
        policy = parse_escalation_policy(raw)
        assert policy.version == 1

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError, match="YAML mapping"):
            parse_escalation_policy("not a dict")

    def test_rejects_missing_version(self):
        raw = {
            "escalation_policy": {
                "rules": [
                    {
                        "name": "x",
                        "match": {
                            "quantifier": "any",
                            "dimension": "*",
                            "level": "LOW",
                        },
                        "level": "L0",
                    }
                ]
            }
        }
        with pytest.raises(ValueError, match="version"):
            parse_escalation_policy(raw)

    def test_rejects_missing_rules(self):
        raw = {"escalation_policy": {"version": 1}}
        with pytest.raises(ValueError, match="rules"):
            parse_escalation_policy(raw)

    def test_rejects_invalid_quantifier(self):
        raw = {
            "version": 1,
            "rules": [
                {
                    "name": "bad",
                    "match": {"quantifier": "maybe", "dimension": "*", "level": "LOW"},
                    "level": "L0",
                }
            ],
        }
        with pytest.raises(ValueError, match="quantifier"):
            parse_escalation_policy(raw)

    def test_rejects_threshold_on_any(self):
        raw = {
            "version": 1,
            "rules": [
                {
                    "name": "bad",
                    "match": {
                        "quantifier": "any",
                        "dimension": "*",
                        "level": "LOW",
                        "threshold": 2,
                    },
                    "level": "L0",
                }
            ],
        }
        with pytest.raises(ValueError, match="threshold is invalid"):
            parse_escalation_policy(raw)


# ---------------------------------------------------------------------------
# load_escalation_policy (filesystem)
# ---------------------------------------------------------------------------


class TestLoadEscalationPolicy:
    def test_load_from_yaml_file(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            escalation_policy:
              version: 1
              floors:
                - category: security
                  minimum: L3
              rules:
                - name: blocking
                  match:
                    quantifier: any
                    dimension: "*"
                    level: BLOCKING
                  level: L4
                - name: fallback
                  match:
                    quantifier: all
                    dimension: "*"
                    level: LOW
                  level: L0
        """)
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(yaml_content)

        policy = load_escalation_policy(str(policy_file))
        assert policy.version == 1
        assert len(policy.rules) == 2
        assert len(policy.floors) == 1

        dims = {"correctness": DimLevel.LOW}
        assert evaluate(policy, "security", dims) == EscalationLevel.L3
        assert evaluate(policy, "general", dims) == EscalationLevel.L0

    def test_load_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            load_escalation_policy("/nonexistent/policy.yaml")

    def test_round_trip_through_yaml(self, tmp_path: Path):
        """Policy loaded from YAML produces identical evaluation to programmatic."""
        yaml_content = textwrap.dedent("""\
            escalation_policy:
              version: 1
              rules:
                - name: high-risk
                  match:
                    quantifier: any
                    dimension: "*"
                    level: HIGH
                  level: L3
                - name: medium-risk
                  match:
                    quantifier: any
                    dimension: "*"
                    level: MEDIUM
                  level: L1
                - name: low-risk
                  match:
                    quantifier: all
                    dimension: "*"
                    level: LOW
                  level: L0
        """)
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(yaml_content)
        policy = load_escalation_policy(str(policy_file))

        assert evaluate(policy, "g", {"a": DimLevel.LOW}) == EscalationLevel.L0
        assert evaluate(policy, "g", {"a": DimLevel.MEDIUM}) == EscalationLevel.L1
        assert evaluate(policy, "g", {"a": DimLevel.HIGH}) == EscalationLevel.L3


# ---------------------------------------------------------------------------
# DomainConfig
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = textwrap.dedent("""\
    version: 1
    escalation_policy:
      version: 1
      rules:
        - name: fallback
          match:
            quantifier: all
            dimension: "*"
            level: LOW
          level: L0
    critical_validators: []
    dimensions:
      - correctness
    evidence_limits:
      max_evidence_bytes: 65536
      max_string_length: 4096
      max_data_entries: 50
    timeout_seconds: 300
""")


class TestLoadConfig:
    def test_load_minimal_config(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        cfg = load_config(cfg_file)
        assert isinstance(cfg, DomainConfig)
        assert cfg.version == 1
        assert cfg.timeout_seconds == 300
        assert cfg.dimensions == ("correctness",)
        assert cfg.evidence_limits.max_evidence_bytes == 65536
        assert cfg.critical_validators == frozenset()
        assert cfg.dimension_map is None

    def test_load_with_dimension_map(self, tmp_path: Path):
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            dimension_map:
              structure:
                - correctness
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        cfg = load_config(cfg_file)
        assert cfg.dimension_map == {"structure": ["correctness"]}

    def test_load_with_critical_validators(self, tmp_path: Path):
        content = _MINIMAL_CONFIG.replace(
            "critical_validators: []",
            "critical_validators:\n  - training-structure\n  - training-content",
        )
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        cfg = load_config(cfg_file)
        assert cfg.critical_validators == frozenset(
            {
                "training-structure",
                "training-content",
            }
        )

    def test_policy_is_evaluable(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        cfg = load_config(cfg_file)
        result = evaluate(cfg.policy, "general", {"correctness": DimLevel.LOW})
        assert result == EscalationLevel.L0


class TestLoadDomainConfig:
    """The engine names no domain; load_domain_config resolves by name.

    It looks the domain up in the runtime registry and returns whatever
    ``DomainConfig`` that domain publishes.  The engine has no built-in
    config content of its own, so these tests register a stub domain and
    assert the resolution mechanism, not any domain-specific content.
    """

    @staticmethod
    def _stub_domain(name: str, cfg: DomainConfig) -> object:
        from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult

        return DomainSpec(
            name=name,
            markers=(),
            load_default_config=lambda: cfg,
            load_checks=lambda: (),
            load_profiles=lambda: (),
            discover_subjects=lambda _path, _target: SubjectDiscoveryResult(),
        )

    def _register(self, monkeypatch, specs: dict) -> None:
        """Inject stub domains into the runtime registry for the test."""
        from verdx.engine.runtime import domains as domains_mod

        monkeypatch.setattr(domains_mod, "_discovered", dict(specs))
        monkeypatch.setattr(domains_mod, "_load_errors", {})

    def _config(self, tmp_path: Path) -> DomainConfig:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        return load_config(cfg_file)

    def test_returns_registered_domains_config(self, monkeypatch, tmp_path: Path):
        cfg = self._config(tmp_path)
        self._register(monkeypatch, {"alpha": self._stub_domain("alpha", cfg)})
        result = load_domain_config("alpha")
        assert result is cfg
        assert isinstance(result, DomainConfig)
        assert result.version == 1

    def test_names_no_domain_by_literal(self):
        import inspect

        source = inspect.getsource(load_domain_config)
        assert "training" not in source

    def test_raises_for_unknown_domain(self, monkeypatch):
        from verdx.engine.runtime.domains import UnknownDomainError

        self._register(monkeypatch, {})
        with pytest.raises(UnknownDomainError):
            load_domain_config("nope")


class TestConfigValidationErrors:
    def test_rejects_wrong_version(self, tmp_path: Path):
        content = _MINIMAL_CONFIG.replace("version: 1\n", "version: 99\n", 1)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="unsupported config version"):
            load_config(cfg_file)

    def test_rejects_non_mapping(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("- a list\n")
        with pytest.raises(ConfigError, match="YAML mapping"):
            load_config(cfg_file)

    def test_rejects_malformed_policy(self, tmp_path: Path):
        content = textwrap.dedent("""\
            version: 1
            escalation_policy: not-a-dict
            critical_validators: []
            dimensions:
              - correctness
            evidence_limits:
              max_evidence_bytes: 65536
              max_string_length: 4096
              max_data_entries: 50
            timeout_seconds: 300
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="escalation_policy"):
            load_config(cfg_file)

    def test_rejects_empty_dimension_name(self, tmp_path: Path):
        content = _MINIMAL_CONFIG.replace(
            "dimensions:\n  - correctness",
            'dimensions:\n  - ""',
        )
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="non-empty strings"):
            load_config(cfg_file)

    def test_rejects_negative_evidence_limit(self, tmp_path: Path):
        content = _MINIMAL_CONFIG.replace(
            "max_evidence_bytes: 65536",
            "max_evidence_bytes: -1",
        )
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="positive integer"):
            load_config(cfg_file)

    def test_rejects_zero_timeout(self, tmp_path: Path):
        content = _MINIMAL_CONFIG.replace(
            "timeout_seconds: 300",
            "timeout_seconds: 0",
        )
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="positive integer"):
            load_config(cfg_file)

    def test_rejects_unknown_dimension_in_map(self, tmp_path: Path):
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            dimension_map:
              structure:
                - nonexistent-dimension
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="unknown dimension"):
            load_config(cfg_file)

    def test_rejects_critical_validators_not_list(self, tmp_path: Path):
        content = _MINIMAL_CONFIG.replace(
            "critical_validators: []",
            "critical_validators: not-a-list",
        )
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="must be a list"):
            load_config(cfg_file)

    def test_rejects_nonexistent_file(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="Failed to read"):
            load_config(tmp_path / "nonexistent.yml")


# ---------------------------------------------------------------------------
# Scoring config validation
# ---------------------------------------------------------------------------


class TestScoringConfigValidation:
    def test_no_scoring_section_returns_none(self, tmp_path: Path):
        """When scoring section is absent, scoring_config is None."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        cfg = load_config(cfg_file)
        assert cfg.scoring_config is None

    def test_custom_severity_penalties(self, tmp_path: Path):
        """Custom severity penalties are validated and loaded."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              severity_penalties:
                CRITICAL:
                  CONFIRMED: 50
                  PROBABLE: 35
                  POSSIBLE: 25
                HIGH:
                  CONFIRMED: 40
                  PROBABLE: 28
                  POSSIBLE: 18
                MEDIUM:
                  CONFIRMED: 25
                  PROBABLE: 18
                  POSSIBLE: 12
                LOW:
                  CONFIRMED: 12
                  PROBABLE: 9
                  POSSIBLE: 6
                INFO:
                  CONFIRMED: 4
                  PROBABLE: 3
                  POSSIBLE: 2
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        cfg = load_config(cfg_file)
        assert cfg.scoring_config is not None
        assert cfg.scoring_config.severity_penalties is not None

        from verdx.contracts.dimensions import Confidence, Severity

        assert cfg.scoring_config.severity_penalties[Severity.CRITICAL][Confidence.CONFIRMED] == 50

    def test_custom_coverage_penalties(self, tmp_path: Path):
        """Custom coverage penalties are validated and loaded."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              coverage_penalties:
                failed_critical: 40
                skipped_critical: 30
                failed_non_critical: 15
                skipped_non_critical: 8
                truncated_high_critical: 5
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        cfg = load_config(cfg_file)
        assert cfg.scoring_config is not None
        assert cfg.scoring_config.coverage_penalties is not None
        assert cfg.scoring_config.coverage_penalties["failed_critical"] == 40
        assert cfg.scoring_config.coverage_penalties["truncated_high_critical"] == 5

    def test_custom_diminishing_returns_params(self, tmp_path: Path):
        """Custom decay_factor, min_fraction, cap_multiplier are validated."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              decay_factor: 0.3
              min_fraction: 0.2
              cap_multiplier: 3.0
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        cfg = load_config(cfg_file)
        assert cfg.scoring_config is not None
        assert cfg.scoring_config.decay_factor == 0.3
        assert cfg.scoring_config.min_fraction == 0.2
        assert cfg.scoring_config.cap_multiplier == 3.0

    def test_partial_scoring_config(self, tmp_path: Path):
        """Partial scoring config leaves unset fields as None."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              decay_factor: 0.4
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        cfg = load_config(cfg_file)
        assert cfg.scoring_config is not None
        assert cfg.scoring_config.decay_factor == 0.4
        assert cfg.scoring_config.severity_penalties is None
        assert cfg.scoring_config.coverage_penalties is None

    def test_rejects_negative_penalty(self, tmp_path: Path):
        """Negative penalty values are rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              coverage_penalties:
                failed_critical: -10
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="non-negative integer"):
            load_config(cfg_file)

    def test_rejects_out_of_range_decay_factor(self, tmp_path: Path):
        """decay_factor outside [0.0, 1.0] is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              decay_factor: 1.5
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match=r"between 0\.0 and 1\.0"):
            load_config(cfg_file)

    def test_rejects_negative_decay_factor(self, tmp_path: Path):
        """Negative decay_factor is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              decay_factor: -0.1
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match=r"between 0\.0 and 1\.0"):
            load_config(cfg_file)

    def test_rejects_out_of_range_min_fraction(self, tmp_path: Path):
        """min_fraction outside [0.0, 1.0] is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              min_fraction: 2.0
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match=r"between 0\.0 and 1\.0"):
            load_config(cfg_file)

    def test_rejects_zero_cap_multiplier(self, tmp_path: Path):
        """cap_multiplier <= 0 is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              cap_multiplier: 0.0
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match=r"greater than 0\.0"):
            load_config(cfg_file)

    def test_rejects_negative_cap_multiplier(self, tmp_path: Path):
        """Negative cap_multiplier is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              cap_multiplier: -1.0
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match=r"greater than 0\.0"):
            load_config(cfg_file)

    def test_rejects_unknown_severity(self, tmp_path: Path):
        """Unknown severity level is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              severity_penalties:
                SUPER_CRITICAL:
                  CONFIRMED: 100
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="unknown severity"):
            load_config(cfg_file)

    def test_rejects_unknown_confidence(self, tmp_path: Path):
        """Unknown confidence level is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              severity_penalties:
                CRITICAL:
                  DEFINITELY: 100
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="unknown confidence"):
            load_config(cfg_file)

    def test_rejects_unknown_coverage_penalty_key(self, tmp_path: Path):
        """Unknown coverage penalty key is rejected."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              coverage_penalties:
                unknown_key: 50
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="unknown key"):
            load_config(cfg_file)

    def test_rejects_non_dict_scoring(self, tmp_path: Path):
        """scoring must be a mapping."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring: not-a-dict
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="scoring must be a mapping"):
            load_config(cfg_file)

    def test_rejects_non_dict_severity_penalties(self, tmp_path: Path):
        """severity_penalties must be a mapping."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              severity_penalties: not-a-dict
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="severity_penalties must be a mapping"):
            load_config(cfg_file)

    def test_rejects_non_dict_coverage_penalties(self, tmp_path: Path):
        """coverage_penalties must be a mapping."""
        content = _MINIMAL_CONFIG + textwrap.dedent("""\
            scoring:
              coverage_penalties: not-a-dict
        """)
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(content)
        with pytest.raises(ConfigError, match="coverage_penalties must be a mapping"):
            load_config(cfg_file)
