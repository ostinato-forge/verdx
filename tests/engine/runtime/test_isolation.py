"""Tests for process isolation — subprocess validator wrapper and worker."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from verdx.contracts.dimensions import Confidence, DimLevel, Severity
from verdx.contracts.observation import Evidence, ObservationDraft
from verdx.contracts.validator import ValidationContext, ValidatorResult
from verdx.engine.runtime.isolation import (
    SubprocessValidator,
    deserialize_result,
    serialize_context,
    serialize_result,
)


def _fake_worker_validator(ctx: ValidationContext) -> ValidatorResult:
    """Module-level importable validator for isolation worker tests."""
    return ValidatorResult(
        validator_id="fake-v",
        validator_version="1.0.0",
        drafts=[],
        checks_run=3,
        checks_passed=3,
        duration_ms=10,
        status="completed",
    )


_fake_worker_validator.validator_id = "fake-v"  # type: ignore[attr-defined]
_fake_worker_validator.validator_version = "1.0.0"  # type: ignore[attr-defined]


def _ctx() -> ValidationContext:
    return ValidationContext(
        run_id="run-test",
        target_key="course/ch01",
        target_path="/tmp/course",
        target_content_hash="sha256:abc",
        config={"key": "value"},
        previous_observations=[],
        timeout_seconds=300,
    )


def _result_with_drafts() -> ValidatorResult:
    draft = ObservationDraft(
        target_key="course/ch01",
        location="section:1",
        rule_id="test-001",
        category="completeness",
        severity=Severity.HIGH,
        confidence=Confidence.CONFIRMED,
        dimensions={"completeness": DimLevel.HIGH},
        summary="Missing section",
        evidence=(Evidence(source="test", kind="text", details="evidence here", data={}),),
    )
    return ValidatorResult(
        validator_id="test-validator",
        validator_version="1.0.0",
        drafts=[draft],
        checks_run=5,
        checks_passed=4,
        duration_ms=123,
        status="completed",
        subject_keys_checked=("sub-1", "sub-2"),
        subject_keys_skipped=("sub-3",),
    )


def _clean_result() -> ValidatorResult:
    return ValidatorResult(
        validator_id="test-validator",
        validator_version="1.0.0",
        drafts=[],
        checks_run=10,
        checks_passed=10,
        duration_ms=50,
        status="completed",
    )


class TestSerialization:
    def test_context_round_trip(self):
        ctx = _ctx()
        serialized = serialize_context(ctx)
        data = json.loads(serialized)
        assert data["run_id"] == "run-test"
        assert data["target_key"] == "course/ch01"
        assert data["config"] == {"key": "value"}
        assert data["timeout_seconds"] == 300

    def test_result_round_trip_clean(self):
        result = _clean_result()
        serialized = serialize_result(result)
        restored = deserialize_result(serialized)
        assert restored.validator_id == result.validator_id
        assert restored.checks_run == result.checks_run
        assert restored.checks_passed == result.checks_passed
        assert restored.status == "completed"
        assert restored.drafts == []

    def test_result_round_trip_with_drafts(self):
        result = _result_with_drafts()
        serialized = serialize_result(result)
        restored = deserialize_result(serialized)
        assert restored.validator_id == "test-validator"
        assert len(restored.drafts) == 1
        assert restored.drafts[0].rule_id == "test-001"
        assert restored.drafts[0].severity == Severity.HIGH
        assert restored.drafts[0].dimensions == {"completeness": DimLevel.HIGH}
        assert len(restored.drafts[0].evidence) == 1
        assert restored.subject_keys_checked == ("sub-1", "sub-2")
        assert restored.subject_keys_skipped == ("sub-3",)

    def test_result_round_trip_failed(self):
        result = ValidatorResult(
            validator_id="broken",
            validator_version="0.1.0",
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=0,
            status="failed",
            error="crashed hard",
        )
        serialized = serialize_result(result)
        restored = deserialize_result(serialized)
        assert restored.status == "failed"
        assert restored.error == "crashed hard"

    def test_result_round_trip_skipped(self):
        result = ValidatorResult(
            validator_id="optional",
            validator_version="1.0.0",
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=0,
            status="skipped",
            skip_reason="not applicable",
        )
        serialized = serialize_result(result)
        restored = deserialize_result(serialized)
        assert restored.status == "skipped"
        assert restored.skip_reason == "not applicable"


class TestSubprocessValidator:
    def test_successful_execution(self):
        sv = SubprocessValidator(
            validator_id="test-v",
            validator_version="1.0.0",
            domain="training",
        )
        result_json = serialize_result(_clean_result())
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=result_json,
            stderr="",
        )

        with patch("verdx.engine.runtime.isolation.subprocess.run", return_value=completed):
            result = sv(_ctx())

        assert result.status == "completed"
        assert result.checks_run == 10
        assert result.validator_id == "test-validator"

    def test_successful_with_drafts(self):
        sv = SubprocessValidator(
            validator_id="test-v",
            validator_version="1.0.0",
            domain="training",
        )
        result_json = serialize_result(_result_with_drafts())
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=result_json,
            stderr="",
        )

        with patch("verdx.engine.runtime.isolation.subprocess.run", return_value=completed):
            result = sv(_ctx())

        assert result.status == "completed"
        assert len(result.drafts) == 1
        assert result.drafts[0].severity == Severity.HIGH

    def test_timeout_returns_failed_result(self):
        sv = SubprocessValidator(
            validator_id="slow-v",
            validator_version="1.0.0",
            domain="training",
            timeout_seconds=5,
        )

        with patch(
            "verdx.engine.runtime.isolation.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5),
        ):
            result = sv(_ctx())

        assert result.status == "failed"
        assert "timed out" in result.error
        assert result.validator_id == "slow-v"
        assert result.duration_ms == 5000

    def test_crash_returns_failed_result(self):
        sv = SubprocessValidator(
            validator_id="crash-v",
            validator_version="1.0.0",
            domain="training",
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Traceback: boom",
        )

        with patch("verdx.engine.runtime.isolation.subprocess.run", return_value=completed):
            result = sv(_ctx())

        assert result.status == "failed"
        assert "exited with code 1" in result.error
        assert "Traceback: boom" in result.error
        assert result.validator_id == "crash-v"

    def test_passes_domain_and_validator_id_to_worker(self):
        sv = SubprocessValidator(
            validator_id="my-validator",
            validator_version="1.0.0",
            domain="custom-domain",
            timeout_seconds=60,
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=serialize_result(_clean_result()),
            stderr="",
        )

        with patch(
            "verdx.engine.runtime.isolation.subprocess.run",
            return_value=completed,
        ) as mock_run:
            sv(_ctx())

        cmd = mock_run.call_args[0][0]
        assert "--domain" in cmd
        assert "custom-domain" in cmd
        assert "--validator-id" in cmd
        assert "my-validator" in cmd

    def test_has_validator_attributes(self):
        sv = SubprocessValidator(
            validator_id="test-v",
            validator_version="2.0.0",
            domain="training",
        )
        assert sv.validator_id == "test-v"
        assert sv.validator_version == "2.0.0"


class TestIsolationWorker:
    def test_worker_runs_validator(self, tmp_path):
        import io

        from verdx.contracts.check import check_from_validator
        from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
        from verdx.engine.runtime.isolation_worker import main

        check_spec = check_from_validator(_fake_worker_validator, {}, "test-domain")

        fake_domain = DomainSpec(
            name="test-domain",
            markers=(),
            load_default_config=lambda: None,
            load_checks=lambda: (check_spec,),
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )

        ctx_json = serialize_context(_ctx())

        with (
            patch("sys.argv", ["worker", "--domain", "test-domain", "--validator-id", "fake-v"]),
            patch("sys.stdin", io.StringIO(ctx_json)),
            patch("verdx.engine.runtime.isolation_worker.get_domain", return_value=fake_domain),
            patch("builtins.print") as mock_print,
        ):
            main()

        output = mock_print.call_args[0][0]
        result = deserialize_result(output)
        assert result.status == "completed"
        assert result.validator_id == "fake-v"
        assert result.checks_run == 3

    def test_worker_unknown_validator(self):
        import io

        from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
        from verdx.engine.runtime.isolation_worker import main

        fake_domain = DomainSpec(
            name="test-domain",
            markers=(),
            load_default_config=lambda: None,
            load_checks=lambda: (),
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )

        ctx_json = serialize_context(_ctx())

        with (
            patch("sys.argv", ["worker", "--domain", "test-domain", "--validator-id", "missing"]),
            patch("sys.stdin", io.StringIO(ctx_json)),
            patch("verdx.engine.runtime.isolation_worker.get_domain", return_value=fake_domain),
            patch("builtins.print") as mock_print,
        ):
            main()

        output = mock_print.call_args[0][0]
        result = deserialize_result(output)
        assert result.status == "failed"
        assert "not found" in result.error
