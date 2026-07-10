"""Tests for CheckSpec tagged union and adapter contracts."""

from __future__ import annotations

import pytest

from verdx.contracts.check import (
    ALL_CAPABILITIES,
    BatchMode,
    CachePolicy,
    CheckOrigin,
    CheckSpec,
    DependencyProbe,
    ExitCodePolicy,
    FileInputSpec,
    JsonPathParser,
    PythonAdapter,
    RegexParser,
    SarifParser,
    ToolAdapter,
    VersionProbe,
)
from verdx.contracts.dimensions import Confidence, DimLevel, Severity

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _python_adapter(**overrides):
    defaults = {
        "import_path": "verdx.engine.checks.yaml_lint:validate_yaml",
    }
    return PythonAdapter(**(defaults | overrides))


def _tool_adapter(**overrides):
    defaults = {
        "command": ("md-lint", "--parseable"),
    }
    return ToolAdapter(**(defaults | overrides))


def _check_spec(**overrides):
    defaults = {
        "check_id": "training.yaml-lint",
        "domain": "training",
        "title": "YAML Lint",
        "category": "correctness",
        "version": "1.0.0",
        "adapter": _python_adapter(),
        "dimensions": {"correctness": DimLevel.HIGH},
    }
    return CheckSpec(**(defaults | overrides))


# ===========================================================================
# PythonAdapter
# ===========================================================================


class TestPythonAdapter:
    def test_valid_construction(self):
        a = _python_adapter()
        assert a.kind == "python"
        assert a.import_path == "verdx.engine.checks.yaml_lint:validate_yaml"
        assert a.pool_safe is True

    def test_empty_import_path_rejected(self):
        with pytest.raises(ValueError, match="import_path must be non-empty"):
            PythonAdapter(import_path="")

    def test_missing_colon_rejected(self):
        with pytest.raises(ValueError, match="must be 'module:qualname'"):
            PythonAdapter(import_path="verdx.engine.checks.yaml_lint")

    def test_frozen(self):
        a = _python_adapter()
        with pytest.raises(AttributeError):
            a.import_path = "other:func"  # type: ignore[misc]


# ===========================================================================
# Tool adapter components
# ===========================================================================


class TestDependencyProbe:
    def test_valid(self):
        probe = DependencyProbe(
            command=("md-lint", "--version"),
            install_hint="pip install md-lint",
        )
        assert probe.expected_exit == 0

    def test_empty_command_rejected(self):
        with pytest.raises(ValueError, match="command must be non-empty"):
            DependencyProbe(command=())


class TestVersionProbe:
    def test_valid(self):
        probe = VersionProbe(
            command=("md-lint", "--version"),
            parse_pattern=r"md-lint\s+(\S+)",
        )
        assert probe.command == ("md-lint", "--version")

    def test_empty_command_rejected(self):
        with pytest.raises(ValueError, match="command must be non-empty"):
            VersionProbe(command=(), parse_pattern=r"\d+")

    def test_empty_pattern_rejected(self):
        with pytest.raises(ValueError, match="parse_pattern must be non-empty"):
            VersionProbe(command=("x",), parse_pattern="")


class TestExitCodePolicy:
    def test_defaults(self):
        p = ExitCodePolicy()
        assert p.success == (0,)
        assert p.findings == ()
        assert p.unknown_is_failure is True


class TestBatchMode:
    def test_defaults(self):
        b = BatchMode()
        assert b.strategy == "none"
        assert b.placeholder == "{files}"
        assert b.max_command_bytes == 120_000


class TestFileInputSpec:
    def test_valid(self):
        f = FileInputSpec(globs=("*.yml", "*.yaml"))
        assert f.root == "target"

    def test_empty_globs_rejected(self):
        with pytest.raises(ValueError, match="globs must be non-empty"):
            FileInputSpec(globs=())


# ===========================================================================
# Parsers
# ===========================================================================


