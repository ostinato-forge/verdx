"""Pre-execution plan contracts.

ResolvedGatePlan is the output of resolve_gate_plan() — all ambiguity
resolved before any validator executes: profiles expanded, config merged,
capabilities checked, dependencies probed, skips determined, secrets
redacted, provenance recorded.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from verdx.contracts.check import CheckOrigin, CheckSpec, JsonValue, ReportFormat

# ---------------------------------------------------------------------------
# Capability policy
# ---------------------------------------------------------------------------

_DEFAULT_ALLOWED: frozenset[str] = frozenset({"process", "filesystem.write-temp", "live"})
_DEFAULT_DENIED: frozenset[str] = frozenset(
    {
        "filesystem.outside-target",
        "filesystem.write-target",
        "network",
        "secrets",
        "destructive",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Declares which capabilities are permitted for this run."""

    allowed: frozenset[str] = field(default_factory=lambda: _DEFAULT_ALLOWED)
    denied: frozenset[str] = field(default_factory=lambda: _DEFAULT_DENIED)

    @classmethod
    def static_only(cls) -> CapabilityPolicy:
        return cls(
            allowed=frozenset({"process", "filesystem.write-temp"}),
            denied=frozenset(
                {
                    "filesystem.outside-target",
                    "filesystem.write-target",
                    "network",
                    "live",
                    "secrets",
                    "destructive",
                }
            ),
        )


# ---------------------------------------------------------------------------
# Provenance tracking
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """Records where each config value came from.

    Enables ``--plan-only`` output to show the full resolution chain.
    """

    path: tuple[str, ...]
    source: str
    operation: Literal["set", "replace", "deep-merge", "include", "exclude", "pin", "redact"]
    value_repr: JsonValue | None = None
    value_hash: str | None = None


# ---------------------------------------------------------------------------
# Resolved check
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedCheck:
    """A single check after plan resolution.

    All config is merged, capabilities are evaluated, dependencies are
    probed, cacheability is determined.  If ``skip_code`` is set, the
    check will not execute.
    """

    domain: str
    check_id: str
    check_version: str
    spec: CheckSpec
    effective_config: Mapping[str, Any]
    config_hash: str
    capabilities: frozenset[str]
    critical: bool
    cacheable: bool
    origin: CheckOrigin | None = None
    resolved_tool_version: str | None = None
    skip_code: str | None = None
    skip_reason: str | None = None


# ---------------------------------------------------------------------------
# Resolved gate plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedGatePlan:
    """Complete pre-execution plan.  Passed to the executor as-is."""

    schema_version: Literal["verdx.resolved-plan.v1"] = "verdx.resolved-plan.v1"
    run_id: str = ""
    target_key: str = ""
    target: Path = field(default_factory=lambda: Path("."))
    target_content_hash: str = ""
    timestamp: str = ""
    domains: tuple[str, ...] = ()
    checks: tuple[ResolvedCheck, ...] = ()
    domain_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    domain_configs: Mapping[str, Any] = field(default_factory=dict)
    capability_policy: CapabilityPolicy = field(default_factory=CapabilityPolicy)
    state_dir: Path = field(default_factory=lambda: Path(".verdx"))
    report_dir: Path | None = None
    output_formats: tuple[ReportFormat, ...] = ("markdown",)
    profiles: tuple[str, ...] = ()
    lock_digest: str | None = None
    threshold: int | None = None
    workers: int | str = "auto"
    no_cache: bool = False
    static_only: bool = False
    plan_only: bool = False
    provenance: tuple[ProvenanceEntry, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.target_key:
            raise ValueError("target_key must be non-empty")
        if not self.target_content_hash:
            raise ValueError("target_content_hash must be non-empty")
        if not self.timestamp:
            raise ValueError("timestamp must be non-empty")
        if not self.domains:
            raise ValueError("domains must be non-empty")


class PlanError(Exception):
    """Raised when plan resolution fails (exit 3)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}]: {message}")
