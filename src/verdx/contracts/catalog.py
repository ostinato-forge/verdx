"""Domain catalog contracts for discovery and introspection.

``DomainCatalog`` is a lightweight projection of a domain's check suite,
designed for ``verdx domains list/show`` and skill-level discovery.
It is NOT an execution contract — CheckSpec carries the full adapter
and config details needed for execution.

Round 15 schema lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CATALOG_SCHEMA_VERSION: Literal["verdx.domain-catalog.v1"] = "verdx.domain-catalog.v1"


@dataclass(frozen=True, slots=True)
class CheckCatalogEntry:
    """Lightweight check metadata for catalog display."""

    check_id: str
    title: str
    version: str
    adapter_kind: Literal["python", "tool"]
    capabilities: tuple[str, ...]
    default_enabled: bool = True
    critical: bool = False
    dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.check_id:
            raise ValueError("check_id must be non-empty")
        if not self.title:
            raise ValueError("title must be non-empty")
        if not self.version:
            raise ValueError("version must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "version": self.version,
            "adapter_kind": self.adapter_kind,
            "capabilities": list(self.capabilities),
            "default_enabled": self.default_enabled,
            "critical": self.critical,
            "dimensions": list(self.dimensions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckCatalogEntry:
        return cls(
            check_id=data["check_id"],
            title=data["title"],
            version=data["version"],
            adapter_kind=data["adapter_kind"],
            capabilities=tuple(data["capabilities"]),
            default_enabled=data.get("default_enabled", True),
            critical=data.get("critical", False),
            dimensions=tuple(data.get("dimensions", ())),
        )


@dataclass(frozen=True, slots=True)
class DomainCatalog:
    """Read-only projection of a domain's capabilities for discovery."""

    domain: str
    schema_version: Literal["verdx.domain-catalog.v1"] = CATALOG_SCHEMA_VERSION
    package: str | None = None
    version: str | None = None
    markers: tuple[str, ...] = ()
    supports_subject_discovery: bool = False
    checks: tuple[CheckCatalogEntry, ...] = ()

    def __post_init__(self) -> None:
        if not self.domain:
            raise ValueError("domain must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "domain": self.domain,
            "markers": list(self.markers),
            "supports_subject_discovery": self.supports_subject_discovery,
            "checks": [c.to_dict() for c in self.checks],
        }
        if self.package is not None:
            result["package"] = self.package
        if self.version is not None:
            result["version"] = self.version
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainCatalog:
        return cls(
            domain=data["domain"],
            schema_version=data.get("schema_version", CATALOG_SCHEMA_VERSION),
            package=data.get("package"),
            version=data.get("version"),
            markers=tuple(data.get("markers", ())),
            supports_subject_discovery=data.get("supports_subject_discovery", False),
            checks=tuple(CheckCatalogEntry.from_dict(c) for c in data.get("checks", ())),
        )
