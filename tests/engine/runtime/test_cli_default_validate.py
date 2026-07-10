"""Tests for the default-validate CLI behaviour.

These tests verify that:
- a bare ``<path>`` routes to the ``validate`` subcommand
- Domain auto-detection works via marker files
- Explicit subcommands still work untouched
- Edge cases (missing paths, flags, etc.) are handled correctly

These tests exercise the routing and detection logic in isolation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from tests.contracts.test_models import _make_run_assessment
from verdx.contracts.assessment import AssessmentConfidence, EscalationSummary, Score
from verdx.contracts.config import EvidenceLimits
from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
from verdx.engine.runtime.cli import (
    _quick_profiles,
    _resolve_domain_for_targets,
    main,
)
from verdx.engine.runtime.config import DomainConfig
from verdx.engine.runtime.domains import (
    _reset_discovery_cache,
    detect_domain,
)
from verdx.engine.runtime.engine import RunOutcome
from verdx.engine.validation.escalation import (
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """Reset domain discovery cache before and after each test."""
    _reset_discovery_cache()
    yield
    _reset_discovery_cache()


def _minimal_config() -> DomainConfig:
    """Build a minimal DomainConfig sufficient for CLI tests."""
    return DomainConfig(
        version=1,
        policy=EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="default",
                    match=RuleMatch(quantifier="any", dimension="*", level="low"),
                    level="L1",
                ),
            ),
        ),
        critical_validators=frozenset(),
        dimensions=(),
        evidence_limits=EvidenceLimits(
            max_evidence_bytes=65536, max_string_length=4096, max_data_entries=50
        ),
        timeout_seconds=300,
        dimension_map=None,
        scoring_config=None,
    )


def _fake_spec(name: str = "training", markers: tuple[str, ...] = ()) -> DomainSpec:
    return DomainSpec(
        name=name,
        markers=markers,
        load_default_config=_minimal_config,
        load_checks=lambda: (),
        load_profiles=lambda: (),
        discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
    )


def _patch_discovery(specs: dict[str, DomainSpec]):
    """Patch _discover() to return a fixed registry."""
    return patch("verdx.engine.runtime.domains._discover", return_value=specs)


def _score(overall: int = 95) -> Score:
    return Score(
        overall=overall,
        dimension_scores={},
        observation_count=0,
        escalation_distribution={},
        coverage_penalty=0,
    )


def _outcome(**assessment_overrides: Any) -> RunOutcome:
    defaults = {
        "observations": (),
        "recovery_items": (),
        "coverage_gaps": (),
        "validator_statuses": (),
        "checks_run": 0,
        "checks_passed": 0,
        "validators_clean": 0,
        "score": _score(),
        "escalation_summary": EscalationSummary(counts={}, highest=EscalationLevel.L0),
        "confidence": AssessmentConfidence.STRONG,
    }
    assessment = _make_run_assessment(**(defaults | assessment_overrides))
    return RunOutcome(
        assessment=assessment,
        assessment_json=json.dumps(assessment.to_dict()),
        report_md="report\n",
        observations=list(assessment.observations),
        validator_statuses=list(assessment.validator_statuses),
        recovery_items=list(assessment.recovery_items),
    )


# ===================================================================
# Unit tests: detect_domain
# ===================================================================


class TestDetectDomain:
    """Tests for the detect_domain heuristic."""

    def test_detects_training_from_outline_yml(self, tmp_path: Path) -> None:
        """A directory containing outline.yml should detect as 'training'."""
        (tmp_path / "outline.yml").write_text("course: test")

        spec = _fake_spec("training", markers=("outline.yml",))
        with _patch_discovery({"training": spec}):
            result = detect_domain(tmp_path)

        assert result == "training"

    def test_returns_none_for_empty_directory(self, tmp_path: Path) -> None:
        """An empty directory should return None (no domain detected)."""
        spec = _fake_spec("training", markers=("outline.yml",))
        with _patch_discovery({"training": spec}):
            result = detect_domain(tmp_path)
        assert result is None

    def test_returns_none_for_non_training_directory(self, tmp_path: Path) -> None:
        """A directory with random files but no outline.yml returns None."""
        (tmp_path / "README.md").write_text("hello")
        (tmp_path / "setup.py").write_text("# setup")
        spec = _fake_spec("training", markers=("outline.yml",))
        with _patch_discovery({"training": spec}):
            result = detect_domain(tmp_path)
        assert result is None

    def test_detects_from_file_path_parent(self, tmp_path: Path) -> None:
        """When given a file path, should look in parent directory."""
        (tmp_path / "outline.yml").write_text("course: test")
        target_file = tmp_path / "somefile.adoc"
        target_file.write_text("= Title")

        spec = _fake_spec("training", markers=("outline.yml",))
        with _patch_discovery({"training": spec}):
            result = detect_domain(target_file)

        assert result == "training"

    def test_returns_none_when_no_domains_registered(self, tmp_path: Path) -> None:
        """If marker exists but no domains are registered, return None."""
        (tmp_path / "outline.yml").write_text("course: test")

        with _patch_discovery({}):
            result = detect_domain(tmp_path)

        assert result is None


# ===================================================================
# Unit tests: _DefaultValidateGroup
# ===================================================================


class TestDefaultValidateGroup:
    """Tests for the custom Click group that routes paths to validate."""

    def test_existing_path_prepends_validate(self, tmp_path: Path, runner: CliRunner) -> None:
        """A path that exists on disk should be routed to 'validate'."""
        target = tmp_path / "my-course"
        target.mkdir()

        result = runner.invoke(main, [str(target)])

        # The validate command should run and fail because there's no
        # domain to detect (no outline.yml, no --domain).
        assert result.exit_code != 0
        assert "Cannot auto-detect validator domain" in result.output

    def test_existing_path_with_outline_yml_detects_domain(
        self, tmp_path: Path, runner: CliRunner
    ) -> None:
        """A path with a marker file should route to validate and auto-detect domain.

        We mock detect_domain to prove the routing pipeline reaches auto-detection,
        without requiring any external domain package to be installed.
        """
        target = tmp_path / "my-course"
        target.mkdir()
        (target / "outline.yml").write_text("course: test")

        from verdx.engine.runtime.domains import UnknownDomainError

        # Patch detect_domain to return 'training', and get_domain to raise
        # UnknownDomainError with a distinctive message proving we got past detection.
        with (
            patch("verdx.engine.runtime.cli._helpers.detect_domain", return_value="training"),
            patch(
                "verdx.engine.runtime.cli._helpers.get_domain",
                side_effect=UnknownDomainError("DOMAIN_RESOLVED_OK"),
            ),
        ):
            result = runner.invoke(main, [str(target)])

        # The error should come from get_domain (proving detection worked),
        # not from "Cannot auto-detect".
        assert "Cannot auto-detect" not in result.output
        assert "DOMAIN_RESOLVED_OK" in result.output

    def test_explicit_subcommand_not_intercepted(self, runner: CliRunner) -> None:
        """Known subcommands should NOT be redirected to validate."""
        # 'history' is a known subcommand — it should not be treated as a path
        result = runner.invoke(main, ["history", "--help"])
        assert result.exit_code == 0
        assert "Show target validation history" in result.output

    def test_flags_not_intercepted(self, runner: CliRunner) -> None:
        """Arguments starting with '-' should be handled normally."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "verdx" in result.output

    def test_version_flag_still_works(self, runner: CliRunner) -> None:
        """--version should work as before."""
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "verdx" in result.output

    def test_nonexistent_path_not_intercepted(self, runner: CliRunner) -> None:
        """A string that is not a subcommand AND not a path should show an error."""
        result = runner.invoke(main, ["/nonexistent/path/to/course"])
        # Click should fail because it's neither a known command nor a valid path
        assert result.exit_code != 0

    def test_validate_subcommand_still_works(self, tmp_path: Path, runner: CliRunner) -> None:
        """Explicit 'validate' subcommand should still work."""
        target = tmp_path / "my-course"
        target.mkdir()

        result = runner.invoke(main, ["validate", str(target)])

        # Should hit the validate command (fail at domain detection without --domain)
        assert result.exit_code != 0
        assert "Cannot auto-detect validator domain" in result.output

    def test_path_with_domain_flag(self, tmp_path: Path, runner: CliRunner) -> None:
        """verdx <path> --domain <name> should route to validate correctly."""
        target = tmp_path / "my-course"
        target.mkdir()

        # This will fail because "fake-domain" isn't registered, but it
        # proves the routing worked and --domain was passed through.
        result = runner.invoke(main, [str(target), "--domain", "fake-domain"])

        assert result.exit_code != 0
        assert "unknown validator domain" in result.output

    def test_init_subcommand_not_intercepted(self, tmp_path: Path, runner: CliRunner) -> None:
        """The 'init' subcommand should work even if a dir named 'init' exists."""
        # This is a subtle edge case: if there's a directory literally named "init"
        # the group should still route to the init command, not to validate.
        (tmp_path / "init").mkdir()

        # When invoking from a different cwd, "init" should resolve as the command
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "Initialize" in result.output


