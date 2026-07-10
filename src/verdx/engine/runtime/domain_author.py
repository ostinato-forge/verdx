"""Domain authoring checks for verdx domain packages."""

from __future__ import annotations

import importlib
import sys
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from verdx.contracts.catalog import CheckCatalogEntry, DomainCatalog
from verdx.contracts.check import CheckSpec
from verdx.contracts.config import DomainConfig
from verdx.contracts.domain import DomainSpec

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

AUTHOR_SCHEMA_VERSION = "verdx.domain-author.v1"
AuthorStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class AuthorCheck:
    """One domain-authoring check result."""

    name: str
    status: AuthorStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DomainAuthorReport:
    """Authoring report for one local domain package checkout."""

    path: str
    package: str | None
    entry_points: dict[str, str]
    checks: tuple[AuthorCheck, ...]
    schema_version: str = AUTHOR_SCHEMA_VERSION

    @property
    def status(self) -> AuthorStatus:
        statuses = {check.status for check in self.checks}
        if "fail" in statuses:
            return "fail"
        if "warn" in statuses:
            return "warn"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "path": self.path,
            "package": self.package,
            "entry_points": self.entry_points,
            "checks": [check.to_dict() for check in self.checks],
        }


def author_check(path: Path) -> DomainAuthorReport:
    """Inspect a local domain package checkout for authoring readiness."""

    root = path.resolve()
    checks: list[AuthorCheck] = []
    pyproject_path = root / "pyproject.toml"

    if not pyproject_path.exists():
        return DomainAuthorReport(
            path=str(root),
            package=None,
            entry_points={},
            checks=(
                AuthorCheck(
                    "pyproject",
                    "fail",
                    "pyproject.toml not found; run this from a Python package root.",
                ),
            ),
        )

    checks.append(AuthorCheck("pyproject", "pass", "pyproject.toml found."))

    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        checks.append(AuthorCheck("pyproject.parse", "fail", f"invalid TOML: {exc}"))
        return DomainAuthorReport(str(root), None, {}, tuple(checks))

    project = data.get("project", {})
    package = project.get("name") if isinstance(project, dict) else None
    if isinstance(package, str) and package:
        checks.append(AuthorCheck("project.name", "pass", f"package name is {package}."))
    else:
        checks.append(AuthorCheck("project.name", "warn", "project.name is missing."))
        package = None

    entry_points = _domain_entry_points(data)
    if not entry_points:
        checks.append(
            AuthorCheck(
                "entry-points",
                "fail",
                'missing [project.entry-points."verdx.domains"] entries.',
            )
        )
        return DomainAuthorReport(str(root), package, {}, tuple(checks))

    checks.append(
        AuthorCheck(
            "entry-points",
            "pass",
            f"found {len(entry_points)} verdx.domains entry point(s).",
        )
    )

    with _package_import_path(root):
        for entry_name, import_path in entry_points.items():
            checks.extend(_check_entry_point(entry_name, import_path))

    return DomainAuthorReport(str(root), package, entry_points, tuple(checks))


def build_domain_catalog(spec: DomainSpec) -> DomainCatalog:
    """Build the lightweight catalog projection for a loaded domain."""

    checks = spec.load_checks()
    entries = tuple(_catalog_entry(check) for check in checks)
    return DomainCatalog(
        domain=spec.name,
        markers=spec.markers,
        supports_subject_discovery=True,
        checks=entries,
    )


def _domain_entry_points(data: dict[str, Any]) -> dict[str, str]:
    project = data.get("project", {})
    if not isinstance(project, dict):
        return {}
    entry_points = project.get("entry-points", {})
    if not isinstance(entry_points, dict):
        return {}
    group = entry_points.get("verdx.domains", {})
    if not isinstance(group, dict):
        return {}
    return {str(name): str(value) for name, value in group.items() if name and value}


