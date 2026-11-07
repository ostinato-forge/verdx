"""Plan resolution — produces a fully-resolved ResolvedGatePlan.

``resolve_gate_plan()`` is the single entry point.  It takes raw inputs
(target path, domain specs, capability policy, profiles, config
overrides) and resolves all ambiguity before any validator executes:
domains detected, configs merged with provenance, capabilities checked,
dependencies probed, skips determined.

The planner is side-effect-free except for dependency/version probing
(subprocess calls to check tool availability).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from verdx._content import compute_content_hash
from verdx.contracts.plan import (
    CapabilityPolicy,
    PlanError,
    ProvenanceEntry,
    ResolvedCheck,
    ResolvedGatePlan,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from verdx.contracts.check import ReportFormat
    from verdx.contracts.domain import DomainSpec

_log = logging.getLogger(__name__)


_SECRET_KEY_PATTERN = re.compile(
    r"(secret|password|token|api[_-]?key|credential|private[_-]?key)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_gate_plan(
    *,
    target: Path,
    available_domains: Mapping[str, DomainSpec],
    capability_policy: CapabilityPolicy | None = None,
    state_dir: Path = Path(".verdx"),
    report_dir: Path | None = None,
    requested_domains: tuple[str, ...] = (),
    profiles: tuple[str, ...] = (),
    profile_files: tuple[Path, ...] = (),
    project_config_overrides: Mapping[str, Any] = MappingProxyType({}),
    cli_overrides: Mapping[str, Any] = MappingProxyType({}),
    lock: Any = None,
    locked: bool = False,
    output_formats: tuple[ReportFormat, ...] = ("markdown",),
    threshold: int | None = None,
    static_only: bool = False,
    no_cache: bool = False,
    workers: int | Literal["auto"] = "auto",
    plan_only: bool = False,
    run_id: str | None = None,
    timestamp: str | None = None,
    target_key: str | None = None,
    changed_files: frozenset[str] | None = None,
) -> ResolvedGatePlan:
    """Resolve all ambiguity and produce a ready-to-execute plan.

    Raises ``PlanError`` with a specific code for each failure mode.
    Capability denials and missing dependencies do NOT raise — they
    become ``ResolvedCheck.skip_code`` entries and plan warnings.
    """
    if capability_policy is None:
        capability_policy = CapabilityPolicy.static_only() if static_only else CapabilityPolicy()

    try:
        return _resolve(
            target=target,
            available_domains=available_domains,
            capability_policy=capability_policy,
            state_dir=state_dir,
            report_dir=report_dir,
            requested_domains=requested_domains,
            profiles=profiles,
            profile_files=profile_files,
            project_config_overrides=project_config_overrides,
            cli_overrides=cli_overrides,
            lock=lock,
            locked=locked,
            output_formats=output_formats,
            threshold=threshold,
            static_only=static_only,
            no_cache=no_cache,
            workers=workers,
            plan_only=plan_only,
            run_id=run_id,
            timestamp=timestamp,
            target_key=target_key,
            changed_files=changed_files,
        )
    except PlanError:
        raise
    except Exception as exc:
        raise PlanError("PLAN_INTERNAL_ERROR", str(exc)) from exc


# ---------------------------------------------------------------------------
# Internal resolution pipeline
# ---------------------------------------------------------------------------


def _resolve(
    *,
    target: Path,
    available_domains: Mapping[str, DomainSpec],
    capability_policy: CapabilityPolicy,
    state_dir: Path,
    report_dir: Path | None,
    requested_domains: tuple[str, ...],
    profiles: tuple[str, ...],
    profile_files: tuple[Path, ...],
    project_config_overrides: Mapping[str, Any],
    cli_overrides: Mapping[str, Any],
    lock: Any,
    locked: bool,
    output_formats: tuple[ReportFormat, ...],
    threshold: int | None,
    static_only: bool,
    no_cache: bool,
    workers: int | Literal["auto"],
    plan_only: bool,
    run_id: str | None,
    timestamp: str | None,
    target_key: str | None = None,
    changed_files: frozenset[str] | None = None,
) -> ResolvedGatePlan:
    resolved_target = _validate_target(target)
    domains = _resolve_domains(resolved_target, available_domains, requested_domains)
    _validate_report_dir(report_dir)

    if locked and lock is None:
        raise PlanError("LOCK_STALE", "locked=True but no lock provided")

    all_checks: list[ResolvedCheck] = []
    all_provenance: list[ProvenanceEntry] = []
    all_warnings: list[str] = []
    domain_groups: dict[str, tuple[str, ...]] = {}
    domain_configs: dict[str, Any] = {}
    seen_check_ids: dict[str, str] = {}

    for domain_name in domains:
        spec = available_domains[domain_name]

        domain_default_config = _load_domain_config(spec, domain_name)
        profile_config, profile_provenance, _matched = _load_profiles(
            spec, domain_name, profiles, profile_files
        )
        all_provenance.extend(profile_provenance)

        merged_config, merge_provenance = _merge_config_layers(
            domain_default_config,
            profile_config,
            dict(project_config_overrides),
            dict(cli_overrides),
            domain_name,
        )
        all_provenance.extend(merge_provenance)
        domain_configs[domain_name] = merged_config

        checks = spec.load_checks()
        domain_check_ids: list[str] = []

        for check_spec in checks:
            if check_spec.check_id in seen_check_ids:
                raise PlanError(
                    "DUPLICATE_CHECK_ID",
                    f"check_id {check_spec.check_id!r} appears in both "
                    f"{seen_check_ids[check_spec.check_id]!r} and {domain_name!r}",
                )
            seen_check_ids[check_spec.check_id] = domain_name

            resolved, check_warnings = _resolve_check(
                check_spec, domain_name, merged_config, capability_policy, no_cache
            )
            all_checks.append(resolved)
            all_warnings.extend(check_warnings)
            domain_check_ids.append(check_spec.check_id)

        domain_groups[domain_name] = tuple(domain_check_ids)

    if changed_files is not None:
        all_checks, filter_warnings = _filter_by_changed_files(all_checks, changed_files)
        all_warnings.extend(filter_warnings)

    effective_run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    effective_timestamp = timestamp or datetime.now(tz=UTC).isoformat()
    content_hash = compute_content_hash(resolved_target)
    effective_target_key = target_key or str(resolved_target.resolve())

    lock_digest: str | None = None
    if lock is not None and hasattr(lock, "digest"):
        lock_digest = lock.digest

    return ResolvedGatePlan(
        run_id=effective_run_id,
        target_key=effective_target_key,
        target=resolved_target,
        target_content_hash=content_hash,
        timestamp=effective_timestamp,
        domains=domains,
        checks=tuple(all_checks),
        domain_groups=domain_groups,
        domain_configs=domain_configs,
        capability_policy=capability_policy,
        state_dir=state_dir,
        report_dir=report_dir,
        output_formats=output_formats,
        profiles=profiles,
        lock_digest=lock_digest,
        threshold=threshold,
        workers=workers,
        no_cache=no_cache,
        static_only=static_only,
        plan_only=plan_only,
        provenance=tuple(all_provenance),
        warnings=tuple(all_warnings),
    )


# ---------------------------------------------------------------------------
# Changed-files filter
# ---------------------------------------------------------------------------


def _filter_by_changed_files(
    checks: list[ResolvedCheck],
    changed_files: frozenset[str],
) -> tuple[list[ResolvedCheck], list[str]]:
    """Skip checks whose file_domains don't overlap with changed files.

    A check is skipped only when it declares ``file_domains`` AND none of
    the changed files start with any declared prefix. Checks without
    ``file_domains`` (None) always run — conservative default.
    Already-skipped checks are preserved as-is.
    """
    filtered: list[ResolvedCheck] = []
    warnings: list[str] = []

    for check in checks:
        if check.skip_code is not None:
            filtered.append(check)
            continue

        domains = check.spec.file_domains
        if domains is None:
            filtered.append(check)
            continue

        if any(f.startswith(prefix) for f in changed_files for prefix in domains):
            filtered.append(check)
        else:
            filtered.append(
                dataclasses.replace(
                    check,
                    skip_code="no-changed-overlap",
                    skip_reason=f"no changed files match file_domains {domains!r}",
                )
            )
            warnings.append(f"check {check.check_id!r} skipped: no changed files in {domains!r}")

    return filtered, warnings


# ---------------------------------------------------------------------------
# Step 1: Target validation
# ---------------------------------------------------------------------------


def _validate_target(target: Path) -> Path:
    if not target.exists():
        raise PlanError("TARGET_NOT_FOUND", f"target does not exist: {target}")
    return target


# ---------------------------------------------------------------------------
# Step 2: Domain resolution
# ---------------------------------------------------------------------------


def _resolve_domains(
    target: Path,
    available_domains: Mapping[str, DomainSpec],
    requested_domains: tuple[str, ...],
) -> tuple[str, ...]:
    if requested_domains:
        for name in requested_domains:
            if name not in available_domains:
                known = ", ".join(sorted(available_domains))
                raise PlanError(
                    "UNKNOWN_DOMAIN",
                    f"unknown domain {name!r}; available: {known}",
                )
        return requested_domains

    resolved = target if target.is_dir() else target.parent
    matched: list[str] = []
    for name in sorted(available_domains):
        spec = available_domains[name]
        for marker in spec.markers:
            if (resolved / marker).exists():
                matched.append(name)
                break

    if not matched:
        raise PlanError(
            "NO_DOMAIN_MATCH",
            f"no domain markers found under {resolved}; use --domain to specify explicitly",
        )
    return tuple(matched)


# ---------------------------------------------------------------------------
# Step 3: Domain config loading
# ---------------------------------------------------------------------------


def _load_domain_config(spec: DomainSpec, domain_name: str) -> dict[str, Any]:
    try:
        cfg = spec.load_default_config()
        if dataclasses.is_dataclass(cfg) and not isinstance(cfg, type):
            return dataclasses.asdict(cfg)
        return dict(cfg) if isinstance(cfg, dict) else {}
    except Exception as exc:
        raise PlanError(
            "CONFIG_INVALID",
            f"failed to load default config for domain {domain_name!r}: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Step 4: Profile loading
# ---------------------------------------------------------------------------


def _load_profiles(
    spec: DomainSpec,
    domain_name: str,
    requested_profiles: tuple[str, ...],
    profile_files: tuple[Path, ...],
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[str]]:
    """Load and merge profiles. Returns (merged_config, provenance, matched_profile_names)."""
    if not requested_profiles and not profile_files:
        return {}, [], []

    available_profiles = spec.load_profiles()
    profile_by_name: dict[str, dict[str, Any]] = {}
    for profile in available_profiles:
        name = profile.get("name", "")
        if name:
            profile_by_name[name] = profile

    merged: dict[str, Any] = {}
    provenance: list[ProvenanceEntry] = []
    matched: list[str] = []

    for name in requested_profiles:
        if name not in profile_by_name:
            raise PlanError(
                "PROFILE_NOT_FOUND",
                f"profile {name!r} not found in domain {domain_name!r}; "
                f"available: {', '.join(sorted(profile_by_name))}",
            )
        profile_data = profile_by_name[name]
        config_section = profile_data.get("config", {})
        _deep_merge(
            merged,
            config_section,
            path_prefix=(domain_name,),
            source=f"profile:{name}",
            provenance=provenance,
        )
        matched.append(name)

    for profile_path in profile_files:
        if not profile_path.exists():
            raise PlanError(
                "PROFILE_NOT_FOUND",
                f"profile file does not exist: {profile_path}",
            )
        try:
            import yaml

            with profile_path.open() as f:
                file_data = yaml.safe_load(f) or {}
        except Exception as exc:
            raise PlanError(
                "CONFIG_INVALID",
                f"failed to parse profile file {profile_path}: {exc}",
            ) from exc
        config_section = file_data.get("config", file_data)
        _deep_merge(
            merged,
            config_section,
            path_prefix=(domain_name,),
            source=f"profile-file:{profile_path.name}",
            provenance=provenance,
        )

    return merged, provenance, matched


# ---------------------------------------------------------------------------
# Step 5: Config merging
# ---------------------------------------------------------------------------


def _merge_config_layers(
    domain_defaults: dict[str, Any],
    profile_config: dict[str, Any],
    project_overrides: dict[str, Any],
    cli_overrides: dict[str, Any],
    domain_name: str,
) -> tuple[dict[str, Any], list[ProvenanceEntry]]:
    provenance: list[ProvenanceEntry] = []
    result: dict[str, Any] = {}

    _deep_merge(
        result,
        copy.deepcopy(domain_defaults),
        path_prefix=(domain_name,),
        source=f"domain-default:{domain_name}",
        provenance=provenance,
    )
    _deep_merge(
        result,
        profile_config,
        path_prefix=(domain_name,),
        source="profile-merged",
        provenance=provenance,
    )
    _deep_merge(
        result,
        project_overrides,
        path_prefix=(domain_name,),
        source="project-config",
        provenance=provenance,
    )
    _deep_merge(
        result,
        cli_overrides,
        path_prefix=(domain_name,),
        source="cli-override",
        provenance=provenance,
    )

    return result, provenance


def _deep_merge(
    base: dict[str, Any],
    overlay: dict[str, Any],
    *,
    path_prefix: tuple[str, ...],
    source: str,
    provenance: list[ProvenanceEntry],
) -> None:
    """Recursively merge overlay into base, tracking provenance."""
    for key, value in overlay.items():
        current_path = (*path_prefix, key)
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            provenance.append(
                ProvenanceEntry(
                    path=current_path,
                    source=source,
                    operation="deep-merge",
                )
            )
            _deep_merge(
                base[key],
                value,
                path_prefix=current_path,
                source=source,
                provenance=provenance,
            )
        else:
            operation: Literal["set", "replace"] = "replace" if key in base else "set"
            is_secret = _SECRET_KEY_PATTERN.search(key) is not None
            provenance.append(
                ProvenanceEntry(
                    path=current_path,
                    source=source,
                    operation="redact" if is_secret else operation,
                    value_repr="<redacted>" if is_secret else _safe_value_repr(value),
                )
            )
            base[key] = value


def _safe_value_repr(value: Any) -> Any:
    """Return a JSON-safe representation, or None if not serializable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value_repr(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe_value_repr(v) for k, v in value.items()}
    return str(value)