# ===================================================================
# Unit tests: _resolve_domain_for_targets
# ===================================================================


class TestResolveDomainForTargets:
    """Tests for _resolve_domain_for_targets helper."""

    def test_explicit_domain_honoured(self) -> None:
        """When --domain is given, it should be used directly."""
        with patch("verdx.engine.runtime.cli._helpers.get_domain") as mock_get:
            mock_get.return_value = object()
            _resolve_domain_for_targets("training", ("/some/path",))
            mock_get.assert_called_once_with("training")

    def test_explicit_unknown_domain_raises(self) -> None:
        """An explicit but unknown domain should raise ClickException."""
        with pytest.raises(click.ClickException, match="unknown validator domain"):
            _resolve_domain_for_targets("nonexistent", ("/some/path",))

    def test_auto_detect_from_target(self, tmp_path: Path) -> None:
        """When --domain is omitted, auto-detect from the first target."""
        (tmp_path / "outline.yml").write_text("course: test")

        sentinel = object()
        with (
            patch("verdx.engine.runtime.cli._helpers.detect_domain", return_value="training"),
            patch(
                "verdx.engine.runtime.cli._helpers.get_domain", return_value=sentinel
            ) as mock_get,
        ):
            result = _resolve_domain_for_targets(None, (str(tmp_path),))

        assert result is sentinel
        mock_get.assert_called_once_with("training")

    def test_auto_detect_fails_raises(self, tmp_path: Path) -> None:
        """When auto-detection returns None, should raise ClickException."""
        target = tmp_path / "unknown-project"
        target.mkdir()

        with pytest.raises(click.ClickException, match="Cannot auto-detect"):
            _resolve_domain_for_targets(None, (str(target),))

    def test_no_targets_raises(self) -> None:
        """No targets and no domain should raise ClickException."""
        with pytest.raises(click.ClickException, match="Cannot auto-detect"):
            _resolve_domain_for_targets(None, ())


