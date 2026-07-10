"""CheckSpec tagged union and adapter contracts.

Defines the unified check abstraction: every validator (Python function,
external tool, or marketplace check) is a CheckSpec with a PythonAdapter
or ToolAdapter.  The engine doesn't care about adapter type after plan
resolution.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from verdx.contracts.dimensions import Confidence, DimLevel, Severity

if TYPE_CHECKING:
    from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

Capability = Literal[
    "process",
    "filesystem.outside-target",
    "filesystem.write-temp",
    "filesystem.write-target",
    "network",
    "live",
    "secrets",
    "destructive",
]

ReportFormat = Literal["markdown", "json", "agent", "junit", "sarif"]

ALL_CAPABILITIES: frozenset[str] = frozenset(
    {
        "process",
        "filesystem.outside-target",
        "filesystem.write-temp",
        "filesystem.write-target",
        "network",
        "live",
        "secrets",
        "destructive",
    }
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """Controls whether and how a check's results may be cached."""

    enabled: bool = True
    allow_live: bool = False
    allow_secret_config: bool = False
    require_known_version: bool = True


@dataclass(frozen=True, slots=True)
class CheckOrigin:
    """Provenance of a check: builtin, catalog, or local."""

    kind: Literal["builtin", "catalog", "local"]
    package: str | None = None
    version: str | None = None
    digest: str | None = None
    lock_ref: str | None = None


# ---------------------------------------------------------------------------
# Python adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PythonAdapter:
    """In-process Python check via import-path resolution.

    The worker imports the module and resolves the qualname to get the
    decorated validator callable.  Only JSON-safe dicts cross the
    process boundary.
    """

    kind: Literal["python"] = "python"
    import_path: str = ""
    pool_safe: bool = True

    def __post_init__(self) -> None:
        if not self.import_path:
            raise ValueError("import_path must be non-empty")
        if ":" not in self.import_path:
            raise ValueError(f"import_path must be 'module:qualname', got {self.import_path!r}")


# ---------------------------------------------------------------------------
# Tool adapter components
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DependencyProbe:
    """Pre-flight check that a tool's dependency is available."""

    command: tuple[str, ...]
    expected_exit: int = 0
    install_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must be non-empty")


@dataclass(frozen=True, slots=True)
class VersionProbe:
    """Discovers the installed version of an external tool."""

    command: tuple[str, ...]
    parse_pattern: str

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must be non-empty")
        if not self.parse_pattern:
            raise ValueError("parse_pattern must be non-empty")


@dataclass(frozen=True, slots=True)
class ExitCodePolicy:
    """Maps tool exit codes to semantic outcomes."""

    success: tuple[int, ...] = (0,)
    findings: tuple[int, ...] = ()
    failure: tuple[int, ...] = ()
    unknown_is_failure: bool = True


@dataclass(frozen=True, slots=True)
class BatchMode:
    """Controls how multiple files are passed to a tool."""

    strategy: Literal["none", "argv"] = "none"
    placeholder: str = "{files}"
    max_files_per_invocation: int | None = None
    max_command_bytes: int = 120_000


@dataclass(frozen=True, slots=True)
class FileInputSpec:
    """Describes which files a tool operates on."""

    globs: tuple[str, ...]
    root: Literal["target", "scratch"] = "target"
    excludes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.globs:
            raise ValueError("globs must be non-empty")


