"""Process-pool worker for pool-safe Python checks.

``run_python_check`` is the entrypoint called by ProcessPoolExecutor
in executor.py.  It must be a top-level function (picklable), stateless,
and import-safe.

Data crosses the process boundary as plain dicts (JSON-safe),
not dataclass instances, to avoid pickle issues with slotted dataclasses.
"""

from __future__ import annotations

import importlib
import time
from typing import Any


def run_python_check(
    import_path: str,
    context_dict: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Execute a Python check in a worker process.

    Returns ValidatorResult.to_dict() — always a dict, never raises.
    """
    from verdx.contracts.context import CheckContext
    from verdx.contracts.validator import ValidatorResult

    check_id = context_dict.get("check_id", "unknown")
    check_version = context_dict.get("check_version", "0.0.0")

    start = time.monotonic()
    try:
        ctx = CheckContext.from_worker_dict(context_dict)

        module_path, qualname = import_path.split(":", 1)
        mod = importlib.import_module(module_path)
        check_fn = _resolve_qualname(mod, qualname)

        result = check_fn(ctx)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not isinstance(result, ValidatorResult):
            return ValidatorResult(
                validator_id=check_id,
                validator_version=check_version,
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=elapsed_ms,
                status="failed",
                error=f"check returned {type(result).__name__}, expected ValidatorResult",
            ).to_dict()

        if elapsed_ms > timeout_seconds * 1000:
            return ValidatorResult(
                validator_id=check_id,
                validator_version=check_version,
                drafts=list(result.drafts),
                checks_run=result.checks_run,
                checks_passed=result.checks_passed,
                duration_ms=elapsed_ms,
                status="failed",
                error=f"exceeded soft timeout ({elapsed_ms}ms > {timeout_seconds * 1000}ms)",
            ).to_dict()

        return result.to_dict()

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ValidatorResult(
            validator_id=check_id,
            validator_version=check_version,
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=elapsed_ms,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        ).to_dict()


def _resolve_qualname(mod: Any, qualname: str) -> Any:
    """Resolve a dotted qualname within a module."""
    obj = mod
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj
