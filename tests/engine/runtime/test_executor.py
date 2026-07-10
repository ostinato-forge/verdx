"""Tests for execute_plan() — the main execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
from unittest.mock import patch

from verdx.contracts.check import (
    CheckSpec,
    PythonAdapter,
    SarifParser,
    ToolAdapter,
)
from verdx.contracts.dimensions import DimLevel
from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.executor import (
    ExecutionResult,
    _build_check_context,
    _resolve_worker_count,
    execute_plan,
)
from verdx.engine.runtime.planner import resolve_gate_plan
from verdx.engine.runtime.store.check_cache import CheckCacheStore

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _python_adapter(**overrides: Any) -> PythonAdapter:
    defaults = {"import_path": "tests.engine.runtime.test_executor:_passing_check"}
    return PythonAdapter(**(defaults | overrides))


def _tool_adapter(**overrides: Any) -> ToolAdapter:
    defaults: dict[str, Any] = {"command": ("echo", "ok"), "parser": SarifParser()}
    return ToolAdapter(**(defaults | overrides))


def _check_spec(**overrides: Any) -> CheckSpec:
    defaults: dict[str, Any] = {
        "check_id": "test-check",
        "domain": "training",
        "title": "Test Check",
        "category": "structure",
        "version": "1.0.0",
        "adapter": _python_adapter(),
        "dimensions": {"correctness": DimLevel.HIGH},
    }
    return CheckSpec(**(defaults | overrides))


def _domain_spec(
    name: str = "training",
    markers: tuple[str, ...] = ("outline.yml",),
    checks: tuple[CheckSpec, ...] | None = None,
) -> DomainSpec:
    if checks is None:
        checks = (_check_spec(domain=name),)

    @dataclass
    class _FakeConfig:
        version: int = 1

    return DomainSpec(
        name=name,
        markers=markers,
        load_default_config=_FakeConfig,
        load_checks=lambda: checks,
        load_profiles=lambda: (),
        discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
    )


def _plan(tmp_path: Path, **overrides: Any):
    (tmp_path / "outline.yml").write_text("")
    defaults: dict[str, Any] = {
        "target": tmp_path,
        "available_domains": {"training": _domain_spec()},
    }
    return resolve_gate_plan(**(defaults | overrides))


# -- Test check functions (must be importable for the pool worker) --


def _passing_check(ctx):
    return ValidatorResult(
        validator_id=ctx.check_id,
        validator_version=ctx.check_version,
        drafts=[],
        checks_run=3,
        checks_passed=3,
        duration_ms=10,
        status="completed",
    )


def _failing_check(ctx):
    raise RuntimeError("intentional failure")


# ===========================================================================
# Worker count resolution
# ===========================================================================


class TestWorkerCount:
    def test_auto_with_small_batch(self) -> None:
        count = _resolve_worker_count("auto", 2)
        assert count >= 1
        assert count <= 2

    def test_explicit_count(self) -> None:
        assert _resolve_worker_count(4, 10) == 4

    def test_minimum_one(self) -> None:
        assert _resolve_worker_count(0, 10) == 1
        assert _resolve_worker_count("auto", 0) == 1


# ===========================================================================
# CheckContext construction
# ===========================================================================


class TestBuildCheckContext:
    def test_basic_construction(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path)
        check = plan.checks[0]
        ctx = _build_check_context(plan, check, None, None)
        assert ctx.run_id == plan.run_id
        assert ctx.target_key == plan.target_key
        assert ctx.check_id == check.check_id
        assert ctx.domain == check.domain
        assert ctx.scratch_dir is None

    def test_scratch_dir_created(self, tmp_path: Path) -> None:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        plan = _plan(tmp_path)
        check = plan.checks[0]
        ctx = _build_check_context(plan, check, None, scratch)
        assert ctx.scratch_dir is not None
        assert ctx.scratch_dir.exists()


# ===========================================================================
# execute_plan
# ===========================================================================


class TestExecutePlan:
    def test_empty_plan(self, tmp_path: Path) -> None:
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=())},
        )
        result = execute_plan(plan)
        assert isinstance(result, ExecutionResult)
        assert result.results == ()
        assert result.recovery_items == ()
        assert result.duration_ms >= 0

    def test_skipped_check_produces_skipped_result(self, tmp_path: Path) -> None:
        check = _check_spec(
            capabilities=frozenset({"network"}),
        )
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=(check,))},
        )
        assert plan.checks[0].skip_code is not None
        result = execute_plan(plan)
        assert len(result.results) == 1
        assert result.results[0].status == "skipped"
        assert len(result.skipped_checks) == 1

    @patch("verdx.engine.runtime.executor.ProcessPoolExecutor")
    def test_pool_safe_check_dispatched_to_pool(self, mock_pool_cls, tmp_path: Path) -> None:
        mock_pool = mock_pool_cls.return_value.__enter__.return_value
        mock_future = mock_pool.submit.return_value
        mock_future.result.return_value = ValidatorResult(
            validator_id="test-check",
            validator_version="1.0.0",
            drafts=[],
            checks_run=1,
            checks_passed=1,
            duration_ms=5,
            status="completed",
        ).to_dict()

        mock_pool.submit.return_value = mock_future
        mock_pool_cls.return_value.__enter__.return_value = mock_pool

        def fake_as_completed(futures):
            return list(futures.keys())

        with patch("verdx.engine.runtime.executor.as_completed", fake_as_completed):
            plan = _plan(tmp_path)
            result = execute_plan(plan)

        assert mock_pool.submit.called
        assert len(result.results) >= 1

    def test_non_pool_safe_runs_inline(self, tmp_path: Path) -> None:
        adapter = _python_adapter(pool_safe=False)
        check = _check_spec(adapter=adapter)
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=(check,))},
        )
        result = execute_plan(plan)
        assert len(result.results) == 1
        assert result.results[0].status == "completed"
        assert result.results[0].checks_run == 3

    def test_inline_check_failure_produces_recovery(self, tmp_path: Path) -> None:
        adapter = PythonAdapter(
            import_path="tests.engine.runtime.test_executor:_failing_check",
            pool_safe=False,
        )
        check = _check_spec(adapter=adapter)
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=(check,))},
        )
        result = execute_plan(plan)
        assert len(result.results) == 1
        assert result.results[0].status == "failed"
        assert len(result.recovery_items) == 1
        assert result.recovery_items[0].kind == "retryable"

    def test_cache_hit_skips_execution(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path)
        check = plan.checks[0]
        cache = CheckCacheStore(tmp_path)
        cached_result = ValidatorResult(
            validator_id=check.check_id,
            validator_version=check.check_version,
            drafts=[],
            checks_run=99,
            checks_passed=99,
            duration_ms=1,
            status="completed",
        )
        cache.put(
            plan.target_content_hash,
            check.check_id,
            check.check_version,
            check.config_hash,
            cached_result,
        )
        result = execute_plan(plan, cache_store=cache)
        assert result.cache_hits == 1
        assert result.cache_misses == 0
        assert result.results[0].checks_run == 99

    def test_cache_write_on_success(self, tmp_path: Path) -> None:
        adapter = _python_adapter(pool_safe=False)
        check = _check_spec(adapter=adapter)
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=(check,))},
        )
        cache = CheckCacheStore(tmp_path)
        result = execute_plan(plan, cache_store=cache)
        assert result.cache_misses == 1

        resolved = plan.checks[0]
        hit = cache.get(
            plan.target_content_hash,
            resolved.check_id,
            resolved.check_version,
            resolved.config_hash,
        )
        assert hit is not None
        assert hit.status == "completed"

    def test_no_cache_flag_bypasses_cache(self, tmp_path: Path) -> None:
        adapter = _python_adapter(pool_safe=False)
        check = _check_spec(adapter=adapter)
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=(check,))},
            no_cache=True,
        )
        cache = CheckCacheStore(tmp_path)
        cached_result = ValidatorResult(
            validator_id="test-check",
            validator_version="1.0.0",
            drafts=[],
            checks_run=99,
            checks_passed=99,
            duration_ms=1,
            status="completed",
        )
        cache.put(
            plan.target_content_hash,
            plan.checks[0].check_id,
            plan.checks[0].check_version,
            plan.checks[0].config_hash,
            cached_result,
        )
        result = execute_plan(plan, cache_store=cache)
        assert result.cache_hits == 0
        assert result.results[0].checks_run == 3

    def test_multiple_checks_all_execute(self, tmp_path: Path) -> None:
        checks = (
            _check_spec(
                check_id="c1",
                adapter=PythonAdapter(
                    import_path="tests.engine.runtime.test_executor:_passing_check",
                    pool_safe=False,
                ),
            ),
            _check_spec(
                check_id="c2",
                adapter=PythonAdapter(
                    import_path="tests.engine.runtime.test_executor:_passing_check",
                    pool_safe=False,
                ),
            ),
        )
        plan = _plan(
            tmp_path,
            available_domains={"training": _domain_spec(checks=checks)},
        )
        result = execute_plan(plan)
        assert len(result.results) == 2
        assert all(r.status == "completed" for r in result.results)