# ---------------------------------------------------------------------------
# Tool output parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JsonPathParser:
    """Parses tool output as JSON using path expressions."""

    kind: Literal["jsonpath"] = "jsonpath"
    observations_path: str = ""
    rule_id_path: str = ""
    message_path: str = ""
    location_path: str | None = None
    severity_path: str | None = None
    severity_map: Mapping[str, Severity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.observations_path:
            raise ValueError("observations_path must be non-empty")
        if not self.rule_id_path:
            raise ValueError("rule_id_path must be non-empty")
        if not self.message_path:
            raise ValueError("message_path must be non-empty")


@dataclass(frozen=True, slots=True)
class SarifParser:
    """Parses SARIF 2.1.0 tool output."""

    kind: Literal["sarif"] = "sarif"
    severity_map: Mapping[str, Severity] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegexParser:
    """Parses tool stdout line-by-line with a regex."""

    kind: Literal["regex"] = "regex"
    pattern: str = ""
    severity_map: Mapping[str, Severity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pattern:
            raise ValueError("pattern must be non-empty")


type ParserRef = JsonPathParser | SarifParser | RegexParser


# ---------------------------------------------------------------------------
# Tool adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolAdapter:
    """Subprocess-based check execution.  Never ``shell=True``."""

    kind: Literal["tool"] = "tool"
    command: tuple[str, ...] = ()
    parser: ParserRef = field(default_factory=SarifParser)
    exit_codes: ExitCodePolicy = field(default_factory=ExitCodePolicy)
    dependencies: tuple[DependencyProbe, ...] = ()
    file_inputs: FileInputSpec | None = None
    batch: BatchMode = field(default_factory=BatchMode)
    env: Mapping[str, str] = field(default_factory=dict)
    version_probe: VersionProbe | None = None
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must be non-empty")


# ---------------------------------------------------------------------------
# CheckSpec — the unified check abstraction
# ---------------------------------------------------------------------------


def check_from_validator(
    validator: Any,
    config: dict[str, Any],
    domain: str,
    *,
    severity: Severity = Severity.MEDIUM,
    confidence: Confidence = Confidence.CONFIRMED,
    capabilities: frozenset[str] = frozenset(),
) -> CheckSpec:
    """Build a CheckSpec from a legacy ``(validator, config)`` pair.

    Domain gates that still define validators as decorated functions can
    use this helper instead of constructing ``CheckSpec`` manually.
    """
    check_id: str = getattr(validator, "validator_id", "unknown")
    version: str = getattr(validator, "validator_version", "0.0.0")
    module: str = getattr(validator, "__module__", "__unknown__")
    qualname: str = getattr(validator, "__qualname__", check_id)
    # The PythonAdapter resolves the validator by importing ``module`` and
    # looking up ``qualname``. Only a module-level function is resolvable that
    # way: a nested or method-scoped one has a dotted or ``<locals>`` qualname
    # that import cannot follow. Reject it here with a clear authoring error
    # naming the validator, instead of building an import path that only blows
    # up later when the executor tries to resolve it.
    if "<locals>" in qualname or "." in qualname:
        raise ValueError(
            f"validator {check_id!r} ({module}:{qualname}) is not importable: "
            "validators must be module-level functions, not nested functions "
            "or methods (its __qualname__ is not a bare, module-level name)"
        )
    return CheckSpec(
        check_id=check_id,
        domain=domain,
        title=check_id.replace("_", " ").replace("-", " ").title(),
        category=check_id,
        version=version,
        adapter=PythonAdapter(
            import_path=f"{module}:{qualname}",
            pool_safe="live" not in capabilities,
        ),
        dimensions={"correctness": DimLevel.MEDIUM},
        severity=severity,
        confidence=confidence,
        capabilities=capabilities,
        default_config=dict(config),
    )


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """A single quality check: Python function or external tool.

    The engine resolves ``CheckSpec`` into ``ResolvedCheck`` during
    planning.  After resolution, the adapter type is irrelevant — the
    executor dispatches based on ``adapter.kind``.
    """

    check_id: str
    domain: str
    title: str
    category: str
    version: str
    adapter: PythonAdapter | ToolAdapter
    dimensions: Mapping[str, DimLevel]
    severity: Severity = Severity.MEDIUM
    confidence: Confidence = Confidence.CONFIRMED
    capabilities: frozenset[str] = field(default_factory=frozenset)
    default_config: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    cache: CachePolicy = field(default_factory=CachePolicy)
    file_domains: tuple[str, ...] | None = None
    critical: bool = False
    origin: CheckOrigin | None = None

    def __post_init__(self) -> None:
        if not self.check_id:
            raise ValueError("check_id must be non-empty")
        if not self.domain:
            raise ValueError("domain must be non-empty")
        if not self.title:
            raise ValueError("title must be non-empty")
        if not self.category:
            raise ValueError("category must be non-empty")
        if not self.version:
            raise ValueError("version must be non-empty")
        if not self.dimensions:
            raise ValueError("dimensions must contain at least one entry")
        caps = self.capabilities
        if isinstance(self.adapter, ToolAdapter) and "process" not in caps:
            object.__setattr__(self, "capabilities", caps | {"process"})
            caps = self.capabilities
        unknown = caps - ALL_CAPABILITIES
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
