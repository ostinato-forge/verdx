"""Subprocess worker for isolated validator execution.

Entry point: python -m verdx.runtime.isolation_worker

Reads a ValidationContext from stdin (JSON), loads the named validator
from the specified domain, executes it, and writes the ValidatorResult
to stdout (JSON).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys

from verdx.contracts.check import PythonAdapter
from verdx.contracts.validator import ValidationContext
from verdx.engine.runtime.domains import get_domain
from verdx.engine.runtime.isolation import serialize_result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single validator in an isolated process.",
    )
    parser.add_argument("--domain", required=True)
    parser.add_argument("--validator-id", required=True)
    args = parser.parse_args()

    ctx_data = json.loads(sys.stdin.read())
    ctx = ValidationContext(
        run_id=ctx_data["run_id"],
        target_key=ctx_data["target_key"],
        target_path=ctx_data["target_path"],
        target_content_hash=ctx_data["target_content_hash"],
        config=ctx_data["config"],
        previous_observations=ctx_data["previous_observations"],
        timeout_seconds=ctx_data["timeout_seconds"],
    )

    domain = get_domain(args.domain)
    checks = domain.load_checks()

    validator_func = None
    for check in checks:
        if check.check_id == args.validator_id and isinstance(check.adapter, PythonAdapter):
            module_path, qualname = check.adapter.import_path.split(":", 1)
            mod = importlib.import_module(module_path)
            validator_func = getattr(mod, qualname)
            break

    if validator_func is None:
        from verdx.contracts.validator import ValidatorResult

        print(
            serialize_result(
                ValidatorResult(
                    validator_id=args.validator_id,
                    validator_version="0.0.0",
                    drafts=[],
                    checks_run=0,
                    checks_passed=0,
                    duration_ms=0,
                    status="failed",
                    error=f"validator '{args.validator_id}' not found in domain '{args.domain}'",
                )
            )
        )
        return

    result = validator_func(ctx)
    print(serialize_result(result))


if __name__ == "__main__":
    main()
