"""Tests for the domains CLI group."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from tests.engine.runtime.test_domain_author import _write_domain_package
from verdx.contracts.check import CheckSpec, PythonAdapter
from verdx.contracts.config import DomainConfig, EvidenceLimits
from verdx.contracts.dimensions import DimLevel, EscalationLevel
from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
from verdx.contracts.escalation import EscalationPolicy, EscalationRule, RuleMatch
from verdx.engine.runtime.cli import main
from verdx.engine.runtime.domains import _reset_discovery_cache

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    _reset_discovery_cache()
    yield
    _reset_discovery_cache()


def _config() -> DomainConfig:
    return DomainConfig(
        version=1,
        policy=EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="default",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L1,
                ),
            ),
        ),
        critical_validators=frozenset(),
        dimensions=("correctness",),
        evidence_limits=EvidenceLimits(
            max_evidence_bytes=65536,
            max_string_length=4096,
            max_data_entries=50,
        ),
        timeout_seconds=300,
        dimension_map=None,
        scoring_config=None,
    )


def _spec(name: str = "sample") -> DomainSpec:
    check = CheckSpec(
        check_id=f"{name}.sample",
        domain=name,
        title="Sample Check",
        category="correctness",
        version="1.0.0",
        adapter=PythonAdapter(import_path="sample_domain:sample_validator"),
        dimensions={"correctness": DimLevel.LOW},
    )
    return DomainSpec(
        name=name,
        markers=("sample.yml",),
        load_default_config=_config,
        load_checks=lambda: (check,),
        load_profiles=lambda: ({"name": "quick", "config": {}},),
        discover_subjects=lambda _path, _target_key: SubjectDiscoveryResult(),
    )


def test_domains_list_json(runner: CliRunner) -> None:
    ep = MagicMock()
    ep.name = "sample"
    ep.load.return_value = lambda: _spec("sample")

    with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep]):
        result = runner.invoke(main, ["domains", "list", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["domains"] == ["sample"]
    assert data["load_errors"] == {}


def test_domains_show_json(runner: CliRunner) -> None:
    ep = MagicMock()
    ep.name = "sample"
    ep.load.return_value = lambda: _spec("sample")

    with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep]):
        result = runner.invoke(main, ["domains", "show", "sample", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == "verdx.domain-catalog.v1"
    assert data["domain"] == "sample"
    assert data["checks"][0]["check_id"] == "sample.sample"


def test_domains_author_json_success(tmp_path: Path, runner: CliRunner) -> None:
    _write_domain_package(tmp_path)

    result = runner.invoke(main, ["domains", "author", str(tmp_path), "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == "verdx.domain-author.v1"
    assert data["status"] == "pass"


def test_domains_author_failure_exits_one(tmp_path: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["domains", "author", str(tmp_path)])

    assert result.exit_code == 1
    assert "Domain author check: FAIL" in result.output
