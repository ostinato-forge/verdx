"""Tests for DomainCatalog and CheckCatalogEntry contracts."""

from __future__ import annotations

import json

import pytest

from verdx.contracts.catalog import (
    CATALOG_SCHEMA_VERSION,
    CheckCatalogEntry,
    DomainCatalog,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _catalog_entry(**overrides):
    defaults = {
        "check_id": "yaml-syntax",
        "title": "YAML Syntax Check",
        "version": "1.0.0",
        "adapter_kind": "python",
        "capabilities": ("process",),
    }
    return CheckCatalogEntry(**(defaults | overrides))


def _domain_catalog(**overrides):
    defaults = {
        "domain": "training",
        "markers": ("outline.yml",),
        "checks": (_catalog_entry(),),
    }
    return DomainCatalog(**(defaults | overrides))


# ===========================================================================
# CheckCatalogEntry
# ===========================================================================


class TestCheckCatalogEntry:
    def test_valid_construction(self):
        entry = _catalog_entry()
        assert entry.check_id == "yaml-syntax"
        assert entry.adapter_kind == "python"
        assert entry.capabilities == ("process",)

    def test_defaults(self):
        entry = _catalog_entry()
        assert entry.default_enabled is True
        assert entry.critical is False
        assert entry.dimensions == ()

    def test_empty_check_id_rejected(self):
        with pytest.raises(ValueError, match="check_id"):
            _catalog_entry(check_id="")

    def test_empty_title_rejected(self):
        with pytest.raises(ValueError, match="title"):
            _catalog_entry(title="")

    def test_empty_version_rejected(self):
        with pytest.raises(ValueError, match="version"):
            _catalog_entry(version="")

    def test_frozen(self):
        entry = _catalog_entry()
        with pytest.raises(AttributeError):
            entry.check_id = "new"  # type: ignore[misc]

    def test_roundtrip(self):
        original = _catalog_entry(
            critical=True,
            dimensions=("correctness", "security"),
        )
        rebuilt = CheckCatalogEntry.from_dict(original.to_dict())
        assert rebuilt.check_id == original.check_id
        assert rebuilt.critical is True
        assert rebuilt.dimensions == ("correctness", "security")

    def test_json_serializable(self):
        entry = _catalog_entry()
        json.dumps(entry.to_dict())

    def test_tool_adapter_kind(self):
        caps = ("process", "filesystem.write-temp")
        entry = _catalog_entry(adapter_kind="tool", capabilities=caps)
        assert entry.adapter_kind == "tool"
        assert "process" in entry.capabilities


# ===========================================================================
# DomainCatalog
# ===========================================================================


class TestDomainCatalog:
    def test_valid_construction(self):
        catalog = _domain_catalog()
        assert catalog.domain == "training"
        assert len(catalog.checks) == 1

    def test_schema_version(self):
        catalog = _domain_catalog()
        assert catalog.schema_version == CATALOG_SCHEMA_VERSION

    def test_empty_domain_rejected(self):
        with pytest.raises(ValueError, match="domain"):
            _domain_catalog(domain="")

    def test_defaults(self):
        catalog = DomainCatalog(domain="test")
        assert catalog.package is None
        assert catalog.version is None
        assert catalog.markers == ()
        assert catalog.supports_subject_discovery is False
        assert catalog.checks == ()

    def test_frozen(self):
        catalog = _domain_catalog()
        with pytest.raises(AttributeError):
            catalog.domain = "new"  # type: ignore[misc]

    def test_roundtrip(self):
        original = _domain_catalog(
            package="example-gate",
            version="2.0.0",
            supports_subject_discovery=True,
        )
        rebuilt = DomainCatalog.from_dict(original.to_dict())
        assert rebuilt.domain == "training"
        assert rebuilt.package == "example-gate"
        assert rebuilt.version == "2.0.0"
        assert rebuilt.supports_subject_discovery is True
        assert len(rebuilt.checks) == 1

    def test_roundtrip_without_optional(self):
        original = DomainCatalog(domain="editing")
        d = original.to_dict()
        assert "package" not in d
        assert "version" not in d
        rebuilt = DomainCatalog.from_dict(d)
        assert rebuilt.package is None
        assert rebuilt.version is None

    def test_json_serializable(self):
        catalog = _domain_catalog()
        json.dumps(catalog.to_dict())