def _check_entry_point(entry_name: str, import_path: str) -> list[AuthorCheck]:
    checks: list[AuthorCheck] = []
    label = f"entry:{entry_name}"

    if ":" not in import_path:
        return [
            AuthorCheck(
                label,
                "fail",
                f"{import_path!r} must use 'module:callable' syntax.",
            )
        ]

    try:
        factory = _load_object(import_path)
    except Exception as exc:
        return [
            AuthorCheck(
                label,
                "fail",
                f"could not import {import_path!r}: {type(exc).__name__}: {exc}",
            )
        ]

    if not callable(factory):
        return [AuthorCheck(label, "fail", f"{import_path!r} is not callable.")]

    try:
        spec = factory()
    except Exception as exc:
        return [
            AuthorCheck(
                label,
                "fail",
                f"factory raised {type(exc).__name__}: {exc}",
            )
        ]

    if not isinstance(spec, DomainSpec):
        return [
            AuthorCheck(
                label,
                "fail",
                f"factory returned {type(spec).__name__}, expected DomainSpec.",
            )
        ]

    if spec.name != entry_name:
        checks.append(
            AuthorCheck(
                label,
                "fail",
                f"entry point name {entry_name!r} must match DomainSpec.name {spec.name!r}.",
            )
        )
    else:
        checks.append(AuthorCheck(label, "pass", f"loaded DomainSpec {spec.name!r}."))

    if spec.markers:
        checks.append(
            AuthorCheck(
                f"{label}.markers",
                "pass",
                f"declares marker(s): {', '.join(spec.markers)}.",
            )
        )
    else:
        checks.append(
            AuthorCheck(
                f"{label}.markers",
                "warn",
                "no markers declared; auto-detection will not find this domain.",
            )
        )

    checks.extend(_check_default_config(label, spec))
    checks.extend(_check_checks(label, spec))
    checks.extend(_check_profiles(label, spec))
    return checks


def _check_default_config(label: str, spec: DomainSpec) -> list[AuthorCheck]:
    try:
        config = spec.load_default_config()
    except Exception as exc:
        return [
            AuthorCheck(
                f"{label}.config",
                "fail",
                f"load_default_config raised {type(exc).__name__}: {exc}",
            )
        ]
    if not isinstance(config, DomainConfig):
        return [
            AuthorCheck(
                f"{label}.config",
                "fail",
                f"load_default_config returned {type(config).__name__}, expected DomainConfig.",
            )
        ]
    return [AuthorCheck(f"{label}.config", "pass", "load_default_config returned DomainConfig.")]


def _check_checks(label: str, spec: DomainSpec) -> list[AuthorCheck]:
    try:
        checks = spec.load_checks()
    except Exception as exc:
        return [
            AuthorCheck(
                f"{label}.checks",
                "fail",
                f"load_checks raised {type(exc).__name__}: {exc}",
            )
        ]
    if not isinstance(checks, tuple):
        return [
            AuthorCheck(
                f"{label}.checks",
                "fail",
                f"load_checks returned {type(checks).__name__}, expected tuple[CheckSpec, ...].",
            )
        ]
    bad = [type(check).__name__ for check in checks if not isinstance(check, CheckSpec)]
    if bad:
        return [
            AuthorCheck(
                f"{label}.checks",
                "fail",
                f"load_checks returned non-CheckSpec item(s): {', '.join(bad)}.",
            )
        ]
    if not checks:
        return [AuthorCheck(f"{label}.checks", "warn", "load_checks returned no checks.")]
    return [AuthorCheck(f"{label}.checks", "pass", f"load_checks returned {len(checks)} check(s).")]


def _check_profiles(label: str, spec: DomainSpec) -> list[AuthorCheck]:
    try:
        profiles = spec.load_profiles()
    except Exception as exc:
        return [
            AuthorCheck(
                f"{label}.profiles",
                "fail",
                f"load_profiles raised {type(exc).__name__}: {exc}",
            )
        ]
    if not isinstance(profiles, tuple) or any(not isinstance(p, dict) for p in profiles):
        return [
            AuthorCheck(
                f"{label}.profiles",
                "fail",
                "load_profiles must return tuple[dict, ...].",
            )
        ]
    names = {p.get("name") for p in profiles}
    if "quick" in names:
        return [AuthorCheck(f"{label}.profiles", "pass", "quick profile is available.")]
    return [
        AuthorCheck(
            f"{label}.profiles",
            "warn",
            "no quick profile found; agent quick-check workflows may not apply.",
        )
    ]


def _catalog_entry(check: CheckSpec) -> CheckCatalogEntry:
    return CheckCatalogEntry(
        check_id=check.check_id,
        title=check.title,
        version=check.version,
        adapter_kind=check.adapter.kind,
        capabilities=tuple(sorted(check.capabilities)),
        default_enabled=True,
        critical=check.critical,
        dimensions=tuple(sorted(check.dimensions)),
    )


def _load_object(import_path: str) -> Any:
    module_name, qualname = import_path.split(":", 1)
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


@contextmanager
def _package_import_path(root: Path) -> Iterator[None]:
    original = list(sys.path)
    src = root / "src"
    for candidate in (src, root):
        if candidate.exists():
            sys.path.insert(0, str(candidate))
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path[:] = original
        importlib.invalidate_caches()
