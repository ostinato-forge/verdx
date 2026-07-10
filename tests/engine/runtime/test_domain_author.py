"""Tests for domain authoring checks."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from verdx.engine.runtime.domain_author import AUTHOR_SCHEMA_VERSION, author_check

if TYPE_CHECKING:
    from pathlib import Path


def _write_domain_package(
    root: Path,
    *,
    module_name: str = "sample_domain",
    domain_name: str = "sample",
    entry_name: str = "sample",
    include_quick: bool = True,
) -> None:
    (root / "src" / module_name).mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{module_name.replace("_", "-")}"
            version = "0.1.0"

            [project.entry-points."verdx.domains"]
            {entry_name} = "{module_name}:register_domain"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    profiles = '({"name": "quick", "config": {}},)' if include_quick else "()"
    (root / "src" / module_name / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from verdx.contracts.check import CheckSpec, PythonAdapter
            from verdx.contracts.config import DomainConfig, EvidenceLimits
            from verdx.contracts.dimensions import DimLevel, EscalationLevel
            from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
            from verdx.contracts.escalation import EscalationPolicy, EscalationRule, RuleMatch
            from verdx.contracts.validator import ValidatorResult


            def sample_validator(_context):
                return ValidatorResult(
                    validator_id="{domain_name}.sample",
                    validator_version="1.0.0",
                    drafts=[],
                    checks_run=1,
                    checks_passed=1,
                    duration_ms=0,
                    status="completed",
                )


            sample_validator.validator_id = "{domain_name}.sample"
            sample_validator.validator_version = "1.0.0"


            def _config():
                return DomainConfig(
                    version=1,
                    policy=EscalationPolicy(
                        version=1,
                        floors=(),
                        rules=(
                            EscalationRule(
                                name="default",
                                match=RuleMatch(
                                    quantifier="any",
                                    dimension="*",
                                    level=DimLevel.LOW,
                                ),
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


            def _checks():
                return (
                    CheckSpec(
                        check_id="{domain_name}.sample",
                        domain="{domain_name}",
                        title="Sample Check",
                        category="correctness",
                        version="1.0.0",
                        adapter=PythonAdapter(import_path="{module_name}:sample_validator"),
                        dimensions={{"correctness": DimLevel.LOW}},
                    ),
                )


            def register_domain():
                return DomainSpec(
                    name="{domain_name}",
                    markers=("sample.yml",),
                    load_default_config=_config,
                    load_checks=_checks,
                    load_profiles=lambda: {profiles},
                    discover_subjects=lambda _path, _target_key: SubjectDiscoveryResult(),
                )
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_missing_pyproject_fails(tmp_path: Path) -> None:
    report = author_check(tmp_path)

    assert report.status == "fail"
    assert report.schema_version == AUTHOR_SCHEMA_VERSION
    assert report.checks[0].name == "pyproject"


def test_valid_domain_package_passes(tmp_path: Path) -> None:
    _write_domain_package(tmp_path)

    report = author_check(tmp_path)

    assert report.status == "pass"
    assert report.entry_points == {"sample": "sample_domain:register_domain"}
    assert report.to_dict()["schema_version"] == AUTHOR_SCHEMA_VERSION


def test_missing_quick_profile_warns(tmp_path: Path) -> None:
    _write_domain_package(tmp_path, module_name="no_quick_domain", include_quick=False)

    report = author_check(tmp_path)

    assert report.status == "warn"
    assert any(
        check.name.endswith(".profiles") and check.status == "warn" for check in report.checks
    )


def test_entry_name_must_match_domain_name(tmp_path: Path) -> None:
    _write_domain_package(tmp_path, module_name="mismatch_domain", entry_name="public")

    report = author_check(tmp_path)

    assert report.status == "fail"
    assert any("must match DomainSpec.name" in check.message for check in report.checks)
