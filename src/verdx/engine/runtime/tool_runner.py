"""ToolAdapter execution — subprocess invocation, output parsing, batching.

Runs external tools described by ``ToolAdapter`` check specs.  Handles
file input resolution, batch chunking (``none`` and ``argv`` strategies),
subprocess management, exit code interpretation, and output parsing
(SARIF, JSON path, regex).

Security: never uses ``shell=True``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from typing import TYPE_CHECKING, Any

from verdx.contracts.assessment import RecoveryItem
from verdx.contracts.check import (
    JsonPathParser,
    RegexParser,
    SarifParser,
)
from verdx.contracts.dimensions import Severity
from verdx.contracts.observation import ObservationDraft
from verdx.contracts.validator import ValidatorResult

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from verdx.contracts.check import (
        BatchMode,
        ExitCodePolicy,
        FileInputSpec,
        ToolAdapter,
    )
    from verdx.contracts.context import CheckContext
    from verdx.contracts.plan import ResolvedCheck


_BLOCKED_ENV_VARS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "RUBYLIB",
        "PERL5LIB",
        "PERL5OPT",
        "NODE_OPTIONS",
        "NODE_PATH",
    }
)


def _sanitize_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k.upper() not in _BLOCKED_ENV_VARS}


_SARIF_LEVEL_MAP: dict[str, Severity] = {
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "note": Severity.LOW,
    "none": Severity.INFO,
}


def run_tool_check(
    adapter: ToolAdapter,
    ctx: CheckContext,
    check: ResolvedCheck,
) -> tuple[ValidatorResult, RecoveryItem | None]:
    """Execute a ToolAdapter check and return parsed results."""
    start = time.monotonic()

    files = _resolve_file_inputs(adapter.file_inputs, ctx) if adapter.file_inputs else []
    chunks = _chunk_files(files, adapter.batch)

    all_drafts: list[ObservationDraft] = []
    checks_run = 0

    timeout = adapter.timeout_seconds or ctx.timeout_seconds

    for chunk in chunks:
        cmd = _build_command(adapter, chunk)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **_sanitize_env(adapter.env)} if adapter.env else None,
                cwd=str(ctx.target),
            )
        except FileNotFoundError:
            return _failed_result(check, start, f"command not found: {cmd[0]}", "TOOL_NOT_FOUND")
        except subprocess.TimeoutExpired:
            return _failed_result(check, start, f"timed out after {timeout}s", "VALIDATOR_TIMEOUT")

        exit_status = _classify_exit(proc.returncode, adapter.exit_codes)

        if exit_status == "failure":
            stderr_preview = (proc.stderr or "")[:500]
            return _failed_result(
                check,
                start,
                f"exited with code {proc.returncode}: {stderr_preview}",
                "TOOL_PROTOCOL_ERROR",
            )

        checks_run += 1
        if exit_status in ("success", "findings"):
            try:
                drafts = _parse_output(proc.stdout, adapter, check, ctx)
                all_drafts.extend(drafts)
            except Exception as exc:
                return _failed_result(
                    check,
                    start,
                    f"failed to parse output: {type(exc).__name__}: {exc}",
                    "TOOL_PROTOCOL_ERROR",
                )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    result = ValidatorResult(
        validator_id=check.check_id,
        validator_version=check.check_version,
        drafts=all_drafts,
        checks_run=checks_run,
        checks_passed=checks_run - (1 if all_drafts else 0),
        duration_ms=elapsed_ms,
        status="completed",
    )
    return result, None


# ---------------------------------------------------------------------------
# File input resolution
# ---------------------------------------------------------------------------


def _resolve_file_inputs(file_inputs: FileInputSpec, ctx: CheckContext) -> list[Path]:
    root = ctx.target if file_inputs.root == "target" else ctx.scratch_dir
    if root is None:
        return []

    matched: set[Path] = set()
    for glob_pattern in file_inputs.globs:
        matched.update(root.glob(glob_pattern))

    if file_inputs.excludes:
        excluded: set[Path] = set()
        for exc_glob in file_inputs.excludes:
            excluded.update(root.glob(exc_glob))
        matched -= excluded

    return sorted(matched)


# ---------------------------------------------------------------------------
# Batch chunking
# ---------------------------------------------------------------------------


def _chunk_files(files: list[Path], batch: BatchMode) -> list[list[Path]]:
    if batch.strategy == "none":
        return [files]

    max_files = batch.max_files_per_invocation or len(files) or 1
    max_bytes = batch.max_command_bytes

    chunks: list[list[Path]] = []
    current: list[Path] = []
    current_bytes = 0

    for f in files:
        f_str = str(f)
        f_size = len(f_str) + 1
        if current and (len(current) >= max_files or current_bytes + f_size > max_bytes):
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(f)
        current_bytes += f_size

    if current:
        chunks.append(current)
    return chunks or [[]]


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def _build_command(adapter: ToolAdapter, files: list[Path]) -> list[str]:
    cmd = list(adapter.command)
    if adapter.batch.strategy != "argv" or not files:
        return cmd

    placeholder = adapter.batch.placeholder
    file_strs = [str(f) for f in files]

    for i, arg in enumerate(cmd):
        if placeholder in arg:
            return cmd[:i] + file_strs + cmd[i + 1 :]

    return cmd + file_strs


# ---------------------------------------------------------------------------
# Exit code classification
# ---------------------------------------------------------------------------


def _classify_exit(returncode: int, policy: ExitCodePolicy) -> str:
    if returncode in policy.success:
        return "success"
    if returncode in policy.findings:
        return "findings"
    if returncode in policy.failure:
        return "failure"
    return "failure" if policy.unknown_is_failure else "findings"


# ---------------------------------------------------------------------------
# Output parsing dispatch
# ---------------------------------------------------------------------------


def _parse_output(
    stdout: str,
    adapter: ToolAdapter,
    check: ResolvedCheck,
    ctx: CheckContext,
) -> list[ObservationDraft]:
    parser = adapter.parser
    if isinstance(parser, SarifParser):
        return _parse_sarif(stdout, parser, check, ctx)
    if isinstance(parser, JsonPathParser):
        return _parse_jsonpath(stdout, parser, check, ctx)
    if isinstance(parser, RegexParser):
        return _parse_regex(stdout, parser, check, ctx)
    msg = f"unknown parser kind: {parser.kind!r}"
    raise ValueError(msg)


def _parse_sarif(
    stdout: str,
    parser: SarifParser,
    check: ResolvedCheck,
    ctx: CheckContext,
) -> list[ObservationDraft]:
    data = json.loads(stdout)
    drafts: list[ObservationDraft] = []

    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "unknown")
            message = result.get("message", {}).get("text", "")
            level = result.get("level", "warning")
            severity = parser.severity_map.get(level, _SARIF_LEVEL_MAP.get(level, Severity.MEDIUM))
            location = _extract_sarif_location(result)

            drafts.append(
                ObservationDraft(
                    target_key=ctx.target_key,
                    location=location,
                    rule_id=f"{check.check_id}/{rule_id}",
                    category=check.spec.category,
                    severity=severity,
                    confidence=check.spec.confidence,
                    dimensions=dict(check.spec.dimensions),
                    summary=message or f"Finding from {check.check_id}: {rule_id}",
                )
            )
    return drafts


def _extract_sarif_location(result: dict[str, Any]) -> str | None:
    locations = result.get("locations", [])
    if not locations:
        return None
    phys = locations[0].get("physicalLocation", {})
    artifact: str | None = phys.get("artifactLocation", {}).get("uri")
    region = phys.get("region", {})
    line = region.get("startLine")
    if artifact and line:
        return f"{artifact}:{line}"
    return artifact


def _parse_jsonpath(
    stdout: str,
    parser: JsonPathParser,
    check: ResolvedCheck,
    ctx: CheckContext,
) -> list[ObservationDraft]:
    data = json.loads(stdout)
    observations = _navigate_path(data, parser.observations_path)
    if not isinstance(observations, list):
        return []

    drafts: list[ObservationDraft] = []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        rule_id = _navigate_path(obs, parser.rule_id_path) or "unknown"
        message = _navigate_path(obs, parser.message_path) or ""
        location = _navigate_path(obs, parser.location_path) if parser.location_path else None
        severity_str = _navigate_path(obs, parser.severity_path) if parser.severity_path else None
        severity = _map_severity(severity_str, parser.severity_map, check.spec.severity)

        drafts.append(
            ObservationDraft(
                target_key=ctx.target_key,
                location=str(location) if location else None,
                rule_id=f"{check.check_id}/{rule_id}",
                category=check.spec.category,
                severity=severity,
                confidence=check.spec.confidence,
                dimensions=dict(check.spec.dimensions),
                summary=str(message),
            )
        )
    return drafts


def _navigate_path(data: Any, path: str) -> Any:
    """Simple dot-notation path navigation (no JSONPath library needed)."""
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
    return current


def _parse_regex(
    stdout: str,
    parser: RegexParser,
    check: ResolvedCheck,
    ctx: CheckContext,
) -> list[ObservationDraft]:
    pattern = re.compile(parser.pattern)
    drafts: list[ObservationDraft] = []

    for line in stdout.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        groups = m.groupdict()
        rule_id = groups.get("rule_id", "match")
        message = groups.get("message", line.strip())
        location = groups.get("location")
        severity_str = groups.get("severity")
        severity = _map_severity(severity_str, parser.severity_map, check.spec.severity)

        drafts.append(
            ObservationDraft(
                target_key=ctx.target_key,
                location=location,
                rule_id=f"{check.check_id}/{rule_id}",
                category=check.spec.category,
                severity=severity,
                confidence=check.spec.confidence,
                dimensions=dict(check.spec.dimensions),
                summary=message,
            )
        )
    return drafts


def _map_severity(
    value: Any,
    severity_map: dict[str, Severity] | Any,
    default: Severity,
) -> Severity:
    if value is None:
        return default
    s = str(value).lower()
    if isinstance(severity_map, dict) and s in severity_map:
        return severity_map[s]
    try:
        return Severity(s)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _failed_result(
    check: ResolvedCheck,
    start: float,
    error: str,
    code: str,
) -> tuple[ValidatorResult, RecoveryItem]:
    elapsed_ms = int((time.monotonic() - start) * 1000)
    result = ValidatorResult(
        validator_id=check.check_id,
        validator_version=check.check_version,
        drafts=[],
        checks_run=0,
        checks_passed=0,
        duration_ms=elapsed_ms,
        status="failed",
        error=error,
    )
    recovery = RecoveryItem(
        id=f"recovery-{uuid.uuid4().hex[:8]}",
        kind="blocked" if code in ("VALIDATOR_TIMEOUT", "TOOL_NOT_FOUND") else "retryable",
        description=f"Check {check.check_id}: {error}",
        validator_id=check.check_id,
        context={"error": error, "code": code},
    )
    return result, recovery