# ===================================================================
# Unit tests: --static-only / live default
# ===================================================================


class TestLiveDefault:
    """Tests for the live-by-default validator wiring."""

    def test_no_live_support_silently_skips(self, tmp_path: Path, runner: CliRunner) -> None:
        """Domain without live validators runs static-only without error."""
        target = tmp_path / "my-course"
        target.mkdir()

        spec = _fake_spec("test", markers=())
        with (
            patch("verdx.engine.runtime.cli._helpers.detect_domain", return_value="test"),
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=spec),
        ):
            result = runner.invoke(main, ["validate", str(target), "--domain", "test"])

        assert result.exit_code == 0

    def test_live_validators_loaded_by_default(self, tmp_path: Path, runner: CliRunner) -> None:
        """Without --static-only, the workflow path handles live checks.

        The non-isolate path delegates to run_gate_workflow which discovers
        checks (including live) through the domain's load_checks() interface.
        """
        target = tmp_path / "my-course"
        target.mkdir()
        (target / "outline.yml").write_text("course: test")

        spec = _fake_spec("test", markers=("outline.yml",))
        with (
            patch("verdx.engine.runtime.cli._helpers.detect_domain", return_value="test"),
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=spec),
        ):
            result = runner.invoke(
                main,
                ["validate", str(target), "--domain", "test", "-h", "myhost"],
            )

        assert result.exit_code == 0

    def test_static_only_skips_live_validators(self, tmp_path: Path, runner: CliRunner) -> None:
        """--static-only prevents execution of live validators."""
        target = tmp_path / "my-course"
        target.mkdir()
        (target / "outline.yml").write_text("course: test")

        live_executed = []

        def fake_live_check(ctx: Any) -> Any:
            live_executed.append(True)
            from verdx.contracts.validator import ValidatorResult

            return ValidatorResult(
                validator_id="live-check",
                validator_version="1.0.0",
                drafts=[],
                checks_run=1,
                checks_passed=1,
                duration_ms=10,
                status="completed",
            )

        fake_live_check.validator_id = "live-check"  # type: ignore[attr-defined]
        fake_live_check.validator_version = "1.0.0"  # type: ignore[attr-defined]
        fake_live_check.__module__ = "tests.engine.runtime.test_cli_default_validate"
        fake_live_check.__qualname__ = "fake_live_check"

        from verdx.contracts.check import check_from_validator

        live_check_spec = check_from_validator(
            fake_live_check, {}, "test", capabilities=frozenset({"live"})
        )
        spec = DomainSpec(
            name="test",
            markers=("outline.yml",),
            load_default_config=_minimal_config,
            load_checks=lambda: (live_check_spec,),
            load_profiles=lambda: (),
            discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
        )
        with (
            patch("verdx.engine.runtime.cli._helpers.detect_domain", return_value="test"),
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=spec),
        ):
            runner.invoke(
                main,
                ["validate", str(target), "--domain", "test", "--static-only"],
            )

        assert not live_executed, "live validators should not execute with --static-only"


