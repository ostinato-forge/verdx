"""Importable check functions for workflow tests.

The executor resolves checks via ``importlib.import_module`` from
PythonAdapter import paths, so test validators must live in a real
importable module.
"""

from __future__ import annotations

from typing import Any

from verdx.contracts.dimensions import Confidence, DimLevel, Severity
from verdx.contracts.observation import ObservationDraft
from verdx.contracts.validator import ValidatorResult


def passing_check(ctx: Any) -> ValidatorResult:
    return ValidatorResult(
        validator_id="test-check",
        validator_version="1.0.0",
        drafts=[],
        checks_run=1,
        checks_passed=1,
        duration_ms=10,
        status="completed",
    )


passing_check.validator_id = "test-check"  # type: ignore[attr-defined]
passing_check.validator_version = "1.0.0"  # type: ignore[attr-defined]


def failing_check(ctx: Any) -> ValidatorResult:
    return ValidatorResult(
        validator_id="fail-check",
        validator_version="1.0.0",
        drafts=[
            ObservationDraft(
                target_key=ctx.target_key,
                rule_id="fail-check:issue-1",
                category="fail-check",
                summary="test failure",
                severity=Severity.MEDIUM,
                confidence=Confidence.CONFIRMED,
                dimensions={"correctness": DimLevel.MEDIUM},
                location="test.adoc",
            ),
        ],
        checks_run=1,
        checks_passed=0,
        duration_ms=10,
        status="completed",
    )


failing_check.validator_id = "fail-check"  # type: ignore[attr-defined]
failing_check.validator_version = "1.0.0"  # type: ignore[attr-defined]
