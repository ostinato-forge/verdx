"""Tests for pre-execution plan contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdx.contracts.check import CheckSpec, PythonAdapter
from verdx.contracts.dimensions import DimLevel
from verdx.contracts.plan import (
    CapabilityPolicy,
    PlanError,
    ProvenanceEntry,
    ResolvedCheck,
    ResolvedGatePlan,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _resolved_check(**overrides):
    spec = CheckSpec(
        check_id="training.yaml-lint",
        domain="training",
        title="YAML Lint",
        category="correctness",
        version="1.0.0",
        adapter=PythonAdapter(import_path="checks.yaml:validate"),
        dimensions={"correctness": DimLevel.HIGH},
    )
    defaults = {
        "domain": "training",
        "check_id": "training.yaml-lint",
        "check_version": "1.0.0",
        "spec": spec,
        "effective_config": {},
        "config_hash": "sha256:abc",
        "capabilities": frozenset(),
        "critical": False,
        "cacheable": True,
    }
    return ResolvedCheck(**(defaults | overrides))


def _plan(**overrides):
    defaults = {
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "target": Path("/tmp/target"),
        "target_content_hash": "sha256:abc123",
        "timestamp": "2026-05-21T12:00:00Z",
        "domains": ("training",),
        "checks": (_resolved_check(),),
    }
    return ResolvedGatePlan(**(defaults | overrides))


# ===========================================================================
# CapabilityPolicy
# ===========================================================================


class TestCapabilityPolicy:
    def test_defaults_allow_process_and_temp(self):
        p = CapabilityPolicy()
        assert "process" in p.allowed
        assert "filesystem.write-temp" in p.allowed

    def test_defaults_deny_network_and_destructive(self):
        p = CapabilityPolicy()
        assert "network" in p.denied
        assert "destructive" in p.denied

    def test_defaults_allow_live(self):
        p = CapabilityPolicy()
        assert "live" in p.allowed
        assert "live" not in p.denied

    def test_static_only_denies_live(self):
        static = CapabilityPolicy.static_only()
        assert "live" in static.denied
        assert "live" not in static.allowed

    def test_static_only_differs_from_default(self):
        static = CapabilityPolicy.static_only()
        default = CapabilityPolicy()
        assert static.denied != default.denied
        assert "live" in static.denied
        assert "live" not in default.denied

    def test_frozen(self):
        p = CapabilityPolicy()
        with pytest.raises(AttributeError):
            p.allowed = frozenset()  # type: ignore[misc]


# ===========================================================================
# ProvenanceEntry
# ===========================================================================


class TestProvenanceEntry:
    def test_construction(self):
        e = ProvenanceEntry(
            path=("training", "scoring", "decay_factor"),
            source="profile:training-quick",
            operation="replace",
            value_repr=0.85,
        )
        assert e.path == ("training", "scoring", "decay_factor")
        assert e.operation == "replace"

    def test_redact_operation(self):
        e = ProvenanceEntry(
            path=("secrets", "api_key"),
            source="env:VERDX_API_KEY",
            operation="redact",
            value_hash="sha256:redacted",
        )
        assert e.value_repr is None
        assert e.value_hash is not None


# ===========================================================================
# ResolvedCheck
# ===========================================================================


class TestResolvedCheck:
    def test_valid(self):
        rc = _resolved_check()
        assert rc.check_id == "training.yaml-lint"
        assert rc.cacheable is True
        assert rc.skip_code is None

    def test_skipped_check(self):
        rc = _resolved_check(
            skip_code="capability-denied",
            skip_reason="requires network",
        )
        assert rc.skip_code == "capability-denied"


# ===========================================================================
# ResolvedGatePlan
# ===========================================================================


class TestResolvedGatePlan:
    def test_valid(self):
        plan = _plan()
        assert plan.schema_version == "verdx.resolved-plan.v1"
        assert plan.run_id == "run-001"
        assert len(plan.checks) == 1

    def test_empty_run_id_rejected(self):
        with pytest.raises(ValueError, match="run_id must be non-empty"):
            _plan(run_id="")

    def test_empty_target_key_rejected(self):
        with pytest.raises(ValueError, match="target_key must be non-empty"):
            _plan(target_key="")

    def test_empty_content_hash_rejected(self):
        with pytest.raises(ValueError, match="target_content_hash must be non-empty"):
            _plan(target_content_hash="")

    def test_empty_timestamp_rejected(self):
        with pytest.raises(ValueError, match="timestamp must be non-empty"):
            _plan(timestamp="")

    def test_empty_domains_rejected(self):
        with pytest.raises(ValueError, match="domains must be non-empty"):
            _plan(domains=())

    def test_defaults(self):
        plan = _plan()
        assert plan.output_formats == ("markdown",)
        assert plan.workers == "auto"
        assert plan.no_cache is False
        assert plan.static_only is False
        assert plan.plan_only is False

    def test_frozen(self):
        plan = _plan()
        with pytest.raises(AttributeError):
            plan.run_id = "other"  # type: ignore[misc]


# ===========================================================================
# PlanError
# ===========================================================================


class TestPlanError:
    def test_message_format(self):
        err = PlanError("MISSING_DOMAIN", "no domain matched target")
        assert err.code == "MISSING_DOMAIN"
        assert "[MISSING_DOMAIN]" in str(err)
        assert "no domain matched target" in str(err)

    def test_is_exception(self):
        assert issubclass(PlanError, Exception)