# ===================================================================
# Unit tests: quick command
# ===================================================================


class TestQuickCommand:
    def test_quick_profiles_are_first_and_not_duplicated(self) -> None:
        assert _quick_profiles(()) == ("quick",)
        assert _quick_profiles(("strict", "quick")) == ("quick", "strict")

    def test_quick_command_applies_quick_profile_and_static_default(
        self, tmp_path: Path, runner: CliRunner
    ) -> None:
        target = tmp_path / "course"
        target.mkdir()
        state_dir = tmp_path / "state"
        live_called: list[bool] = []

        spec = _fake_spec("test")

        with (
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=spec),
            patch(
                "verdx.engine.runtime.cli._validation.run_gate_workflow", return_value=_outcome()
            ) as workflow,
        ):
            result = runner.invoke(
                main,
                [
                    "quick",
                    str(target),
                    "--domain",
                    "test",
                    "--state-dir",
                    str(state_dir),
                ],
            )

        assert result.exit_code == 0
        assert workflow.call_args.kwargs["profiles"] == ("quick",)
        assert workflow.call_args.kwargs["static_only"] is True
        assert live_called == []

    def test_quick_command_can_layer_profiles_and_include_live_validators(
        self, tmp_path: Path, runner: CliRunner
    ) -> None:
        target = tmp_path / "course"
        target.mkdir()
        state_dir = tmp_path / "state"

        spec = _fake_spec("test")

        with (
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=spec),
            patch(
                "verdx.engine.runtime.cli._validation.run_gate_workflow", return_value=_outcome()
            ) as workflow,
        ):
            result = runner.invoke(
                main,
                [
                    "quick",
                    str(target),
                    "--domain",
                    "test",
                    "--profile",
                    "strict",
                    "--live",
                    "--state-dir",
                    str(state_dir),
                ],
            )

        assert result.exit_code == 0
        assert workflow.call_args.kwargs["profiles"] == ("quick", "strict")
        assert workflow.call_args.kwargs["static_only"] is False