class TestJsonPathParser:
    def test_valid(self):
        p = JsonPathParser(
            observations_path="$.results[*]",
            rule_id_path="$.rule",
            message_path="$.message",
        )
        assert p.kind == "jsonpath"

    def test_empty_observations_path_rejected(self):
        with pytest.raises(ValueError, match="observations_path must be non-empty"):
            JsonPathParser(
                observations_path="",
                rule_id_path="$.rule",
                message_path="$.message",
            )

    def test_empty_rule_id_path_rejected(self):
        with pytest.raises(ValueError, match="rule_id_path must be non-empty"):
            JsonPathParser(
                observations_path="$.results",
                rule_id_path="",
                message_path="$.message",
            )


class TestSarifParser:
    def test_defaults(self):
        p = SarifParser()
        assert p.kind == "sarif"
        assert p.severity_map == {}


class TestRegexParser:
    def test_valid(self):
        p = RegexParser(pattern=r"^(?P<file>.+):(?P<line>\d+):(?P<msg>.+)$")
        assert p.kind == "regex"

    def test_empty_pattern_rejected(self):
        with pytest.raises(ValueError, match="pattern must be non-empty"):
            RegexParser(pattern="")


# ===========================================================================
# ToolAdapter
# ===========================================================================


class TestToolAdapter:
    def test_valid(self):
        a = _tool_adapter()
        assert a.kind == "tool"
        assert a.command == ("md-lint", "--parseable")

    def test_empty_command_rejected(self):
        with pytest.raises(ValueError, match="command must be non-empty"):
            ToolAdapter(command=())

    def test_frozen(self):
        a = _tool_adapter()
        with pytest.raises(AttributeError):
            a.command = ("other",)  # type: ignore[misc]


# ===========================================================================
# CheckSpec
# ===========================================================================


class TestCheckSpec:
    def test_valid_with_python_adapter(self):
        spec = _check_spec()
        assert spec.check_id == "training.yaml-lint"
        assert spec.adapter.kind == "python"
        assert spec.severity == Severity.MEDIUM
        assert spec.confidence == Confidence.CONFIRMED

    def test_valid_with_tool_adapter(self):
        spec = _check_spec(adapter=_tool_adapter())
        assert spec.adapter.kind == "tool"

    def test_empty_check_id_rejected(self):
        with pytest.raises(ValueError, match="check_id must be non-empty"):
            _check_spec(check_id="")

    def test_empty_domain_rejected(self):
        with pytest.raises(ValueError, match="domain must be non-empty"):
            _check_spec(domain="")

    def test_empty_title_rejected(self):
        with pytest.raises(ValueError, match="title must be non-empty"):
            _check_spec(title="")

    def test_empty_category_rejected(self):
        with pytest.raises(ValueError, match="category must be non-empty"):
            _check_spec(category="")

    def test_empty_version_rejected(self):
        with pytest.raises(ValueError, match="version must be non-empty"):
            _check_spec(version="")

    def test_empty_dimensions_rejected(self):
        with pytest.raises(ValueError, match="dimensions must contain at least one"):
            _check_spec(dimensions={})

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValueError, match="unknown capabilities"):
            _check_spec(capabilities=frozenset({"teleport"}))

    def test_valid_capabilities_accepted(self):
        spec = _check_spec(capabilities=frozenset({"process", "network"}))
        assert "process" in spec.capabilities
        assert "network" in spec.capabilities

    def test_all_capabilities_frozenset(self):
        assert isinstance(ALL_CAPABILITIES, frozenset)
        assert "process" in ALL_CAPABILITIES
        assert "destructive" in ALL_CAPABILITIES


# ===========================================================================
# CachePolicy / CheckOrigin
# ===========================================================================


class TestCachePolicy:
    def test_defaults(self):
        p = CachePolicy()
        assert p.enabled is True
        assert p.allow_live is False
        assert p.require_known_version is True


class TestCheckOrigin:
    def test_builtin(self):
        o = CheckOrigin(kind="builtin", package="verdx")
        assert o.kind == "builtin"
        assert o.digest is None
