"""Tests for the validator protocol and result model."""

from __future__ import annotations

import pytest

from verdx.contracts.validator import ValidationContext, ValidatorResult


class TestValidationContext:
    def test_construction(self):
        ctx = ValidationContext(
            run_id="run-001",
            target_key="sample/ch04/ge01",
            target_path="/tmp/target",
            target_content_hash="sha256:abc",
            config={"key": "value"},
            previous_observations=[],
            timeout_seconds=60,
        )
        assert ctx.run_id == "run-001"
        assert ctx.timeout_seconds == 60

    def test_frozen(self):
        ctx = ValidationContext(
            run_id="run-001",
            target_key="sample/ch04/ge01",
            target_path="/tmp/target",
            target_content_hash="sha256:abc",
            config={},
            previous_observations=[],
            timeout_seconds=60,
        )
        with pytest.raises(AttributeError):
            ctx.run_id = "run-002"  # type: ignore[misc]


class TestValidatorResult:
    def test_valid_completed(self):
        r = ValidatorResult(
            validator_id="yaml-lint",
            validator_version="1.0.0",
            drafts=[],
            checks_run=10,
            checks_passed=10,
            duration_ms=150,
            status="completed",
        )
        assert r.status == "completed"

    def test_valid_failed(self):
        r = ValidatorResult(
            validator_id="yaml-lint",
            validator_version="1.0.0",
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=100,
            status="failed",
            error="crash",
        )
        assert r.error == "crash"

    def test_valid_skipped(self):
        r = ValidatorResult(
            validator_id="yaml-lint",
            validator_version="1.0.0",
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=0,
            status="skipped",
            skip_reason="not applicable",
        )
        assert r.skip_reason == "not applicable"

    def test_invalid_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            ValidatorResult(
                validator_id="yaml-lint",
                validator_version="1.0.0",
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=0,
                status="invalid",
            )

    def test_empty_validator_id(self):
        with pytest.raises(ValueError, match="validator_id"):
            ValidatorResult(
                validator_id="",
                validator_version="1.0.0",
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=0,
                status="completed",
            )

    def test_metadata_optional(self):
        r = ValidatorResult(
            validator_id="yaml-lint",
            validator_version="1.0.0",
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=0,
            status="completed",
            metadata={"extra": "data"},
        )
        assert r.metadata == {"extra": "data"}