# ===================================================================
# Unit tests: validate gate predicate
# ===================================================================


class TestValidateGatePredicate:
    @pytest.mark.parametrize(
        "format_args",
        [
            [],
            ["--format", "json"],
            ["--format", "junit"],
            ["--format", "sarif"],
            ["--format", "agent"],
        ],
    )
    def test_score_only_pass_with_bad_evidence_exits_one_for_all_formats(
        self,
        tmp_path: Path,
        runner: CliRunner,
        format_args: list[str],
    ) -> None:
        target = tmp_path / "course"
        target.mkdir()
        state_dir = tmp_path / "state"

        with (
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=_fake_spec("test")),
            patch(
                "verdx.engine.runtime.cli._validation.run_gate_workflow",
                return_value=_outcome(
                    coverage_status=CoverageStatus.DEGRADED,
                    confidence=AssessmentConfidence.LOW,
                ),
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "validate",
                    str(target),
                    "--domain",
                    "test",
                    "--state-dir",
                    str(state_dir),
                    *format_args,
                ],
            )

        assert result.exit_code == 1

    def test_partial_coverage_with_guarded_confidence_can_exit_zero(
        self,
        tmp_path: Path,
        runner: CliRunner,
    ) -> None:
        target = tmp_path / "course"
        target.mkdir()
        state_dir = tmp_path / "state"

        with (
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=_fake_spec("test")),
            patch(
                "verdx.engine.runtime.cli._validation.run_gate_workflow",
                return_value=_outcome(
                    coverage_status=CoverageStatus.PARTIAL,
                    confidence=AssessmentConfidence.GUARDED,
                ),
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "validate",
                    str(target),
                    "--domain",
                    "test",
                    "--state-dir",
                    str(state_dir),
                    "--format",
                    "agent",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output[result.output.index("{") :])
        assert data["passed"] is True
        assert data["coverage_ok"] is True
        assert data["confidence_ok"] is True

    def test_agent_multi_target_uses_worst_gate_result(
        self,
        tmp_path: Path,
        runner: CliRunner,
    ) -> None:
        clean = tmp_path / "clean"
        clean.mkdir()
        degraded = tmp_path / "degraded"
        degraded.mkdir()
        state_dir = tmp_path / "state"

        with (
            patch("verdx.engine.runtime.cli._helpers.get_domain", return_value=_fake_spec("test")),
            patch(
                "verdx.engine.runtime.cli._validation.run_gate_workflow",
                side_effect=[
                    _outcome(target_key=str(clean.resolve())),
                    _outcome(
                        target_key=str(degraded.resolve()),
                        coverage_status=CoverageStatus.DEGRADED,
                        confidence=AssessmentConfidence.LOW,
                    ),
                ],
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "validate",
                    str(clean),
                    str(degraded),
                    "--domain",
                    "test",
                    "--state-dir",
                    str(state_dir),
                    "--format",
                    "agent",
                ],
            )

        assert result.exit_code == 1
        data = json.loads(result.output[result.output.index("[") :])
        assert data[0]["passed"] is True
        assert data[0]["coverage_ok"] is True
        assert data[0]["confidence_ok"] is True
        assert data[1]["passed"] is True
        assert data[1]["coverage_ok"] is False
        assert data[1]["confidence_ok"] is False
