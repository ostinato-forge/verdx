"""Plan executor — runs checks from a ResolvedGatePlan.

Takes a fully resolved plan (from ``resolve_gate_plan()``) and executes
all non-skipped checks.  Python checks dispatch to a ProcessPoolExecutor
(if pool-safe) or run inline; ToolAdapter checks run as subprocesses.

The executor owns cache lookup/write and error recovery.  It does NOT
derive assessments, scores, or reports — that is PR 4's job.
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from verdx.contracts.assessment import RecoveryItem
from verdx.contracts.check import PythonAdapter, ToolAdapter
from verdx.contracts.context import CheckContext
from verdx.contracts.domain import SubjectResolver
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.executor_worker import run_python_check
from verdx.engine.runtime.tool_runner import run_tool_check

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from verdx.contracts.plan import ResolvedCheck, ResolvedGatePlan
    from verdx.contracts.subjects import ValidationSubject
    from verdx.engine.runtime.store.check_cache import CheckCacheStore

_DEFAULT_TIMEOUT = 300
_HARD_TIMEOUT_MULTIPLIER = 2


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Output of execute_plan().  Fed to the assessor in PR 4."""

    results: tuple[ValidatorResult, ...] = ()
    recovery_items: tuple[RecoveryItem, ...] = ()
    skipped_checks: tuple[ResolvedCheck, ...] = ()
    cache_hits: int = 0
    cache_misses: int = 0
    duration_ms: int = 0


