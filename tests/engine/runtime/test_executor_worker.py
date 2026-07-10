"""Tests for executor_worker — the ProcessPoolExecutor entrypoint."""

from __future__ import annotations

from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.executor_worker import run_python_check


def _context_dict(**overrides: object) -> dict:
    defaults = {
        "run_id": "run-test123",
        "target_key": "/tmp/target",
        "target": "/tmp/target",
        "target_content_hash": "sha256:abc",
        "domain": "training",
        "check_id": "test-check",
        "check_version": "1.0.0",
        "config": {},
        "timeout_seconds": 300,
        "scratch_dir": None,
        "subjects": {},
    }
    return {**defaults, **overrides}


# -- Test validator functions (must be importable by the worker) --


def passing_check(ctx):
    return ValidatorResult(
        validator_id=ctx.check_id,
        validator_version=ctx.check_version,
        drafts=[],
        checks_run=5,
        checks_passed=5,
        duration_ms=10,
        status="completed",
    )


def raising_check(ctx):
    raise RuntimeError("intentional test error")


def bad_return_check(ctx):
    return "not a ValidatorResult"


class TestRunPythonCheck:
    def test_successful_execution(self) -> None:
        result_dict = run_python_check(
            f"{__name__}:passing_check",
            _context_dict(),
            300,
        )
        result = ValidatorResult.from_dict(result_dict)
        assert result.status == "completed"
        assert result.checks_run == 5
        assert result.checks_passed == 5

    def test_import_failure(self) -> None:
        result_dict = run_python_check(
            "nonexistent.module:check_fn",
            _context_dict(),
            300,
        )
        result = ValidatorResult.from_dict(result_dict)
        assert result.status == "failed"
        assert "ModuleNotFoundError" in (result.error or "")

    def test_check_raises_returns_failed(self) -> None:
        result_dict = run_python_check(
            f"{__name__}:raising_check",
            _context_dict(),
            300,
        )
        result = ValidatorResult.from_dict(result_dict)
        assert result.status == "failed"
        assert "intentional test error" in (result.error or "")

    def test_bad_return_type(self) -> None:
        result_dict = run_python_check(
            f"{__name__}:bad_return_check",
            _context_dict(),
            300,
        )
        result = ValidatorResult.from_dict(result_dict)
        assert result.status == "failed"
        assert "expected ValidatorResult" in (result.error or "")

    def test_soft_timeout(self) -> None:
        import time

        def slow_check(ctx):
            time.sleep(0.05)
            return ValidatorResult(
                validator_id=ctx.check_id,
                validator_version=ctx.check_version,
                drafts=[],
                checks_run=1,
                checks_passed=1,
                duration_ms=50,
                status="completed",
            )

        import tests.engine.runtime.test_executor_worker as mod

        mod.slow_check = slow_check

        result_dict = run_python_check(
            f"{__name__}:slow_check",
            _context_dict(),
            0,
        )
        result = ValidatorResult.from_dict(result_dict)
        assert result.status == "failed"
        assert "soft timeout" in (result.error or "")

    def test_bad_import_path_format(self) -> None:
        result_dict = run_python_check(
            "no_colon_here",
            _context_dict(),
            300,
        )
        result = ValidatorResult.from_dict(result_dict)
        assert result.status == "failed"

    def test_result_is_always_dict(self) -> None:
        result_dict = run_python_check(
            f"{__name__}:passing_check",
            _context_dict(),
            300,
        )
        assert isinstance(result_dict, dict)
        assert "validator_id" in result_dict

    def test_duration_tracked(self) -> None:
        result_dict = run_python_check(
            f"{__name__}:passing_check",
            _context_dict(),
            300,
        )
        assert result_dict["duration_ms"] >= 0