def _merge_nested(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay into base, preserving sibling keys in nested dicts."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_nested(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Step 6: Check resolution
# ---------------------------------------------------------------------------


def _resolve_check(
    check_spec: Any,
    domain_name: str,
    merged_config: dict[str, Any],
    capability_policy: CapabilityPolicy,
    no_cache: bool,
) -> tuple[ResolvedCheck, list[str]]:
    """Resolve a single CheckSpec into a ResolvedCheck."""
    warnings: list[str] = []

    check_config = dict(check_spec.default_config) if check_spec.default_config else {}
    domain_check_overrides = merged_config.get(check_spec.check_id, {})
    if isinstance(domain_check_overrides, dict):
        check_config = _merge_nested(check_config, domain_check_overrides)

    config_hash = _compute_config_hash(check_config)

    skip_code: str | None = None
    skip_reason: str | None = None
    resolved_tool_version: str | None = None

    if check_config.get("enabled") is False:
        skip_code = "config-disabled"
        skip_reason = "disabled by configuration"
        warnings.append(f"check {check_spec.check_id!r} skipped: {skip_reason}")

    cap_result = _apply_capability_policy(check_spec.capabilities, capability_policy)
    if skip_code is None and cap_result is not None:
        skip_code, skip_reason = cap_result
        warnings.append(f"check {check_spec.check_id!r} skipped: {skip_reason}")

    if skip_code is None and check_spec.adapter.kind == "tool":
        for dep in getattr(check_spec.adapter, "dependencies", ()):
            dep_ok, dep_msg = _probe_dependency(dep)
            if not dep_ok:
                skip_code = "dependency-missing"
                hint = getattr(dep, "install_hint", None) or ""
                skip_reason = dep_msg or "dependency check failed"
                if hint:
                    skip_reason = f"{skip_reason}. Install: {hint}"
                warnings.append(f"check {check_spec.check_id!r} skipped: {skip_reason}")
                break

        if skip_code is None:
            version_probe = getattr(check_spec.adapter, "version_probe", None)
            if version_probe is not None:
                resolved_tool_version = _probe_tool_version(version_probe)

    cacheable = _determine_cacheability(check_spec, check_config, no_cache, resolved_tool_version)

    return (
        ResolvedCheck(
            domain=domain_name,
            check_id=check_spec.check_id,
            check_version=check_spec.version,
            spec=check_spec,
            effective_config=check_config,
            config_hash=config_hash,
            capabilities=check_spec.capabilities,
            critical=check_spec.critical,
            cacheable=cacheable,
            origin=check_spec.origin,
            resolved_tool_version=resolved_tool_version,
            skip_code=skip_code,
            skip_reason=skip_reason,
        ),
        warnings,
    )


def _compute_config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


def _apply_capability_policy(
    check_capabilities: frozenset[str],
    policy: CapabilityPolicy,
) -> tuple[str, str] | None:
    denied = check_capabilities & policy.denied
    if denied:
        return (
            "capability-denied",
            f"requires denied capabilities: {sorted(denied)}",
        )
    not_allowed = check_capabilities - policy.allowed - policy.denied
    if not_allowed:
        return (
            "capability-denied",
            f"requires capabilities not in allowlist: {sorted(not_allowed)}",
        )
    return None


def _determine_cacheability(
    check_spec: Any,
    effective_config: dict[str, Any],
    no_cache: bool,
    resolved_tool_version: str | None = None,
) -> bool:
    if no_cache:
        return False

    cache = check_spec.cache
    if not cache.enabled:
        return False

    if not cache.allow_secret_config and _detect_secret_config_keys(effective_config):
        return False

    if (
        cache.require_known_version
        and check_spec.adapter.kind == "tool"
        and resolved_tool_version is None
    ):
        return False

    return cache.allow_live or "live" not in check_spec.capabilities


def _detect_secret_config_keys(config: dict[str, Any]) -> bool:
    for key, value in config.items():
        if _SECRET_KEY_PATTERN.search(key):
            return True
        if isinstance(value, dict) and _detect_secret_config_keys(value):
            return True
    return False


# ---------------------------------------------------------------------------
# Dependency and version probing
# ---------------------------------------------------------------------------


def _probe_dependency(dep: Any) -> tuple[bool, str | None]:
    """Run a dependency probe command. Returns (ok, error_message)."""
    try:
        result = subprocess.run(
            dep.command,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == dep.expected_exit:
            return True, None
        return False, (
            f"dependency probe {dep.command!r} returned exit code "
            f"{result.returncode} (expected {dep.expected_exit})"
        )
    except FileNotFoundError:
        return False, f"command not found: {dep.command[0]!r}"
    except subprocess.TimeoutExpired:
        return False, f"dependency probe {dep.command!r} timed out"
    except Exception as exc:
        return False, f"dependency probe failed: {exc}"


def _probe_tool_version(version_probe: Any) -> str | None:
    """Run a version probe and parse the version string."""
    try:
        result = subprocess.run(
            version_probe.command,
            capture_output=True,
            text=True,
            timeout=10,
        )
        match = re.search(version_probe.parse_pattern, result.stdout)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
        match = re.search(version_probe.parse_pattern, result.stderr)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    except Exception:
        _log.debug("version probe failed for %r", version_probe.command, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_report_dir(report_dir: Path | None) -> None:
    if report_dir is None:
        return
    if report_dir.exists() and not report_dir.is_dir():
        raise PlanError(
            "REPORT_DIR_INVALID",
            f"report_dir exists but is not a directory: {report_dir}",
        )