def execute_plan(
    plan: ResolvedGatePlan,
    *,
    cache_store: CheckCacheStore | None = None,
    scratch_base: Path | None = None,
    subjects: Mapping[str, ValidationSubject] | None = None,
) -> ExecutionResult:
    """Execute all checks in a resolved plan.

    Returns an ExecutionResult with all ValidatorResults, recovery items,
    and cache statistics.  Does not derive assessments or scores.
    """
    overall_start = time.monotonic()

    skipped: list[ResolvedCheck] = []
    pool_batch: list[ResolvedCheck] = []
    sequential_batch: list[ResolvedCheck] = []

    for check in plan.checks:
        if check.skip_code is not None:
            skipped.append(check)
            continue
        adapter = check.spec.adapter
        if isinstance(adapter, PythonAdapter) and adapter.pool_safe:
            pool_batch.append(check)
        else:
            sequential_batch.append(check)

    results: list[ValidatorResult] = []
    recovery_items: list[RecoveryItem] = []
    cache_hits = 0
    cache_misses = 0

    to_execute_pool: list[ResolvedCheck] = []
    to_execute_seq: list[ResolvedCheck] = []

    for check in pool_batch:
        cached = _cache_lookup(plan, check, cache_store)
        if cached is not None:
            results.append(cached)
            cache_hits += 1
        else:
            to_execute_pool.append(check)
            cache_misses += 1

    for check in sequential_batch:
        cached = _cache_lookup(plan, check, cache_store)
        if cached is not None:
            results.append(cached)
            cache_hits += 1
        else:
            to_execute_seq.append(check)
            cache_misses += 1

    if to_execute_pool:
        pool_results, pool_recovery = _run_pool_batch(
            plan, to_execute_pool, subjects, scratch_base, cache_store
        )
        results.extend(pool_results)
        recovery_items.extend(pool_recovery)

    for check in to_execute_seq:
        result, recovery = _run_sequential_check(plan, check, subjects, scratch_base)
        results.append(result)
        if recovery is not None:
            recovery_items.append(recovery)
        _cache_write(plan, check, result, cache_store)

    skipped_results = [_make_skipped_result(c) for c in skipped]
    results.extend(skipped_results)

    elapsed_ms = int((time.monotonic() - overall_start) * 1000)
    return ExecutionResult(
        results=tuple(results),
        recovery_items=tuple(recovery_items),
        skipped_checks=tuple(skipped),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        duration_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_lookup(
    plan: ResolvedGatePlan,
    check: ResolvedCheck,
    cache_store: CheckCacheStore | None,
) -> ValidatorResult | None:
    if cache_store is None or plan.no_cache or not check.cacheable:
        return None
    return cache_store.get(
        plan.target_content_hash,
        check.check_id,
        check.check_version,
        check.config_hash,
    )


def _cache_write(
    plan: ResolvedGatePlan,
    check: ResolvedCheck,
    result: ValidatorResult,
    cache_store: CheckCacheStore | None,
) -> None:
    if (
        cache_store is not None
        and not plan.no_cache
        and check.cacheable
        and result.status == "completed"
    ):
        cache_store.put(
            plan.target_content_hash,
            check.check_id,
            check.check_version,
            check.config_hash,
            result,
        )


# ---------------------------------------------------------------------------
# CheckContext construction
# ---------------------------------------------------------------------------


def _build_check_context(
    plan: ResolvedGatePlan,
    check: ResolvedCheck,
    subjects: Mapping[str, ValidationSubject] | None,
    scratch_base: Path | None,
) -> CheckContext:
    scratch_dir = None
    if scratch_base is not None:
        scratch_dir = scratch_base / check.check_id
        scratch_dir.mkdir(parents=True, exist_ok=True)

    subject_map: dict[str, Any] = dict(subjects) if subjects else {}
    resolver = SubjectResolver(subjects=subject_map)

    return CheckContext(
        run_id=plan.run_id,
        target_key=plan.target_key,
        target=plan.target,
        target_content_hash=plan.target_content_hash,
        domain=check.domain,
        check_id=check.check_id,
        check_version=check.check_version,
        config=check.effective_config,
        timeout_seconds=check.spec.timeout_seconds or _DEFAULT_TIMEOUT,
        scratch_dir=scratch_dir,
        subjects=subject_map,
        subject_resolver=resolver,
    )


# ---------------------------------------------------------------------------
# Pool execution
# ---------------------------------------------------------------------------


def _resolve_worker_count(workers: int | str, batch_size: int) -> int:
    if isinstance(workers, int):
        return max(1, workers)
    return max(1, min(batch_size, os.cpu_count() or 1))


def _run_pool_batch(
    plan: ResolvedGatePlan,
    checks: list[ResolvedCheck],
    subjects: Mapping[str, ValidationSubject] | None,
    scratch_base: Path | None,
    cache_store: CheckCacheStore | None,
) -> tuple[list[ValidatorResult], list[RecoveryItem]]:
    results: list[ValidatorResult] = []
    recovery_items: list[RecoveryItem] = []

    max_workers = _resolve_worker_count(plan.workers, len(checks))

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures: dict[Future[dict[str, Any]], ResolvedCheck] = {}
        for check in checks:
            ctx = _build_check_context(plan, check, subjects, scratch_base)
            timeout = check.spec.timeout_seconds or _DEFAULT_TIMEOUT
            adapter = check.spec.adapter
            assert isinstance(adapter, PythonAdapter)
            future = pool.submit(
                run_python_check,
                adapter.import_path,
                ctx.to_worker_dict(),
                timeout,
            )
            futures[future] = check

        for future in as_completed(futures):
            check = futures[future]
            result, recovery = _collect_future(future, check, plan.run_id)
            results.append(result)
            if recovery is not None:
                recovery_items.append(recovery)
            _cache_write(plan, check, result, cache_store)

    return results, recovery_items


def _collect_future(
    future: Future[dict[str, Any]],
    check: ResolvedCheck,
    run_id: str,
) -> tuple[ValidatorResult, RecoveryItem | None]:
    hard_timeout = (check.spec.timeout_seconds or _DEFAULT_TIMEOUT) * _HARD_TIMEOUT_MULTIPLIER

    try:
        result_dict = future.result(timeout=hard_timeout)
        result = ValidatorResult.from_dict(result_dict)

        if result.status == "failed":
            recovery = RecoveryItem(
                id=f"recovery-{uuid.uuid4().hex[:8]}",
                kind="retryable",
                description=f"Check {check.check_id} failed: {result.error}",
                validator_id=check.check_id,
                context={"error": result.error or ""},
            )
            return result, recovery

        return result, None

    except TimeoutError:
        result = ValidatorResult(
            validator_id=check.check_id,
            validator_version=check.check_version,
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=hard_timeout * 1000,
            status="failed",
            error=f"worker hard timeout after {hard_timeout}s",
        )
        recovery = RecoveryItem(
            id=f"recovery-{uuid.uuid4().hex[:8]}",
            kind="blocked",
            description=f"Check {check.check_id} timed out after {hard_timeout}s",
            validator_id=check.check_id,
            context={"timeout_seconds": hard_timeout},
        )
        return result, recovery

    except Exception as exc:
        result = ValidatorResult(
            validator_id=check.check_id,
            validator_version=check.check_version,
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=0,
            status="failed",
            error=f"worker crashed: {exc}",
        )
        recovery = RecoveryItem(
            id=f"recovery-{uuid.uuid4().hex[:8]}",
            kind="retryable",
            description=f"Check {check.check_id} worker crashed: {exc}",
            validator_id=check.check_id,
            context={"error": str(exc)},
        )
        return result, recovery


# ---------------------------------------------------------------------------
# Sequential execution
# ---------------------------------------------------------------------------


def _run_sequential_check(
    plan: ResolvedGatePlan,
    check: ResolvedCheck,
    subjects: Mapping[str, ValidationSubject] | None,
    scratch_base: Path | None,
) -> tuple[ValidatorResult, RecoveryItem | None]:
    ctx = _build_check_context(plan, check, subjects, scratch_base)
    adapter = check.spec.adapter

    if isinstance(adapter, ToolAdapter):
        return run_tool_check(adapter, ctx, check)

    return _execute_python_check_inline(adapter, ctx, check)


def _execute_python_check_inline(
    adapter: PythonAdapter,
    ctx: CheckContext,
    check: ResolvedCheck,
) -> tuple[ValidatorResult, RecoveryItem | None]:
    import importlib

    start = time.monotonic()
    try:
        module_path, qualname = adapter.import_path.split(":", 1)
        mod = importlib.import_module(module_path)
        obj: Any = mod
        for part in qualname.split("."):
            obj = getattr(obj, part)

        result = obj(ctx)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not isinstance(result, ValidatorResult):
            failed = ValidatorResult(
                validator_id=check.check_id,
                validator_version=check.check_version,
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=elapsed_ms,
                status="failed",
                error=f"check returned {type(result).__name__}, expected ValidatorResult",
            )
            recovery = RecoveryItem(
                id=f"recovery-{uuid.uuid4().hex[:8]}",
                kind="retryable",
                description=f"Check {check.check_id} returned wrong type",
                validator_id=check.check_id,
                context={"error": failed.error or ""},
            )
            return failed, recovery

        return result, None

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        failed = ValidatorResult(
            validator_id=check.check_id,
            validator_version=check.check_version,
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=elapsed_ms,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        recovery = RecoveryItem(
            id=f"recovery-{uuid.uuid4().hex[:8]}",
            kind="retryable",
            description=f"Check {check.check_id} failed: {exc}",
            validator_id=check.check_id,
            context={"error": str(exc)},
        )
        return failed, recovery


# ---------------------------------------------------------------------------
# Skip handling
# ---------------------------------------------------------------------------


def _make_skipped_result(check: ResolvedCheck) -> ValidatorResult:
    return ValidatorResult(
        validator_id=check.check_id,
        validator_version=check.check_version,
        drafts=[],
        checks_run=0,
        checks_passed=0,
        duration_ms=0,
        status="skipped",
        skip_reason=check.skip_reason,
    )
