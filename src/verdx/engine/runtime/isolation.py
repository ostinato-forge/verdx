"""Process isolation for validators.

Wraps validators to run in separate subprocesses, providing crash
and timeout isolation. The SubprocessValidator serializes the
ValidationContext to JSON, spawns a worker process, and deserializes
the ValidatorResult from stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from verdx.contracts.observation import ObservationDraft
from verdx.contracts.validator import ValidatorResult

if TYPE_CHECKING:
    from verdx.contracts.validator import ValidationContext


def serialize_context(ctx: ValidationContext) -> str:
    return json.dumps(asdict(ctx))


def deserialize_result(data: str) -> ValidatorResult:
    d = json.loads(data)
    drafts = [ObservationDraft.from_dict(draft) for draft in d.pop("drafts", [])]
    return ValidatorResult(
        validator_id=d["validator_id"],
        validator_version=d["validator_version"],
        drafts=drafts,
        checks_run=d["checks_run"],
        checks_passed=d["checks_passed"],
        duration_ms=d["duration_ms"],
        status=d["status"],
        error=d.get("error"),
        skip_reason=d.get("skip_reason"),
        subject_keys_checked=tuple(d.get("subject_keys_checked", ())),
        subject_keys_skipped=tuple(d.get("subject_keys_skipped", ())),
        metadata=d.get("metadata"),
    )


def serialize_result(result: ValidatorResult) -> str:
    return json.dumps(
        {
            "validator_id": result.validator_id,
            "validator_version": result.validator_version,
            "drafts": [d.to_dict() for d in result.drafts],
            "checks_run": result.checks_run,
            "checks_passed": result.checks_passed,
            "duration_ms": result.duration_ms,
            "status": result.status,
            "error": result.error,
            "skip_reason": result.skip_reason,
            "subject_keys_checked": list(result.subject_keys_checked),
            "subject_keys_skipped": list(result.subject_keys_skipped),
            "metadata": result.metadata,
        }
    )


@dataclass
class SubprocessValidator:
    """Wraps a validator to run in a separate subprocess."""

    validator_id: str
    validator_version: str
    domain: str
    timeout_seconds: int = 300

    def __call__(self, ctx: ValidationContext) -> ValidatorResult:
        cmd = [
            sys.executable,
            "-m",
            "verdx.engine.runtime.isolation_worker",
            "--domain",
            self.domain,
            "--validator-id",
            self.validator_id,
        ]
        ctx_json = serialize_context(ctx)

        try:
            proc = subprocess.run(
                cmd,
                input=ctx_json,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ValidatorResult(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=self.timeout_seconds * 1000,
                status="failed",
                error=f"subprocess timed out after {self.timeout_seconds}s",
            )

        if proc.returncode != 0:
            return ValidatorResult(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                drafts=[],
                checks_run=0,
                checks_passed=0,
                duration_ms=0,
                status="failed",
                error=f"subprocess exited with code {proc.returncode}: {proc.stderr.strip()}",
            )

        return deserialize_result(proc.stdout)
