"""Tests for resolve_gate_plan() and planner helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from verdx.contracts.check import (
    CachePolicy,
    CheckOrigin,
    CheckSpec,
    DependencyProbe,
    PythonAdapter,
    ToolAdapter,
    VersionProbe,
)
from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
from verdx.contracts.plan import CapabilityPolicy, PlanError
from verdx.engine.runtime.planner import (
    compute_content_hash,
    resolve_gate_plan,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _python_adapter(**overrides) -> PythonAdapter:
    defaults = {"import_path": "mod:check_fn"}
    return PythonAdapter(**(defaults | overrides))


def _check_spec(**overrides) -> CheckSpec:
    defaults = {
        "check_id": "yaml-syntax",
        "domain": "training",
        "title": "YAML Syntax Check",
        "category": "structure",
        "version": "1.0.0",
        "adapter": _python_adapter(),
        "dimensions": {"correctness": "high"},
    }
    return CheckSpec(**(defaults | overrides))


def _domain_spec(
    name: str = "training",
    markers: tuple[str, ...] = ("outline.yml",),
    checks: tuple[CheckSpec, ...] | None = None,
    profiles: tuple[dict[str, Any], ...] = (),
) -> DomainSpec:
    if checks is None:
        checks = (_check_spec(domain=name),)

    @dataclass
    class _FakeConfig:
        version: int = 1

    return DomainSpec(
        name=name,
        markers=markers,
        load_default_config=_FakeConfig,
        load_checks=lambda: checks,
        load_profiles=lambda: profiles,
        discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
    )


# ===========================================================================
# compute_content_hash
# ===========================================================================


class TestComputeContentHash:
    def test_file_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h = compute_content_hash(f)
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 16

    def test_directory_hash(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("aaa")
        (tmp_path / "b.txt").write_text("bbb")
        h = compute_content_hash(tmp_path)
        assert h.startswith("sha256:")

    def test_empty_directory(self, tmp_path: Path) -> None:
        h = compute_content_hash(tmp_path)
        assert h.startswith("sha256:")

    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "det.txt"
        f.write_text("deterministic")
        assert compute_content_hash(f) == compute_content_hash(f)

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f1.write_text("one")
        f2 = tmp_path / "b.txt"
        f2.write_text("two")
        assert compute_content_hash(f1) != compute_content_hash(f2)

    def test_skips_dotfiles_in_directory(self, tmp_path: Path) -> None:
        (tmp_path / "visible.txt").write_text("data")
        (tmp_path / ".hidden").write_text("secret")
        h1 = compute_content_hash(tmp_path)

        (tmp_path / ".hidden").write_text("changed")
        h2 = compute_content_hash(tmp_path)
        assert h1 == h2

    def test_skips_dot_directory_contents(self, tmp_path: Path) -> None:
        (tmp_path / "visible.txt").write_text("data")
        git_dir = tmp_path / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "abc123").write_text("git object data")
        state_dir = tmp_path / ".verdx"
        state_dir.mkdir()
        (state_dir / "cache.json").write_text("{}")
        h1 = compute_content_hash(tmp_path)

        (git_dir / "abc123").write_text("changed git data")
        (state_dir / "cache.json").write_text('{"key": "val"}')
        h2 = compute_content_hash(tmp_path)
        assert h1 == h2

    def test_visible_nested_dirs_still_hashed(self, tmp_path: Path) -> None:
        sub = tmp_path / "content" / "ch01"
        sub.mkdir(parents=True)
        (sub / "guide.adoc").write_text("chapter one")
        h1 = compute_content_hash(tmp_path)

        (sub / "guide.adoc").write_text("chapter one revised")
        h2 = compute_content_hash(tmp_path)
        assert h1 != h2


# ===========================================================================
# resolve_gate_plan — happy path
# ===========================================================================


class TestResolvePlanHappyPath:
    def test_basic_resolution(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("course: test")
        spec = _domain_spec()

        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )

        assert plan.domains == ("training",)
        assert len(plan.checks) == 1
        assert plan.checks[0].check_id == "yaml-syntax"
        assert plan.checks[0].domain == "training"

    def test_schema_version(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.schema_version == "verdx.resolved-plan.v1"

    def test_run_id_auto_generated(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.run_id.startswith("run-")
        assert len(plan.run_id) == 16

    def test_run_id_explicit(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
            run_id="custom-run-001",
        )
        assert plan.run_id == "custom-run-001"

    def test_timestamp_auto_generated(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert "T" in plan.timestamp
        assert "+" in plan.timestamp or "Z" in plan.timestamp

    def test_timestamp_explicit(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
            timestamp="2026-01-01T00:00:00+00:00",
        )
        assert plan.timestamp == "2026-01-01T00:00:00+00:00"

    def test_content_hash_computed(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("data")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.target_content_hash.startswith("sha256:")

    def test_target_key_is_resolved_path(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.target_key == str(tmp_path.resolve())

    def test_domain_groups_populated(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        checks = (
            _check_spec(check_id="c1", domain="training"),
            _check_spec(check_id="c2", domain="training"),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec(checks=checks)},
        )
        assert plan.domain_groups["training"] == ("c1", "c2")

    def test_flags_propagated(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
            no_cache=True,
            static_only=True,
            plan_only=True,
            workers=4,
            output_formats=("json", "markdown"),
            threshold=90,
        )
        assert plan.no_cache is True
        assert plan.static_only is True
        assert plan.plan_only is True
        assert plan.workers == 4
        assert plan.output_formats == ("json", "markdown")
        assert plan.threshold == 90

    def test_threshold_default_none(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.threshold is None


# ===========================================================================
# Domain resolution
# ===========================================================================


class TestDomainResolution:
    def test_requested_domains_used(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
            requested_domains=("training",),
        )
        assert plan.domains == ("training",)

    def test_unknown_domain_raises(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": _domain_spec()},
                requested_domains=("nonexistent",),
            )
        assert exc_info.value.code == "UNKNOWN_DOMAIN"

    def test_auto_detect_from_markers(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.domains == ("training",)

    def test_no_domain_match_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": _domain_spec()},
            )
        assert exc_info.value.code == "NO_DOMAIN_MATCH"

    def test_multiple_domains_detected(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        (tmp_path / "style.yml").write_text("")
        spec_a = _domain_spec(
            name="editing",
            markers=("style.yml",),
            checks=(_check_spec(check_id="ed-1", domain="editing"),),
        )
        spec_b = _domain_spec(name="training")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"editing": spec_a, "training": spec_b},
        )
        assert plan.domains == ("editing", "training")
        assert len(plan.checks) == 2


# ===========================================================================
# Error handling
# ===========================================================================


class TestPlanErrors:
    def test_target_not_found(self) -> None:
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=Path("/nonexistent/path"),
                available_domains={"training": _domain_spec()},
            )
        assert exc_info.value.code == "TARGET_NOT_FOUND"

    def test_duplicate_check_id(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        (tmp_path / "style.yml").write_text("")
        spec_a = _domain_spec(
            name="editing",
            markers=("style.yml",),
            checks=(_check_spec(check_id="dupe", domain="editing"),),
        )
        spec_b = _domain_spec(
            name="training",
            checks=(_check_spec(check_id="dupe", domain="training"),),
        )
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"editing": spec_a, "training": spec_b},
            )
        assert exc_info.value.code == "DUPLICATE_CHECK_ID"

    def test_report_dir_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        not_a_dir = tmp_path / "file.txt"
        not_a_dir.write_text("content")
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": _domain_spec()},
                report_dir=not_a_dir,
            )
        assert exc_info.value.code == "REPORT_DIR_INVALID"

    def test_lock_stale_without_lock(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": _domain_spec()},
                locked=True,
            )
        assert exc_info.value.code == "LOCK_STALE"

    def test_internal_error_wrapped(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")

        def bad_checks():
            raise RuntimeError("boom")

        spec = DomainSpec(
            name="bad",
            markers=("outline.yml",),
            load_default_config=lambda: object(),
            load_checks=bad_checks,
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"bad": spec},
                requested_domains=("bad",),
            )
        assert exc_info.value.code == "PLAN_INTERNAL_ERROR"

    def test_plan_error_has_code(self, tmp_path: Path) -> None:
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=Path("/no/such/path"),
                available_domains={},
            )
        assert hasattr(exc_info.value, "code")
        assert isinstance(exc_info.value.code, str)


# ===========================================================================
# Capability policy
# ===========================================================================


class TestCapabilityPolicy:
    def test_denied_capability_produces_skip(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "network"}))
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].skip_code == "capability-denied"
        assert "network" in (plan.checks[0].skip_reason or "")

    def test_allowed_capability_not_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process"}))
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].skip_code is None

    def test_static_only_denies_live(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "live"}))
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            static_only=True,
        )
        assert plan.checks[0].skip_code == "capability-denied"

    def test_custom_policy(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "network"}))
        spec = _domain_spec(checks=(check,))
        policy = CapabilityPolicy(
            allowed=frozenset({"process", "network"}),
            denied=frozenset(),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            capability_policy=policy,
        )
        assert plan.checks[0].skip_code is None


# ===========================================================================
# Check resolution
# ===========================================================================


class TestCheckResolution:
    def test_config_hash_deterministic(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        spec = _domain_spec()
        plan1 = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        plan2 = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan1.checks[0].config_hash == plan2.checks[0].config_hash

    def test_config_hash_changes_with_override(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        spec = _domain_spec()
        plan1 = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        plan2 = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            cli_overrides={"yaml-syntax": {"strict": True}},
        )
        assert plan1.checks[0].config_hash != plan2.checks[0].config_hash

    def test_critical_from_check_spec(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(critical=True)
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].critical is True

    def test_origin_preserved(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        origin = CheckOrigin(kind="builtin", package="example-gate", version="2.0.0")
        check = _check_spec(origin=origin)
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].origin is not None
        assert plan.checks[0].origin.package == "example-gate"

    def test_check_version_preserved(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(version="3.2.1")
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].check_version == "3.2.1"

    def test_nested_default_config_preserved_on_partial_override(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(
            default_config={
                "rules": {"max_line": 100, "max_file": 50},
                "format": "strict",
            }
        )
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            cli_overrides={"yaml-syntax": {"rules": {"max_line": 120}}},
        )
        cfg = plan.checks[0].effective_config
        assert cfg["rules"]["max_line"] == 120
        assert cfg["rules"]["max_file"] == 50
        assert cfg["format"] == "strict"

    def test_profile_can_disable_check(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        profile = {"name": "quick", "config": {"yaml-syntax": {"enabled": False}}}
        spec = _domain_spec(profiles=(profile,))

        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            profiles=("quick",),
        )

        assert plan.checks[0].skip_code == "config-disabled"
        assert plan.checks[0].skip_reason == "disabled by configuration"


# ===========================================================================
# Cacheability
# ===========================================================================


class TestCacheability:
    def test_cacheable_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.checks[0].cacheable is True

    def test_not_cacheable_when_cache_disabled(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(cache=CachePolicy(enabled=False))
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].cacheable is False

    def test_not_cacheable_with_secret_config(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(default_config={"api_key": "secret123"})
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].cacheable is False

    def test_no_cache_flag_overrides(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
            no_cache=True,
        )
        assert plan.checks[0].cacheable is False

    def test_live_not_cacheable_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "live"}))
        spec = _domain_spec(checks=(check,))
        policy = CapabilityPolicy(
            allowed=frozenset({"process", "live"}),
            denied=frozenset(),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            capability_policy=policy,
        )
        assert plan.checks[0].cacheable is False


# ===========================================================================
# Dependency probing
# ===========================================================================


class TestDependencyProbing:
    def test_missing_dependency_produces_skip(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(
            adapter=ToolAdapter(
                command=("md-lint",),
                dependencies=(
                    DependencyProbe(
                        command=("md-lint", "--version"),
                        install_hint="pip install md-lint",
                    ),
                ),
            ),
            capabilities=frozenset({"process"}),
        )
        spec = _domain_spec(checks=(check,))

        with patch("verdx.engine.runtime.planner.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("not found")
            plan = resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": spec},
            )

        assert plan.checks[0].skip_code == "dependency-missing"
        assert "md-lint" in (plan.checks[0].skip_reason or "")

    def test_present_dependency_no_skip(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(
            adapter=ToolAdapter(
                command=("md-lint",),
                dependencies=(DependencyProbe(command=("md-lint", "--version")),),
            ),
            capabilities=frozenset({"process"}),
        )
        spec = _domain_spec(checks=(check,))

        @dataclass
        class _FakeResult:
            returncode: int = 0

        with patch("verdx.engine.runtime.planner.subprocess.run", return_value=_FakeResult()):
            plan = resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": spec},
            )

        assert plan.checks[0].skip_code is None

    def test_version_probed(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(
            adapter=ToolAdapter(
                command=("yamllint",),
                version_probe=VersionProbe(
                    command=("yamllint", "--version"),
                    parse_pattern=r"(\d+\.\d+\.\d+)",
                ),
            ),
            capabilities=frozenset({"process"}),
        )
        spec = _domain_spec(checks=(check,))

        @dataclass
        class _FakeResult:
            returncode: int = 0
            stdout: str = "yamllint 1.35.1"
            stderr: str = ""

        with patch("verdx.engine.runtime.planner.subprocess.run", return_value=_FakeResult()):
            plan = resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": spec},
            )

        assert plan.checks[0].resolved_tool_version == "1.35.1"

    def test_python_adapter_no_probe(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert plan.checks[0].skip_code is None
        assert plan.checks[0].resolved_tool_version is None


# ===========================================================================
# Config merging
# ===========================================================================


class TestConfigMerging:
    def test_cli_overrides_take_precedence(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(default_config={"strict": False})
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            cli_overrides={"yaml-syntax": {"strict": True}},
        )
        assert plan.checks[0].effective_config["strict"] is True

    def test_domain_defaults_not_mutated(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        shared_config = {"yaml-syntax": {"nested": {"a": 1}}}
        spec = DomainSpec(
            name="training",
            markers=("outline.yml",),
            load_default_config=lambda: shared_config,
            load_checks=lambda: (_check_spec(),),
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )
        resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            cli_overrides={"yaml-syntax": {"nested": {"b": 2}}},
        )
        assert shared_config == {"yaml-syntax": {"nested": {"a": 1}}}

    def test_provenance_tracked(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
        )
        assert len(plan.provenance) > 0
        sources = {p.source for p in plan.provenance}
        assert any("domain-default" in s for s in sources)


# ===========================================================================
# Profiles
# ===========================================================================


class TestProfiles:
    def test_named_profile_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        profiles = ({"name": "quick", "config": {"yaml-syntax": {"strict": False}}},)
        spec = _domain_spec(profiles=profiles)
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            profiles=("quick",),
        )
        assert plan.checks[0].effective_config.get("strict") is False

    def test_unknown_profile_raises(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": _domain_spec()},
                profiles=("nonexistent",),
            )
        assert exc_info.value.code == "PROFILE_NOT_FOUND"

    def test_profile_file_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        profile_file = tmp_path / "custom.yml"
        profile_file.write_text("config:\n  yaml-syntax:\n    max_errors: 10\n")
        spec = _domain_spec()
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            profile_files=(profile_file,),
        )
        assert plan.checks[0].effective_config.get("max_errors") == 10

    def test_profile_file_not_found(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        with pytest.raises(PlanError) as exc_info:
            resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": _domain_spec()},
                profile_files=(Path("/no/such/file.yml"),),
            )
        assert exc_info.value.code == "PROFILE_NOT_FOUND"


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_empty_checks_from_domain(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        spec = _domain_spec(checks=())
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            requested_domains=("training",),
        )
        assert plan.checks == ()
        assert plan.domains == ("training",)

    def test_report_dir_not_created_by_planner(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        report_dir = tmp_path / "reports" / "nested"
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": _domain_spec()},
            report_dir=report_dir,
        )
        assert plan.report_dir == report_dir
        assert not report_dir.exists()

    def test_warnings_collected(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "network"}))
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert len(plan.warnings) > 0
        assert any("skipped" in w for w in plan.warnings)


# ===========================================================================
# Slotted dataclass config loading
# ===========================================================================


class TestSlottedDataclassConfig:
    def test_slotted_dataclass_config_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")

        @dataclass(slots=True)
        class _SlottedConfig:
            strict: bool = True
            max_errors: int = 50

        spec = _domain_spec()
        spec = DomainSpec(
            name="training",
            markers=("outline.yml",),
            load_default_config=_SlottedConfig,
            load_checks=lambda: (_check_spec(),),
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.domain_configs["training"]["strict"] is True
        assert plan.domain_configs["training"]["max_errors"] == 50

    def test_plain_dict_config_still_works(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        spec = DomainSpec(
            name="training",
            markers=("outline.yml",),
            load_default_config=lambda: {"key": "value"},
            load_checks=lambda: (_check_spec(),),
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.domain_configs["training"]["key"] == "value"


# ===========================================================================
# Capability policy — closed-world model
# ===========================================================================


class TestCapabilityPolicyClosedWorld:
    def test_unlisted_capability_denied(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "live"}))
        spec = _domain_spec(checks=(check,))
        policy = CapabilityPolicy(
            allowed=frozenset({"process"}),
            denied=frozenset(),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            capability_policy=policy,
        )
        assert plan.checks[0].skip_code == "capability-denied"
        assert "not in allowlist" in (plan.checks[0].skip_reason or "")

    def test_explicitly_denied_takes_priority(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(capabilities=frozenset({"process", "network"}))
        spec = _domain_spec(checks=(check,))
        policy = CapabilityPolicy(
            allowed=frozenset({"process"}),
            denied=frozenset({"network"}),
        )
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            capability_policy=policy,
        )
        assert plan.checks[0].skip_code == "capability-denied"
        assert "denied capabilities" in (plan.checks[0].skip_reason or "")


# ===========================================================================
# CachePolicy.require_known_version
# ===========================================================================


class TestRequireKnownVersion:
    def test_tool_without_version_not_cacheable(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(
            adapter=ToolAdapter(command=("yamllint",)),
            cache=CachePolicy(require_known_version=True),
            capabilities=frozenset({"process"}),
        )
        spec = _domain_spec(checks=(check,))

        with patch("verdx.engine.runtime.planner.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("not found")
            plan = resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": spec},
            )

        skipped_check = plan.checks[0]
        if skipped_check.skip_code is None:
            assert skipped_check.cacheable is False

    def test_tool_with_version_cacheable(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(
            adapter=ToolAdapter(
                command=("yamllint",),
                version_probe=VersionProbe(
                    command=("yamllint", "--version"),
                    parse_pattern=r"(\d+\.\d+\.\d+)",
                ),
            ),
            cache=CachePolicy(require_known_version=True),
            capabilities=frozenset({"process"}),
        )
        spec = _domain_spec(checks=(check,))

        @dataclass
        class _FakeResult:
            returncode: int = 0
            stdout: str = "yamllint 1.35.1"
            stderr: str = ""

        with patch("verdx.engine.runtime.planner.subprocess.run", return_value=_FakeResult()):
            plan = resolve_gate_plan(
                target=tmp_path,
                available_domains={"training": spec},
            )

        assert plan.checks[0].cacheable is True

    def test_python_adapter_ignores_require_known_version(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(cache=CachePolicy(require_known_version=True))
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        assert plan.checks[0].cacheable is True


# ===========================================================================
# Secret redaction in provenance
# ===========================================================================


class TestSecretRedaction:
    def test_secret_key_redacted_in_provenance(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(default_config={"api_key": "super-secret-123"})
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        secret_entries = [p for p in plan.provenance if p.path and p.path[-1] == "api_key"]
        for entry in secret_entries:
            assert entry.value_repr == "<redacted>"
            assert entry.operation == "redact"

    def test_non_secret_key_not_redacted(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        check = _check_spec(default_config={"strict": True})
        spec = _domain_spec(checks=(check,))
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
        )
        strict_entries = [p for p in plan.provenance if p.path and p.path[-1] == "strict"]
        for entry in strict_entries:
            assert entry.value_repr is not None
            assert entry.value_repr != "<redacted>"

    def test_password_key_redacted_via_override(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")
        spec = _domain_spec()
        plan = resolve_gate_plan(
            target=tmp_path,
            available_domains={"training": spec},
            cli_overrides={"db_password": "hunter2"},
        )
        pw_entries = [p for p in plan.provenance if p.path and p.path[-1] == "db_password"]
        assert len(pw_entries) > 0
        for entry in pw_entries:
            assert entry.value_repr == "<redacted>"
