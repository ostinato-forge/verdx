"""Tests for ta CLI — 2 commands: validate, fix."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from verdx.cli import main
from verdx.types import Assessment


def _fake_assessment(**kwargs: object) -> Assessment:
    defaults = {
        "score": 85,
        "verdict": "pass",
        "observations": (),
        "decision_hash": "a" * 64,
        "overrides_applied": (),
        "validators_run": ("v1",),
        "metadata": {"target": "test", "profile": "standard"},
    }
    defaults.update(kwargs)
    return Assessment(**defaults)  # type: ignore[arg-type]


class TestCLIStructure:
    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "verdx" in result.output

    def test_help_shows_two_commands(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "validate" in result.output
        assert "fix" in result.output

    def test_no_old_commands(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        for old_cmd in (
            "domains",
            "deps",
            "open",
            "status",
            "run",
            "approve",
            "quick",
            "override",
            "dashboard",
            "design-doc",
            "rule",
            "pr-run",
            "pr-verdict",
            "pr-provenance",
        ):
            assert old_cmd not in result.output, f"Old command still present: {old_cmd}"


class TestValidateCommand:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert result.exit_code == 0
        assert "--static-only" in result.output
        assert "--changed-only" in result.output
        assert "--profile" in result.output
        assert "--domain" in result.output
        assert "--output" in result.output
        assert "--live" not in result.output  # hidden deprecated flag

    def test_profiles_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert "quick" in result.output
        assert "standard" in result.output
        assert "strict" in result.output

    def test_missing_target(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate"])
        assert result.exit_code != 0

    @patch("verdx.cli.verdx_validate")
    def test_pass_exit_code_0(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment(score=85, verdict="pass")
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["validate", str(target)])
        assert result.exit_code == 0

    @patch("verdx.cli.verdx_validate")
    def test_block_exit_code_1(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment(score=60, verdict="block")
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["validate", str(target)])
        assert result.exit_code == 1

    @patch("verdx.cli.verdx_validate")
    def test_error_exit_code_2(self, mock_validate, tmp_path) -> None:
        mock_validate.side_effect = RuntimeError("no domains detected")
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["validate", str(target)])
        assert result.exit_code == 2

    @patch("verdx.cli.verdx_validate")
    def test_json_output(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["validate", str(target), "--output", "json"])
        import json

        data = json.loads(result.output)
        assert data["score"] == 85

    @patch("verdx.cli.verdx_validate")
    def test_text_output(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["validate", str(target), "--output", "text"])
        assert "85/100" in result.output
        assert "PASS" in result.output

    @patch("verdx.cli.verdx_validate")
    def test_passes_profile(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["validate", str(target), "--profile", "strict"])
        mock_validate.assert_called_once()
        assert mock_validate.call_args.kwargs["profile"] == "strict"

    @patch("verdx.cli.verdx_validate")
    def test_live_flag_accepted_as_noop(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["validate", str(target), "--live"])
        assert result.exit_code == 0
        assert mock_validate.call_args.kwargs["live"] is True

    @patch("verdx.cli.verdx_validate")
    def test_default_is_live(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["validate", str(target)])
        assert mock_validate.call_args.kwargs["live"] is True

    @patch("verdx.cli.verdx_validate")
    def test_static_only_flag(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["validate", str(target), "--static-only"])
        assert mock_validate.call_args.kwargs["live"] is False

    @patch("verdx.cli.verdx_validate")
    def test_passes_changed_only(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["validate", str(target), "--changed-only"])
        assert mock_validate.call_args.kwargs["changed_only"] is True


class TestFixCommand:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["fix", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--profile" in result.output
        assert "--domain" in result.output

    @patch("verdx.cli.verdx_fix")
    def test_runs_fix(self, mock_fix, tmp_path) -> None:
        from verdx.types import FixResult

        assessment = _fake_assessment()
        mock_fix.return_value = FixResult(
            assessment_before=assessment,
            assessment_after=assessment,
            fixes_applied=(),
            iterations=1,
            remaining_observations=(),
        )
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["fix", str(target), "--output", "text"])
        assert result.exit_code == 0
        assert "No fixable observations found" in result.output

    @patch("verdx.cli.verdx_fix")
    def test_fix_json_output(self, mock_fix, tmp_path) -> None:
        from verdx.types import FixResult

        assessment = _fake_assessment()
        mock_fix.return_value = FixResult(
            assessment_before=assessment,
            assessment_after=assessment,
            fixes_applied=(),
            iterations=1,
            remaining_observations=(),
        )
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        result = runner.invoke(main, ["fix", str(target), "--output", "json"])
        import json

        data = json.loads(result.output)
        assert data["iterations"] == 1

    @patch("verdx.cli.verdx_fix")
    def test_fix_static_only(self, mock_fix, tmp_path) -> None:
        from verdx.types import FixResult

        assessment = _fake_assessment()
        mock_fix.return_value = FixResult(
            assessment_before=assessment,
            assessment_after=assessment,
            fixes_applied=(),
            iterations=1,
            remaining_observations=(),
        )
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["fix", str(target), "--static-only"])
        assert mock_fix.call_args.kwargs["live"] is False

    @patch("verdx.cli.verdx_fix")
    def test_fix_default_is_live(self, mock_fix, tmp_path) -> None:
        from verdx.types import FixResult

        assessment = _fake_assessment()
        mock_fix.return_value = FixResult(
            assessment_before=assessment,
            assessment_after=assessment,
            fixes_applied=(),
            iterations=1,
            remaining_observations=(),
        )
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["fix", str(target)])
        assert mock_fix.call_args.kwargs["live"] is True


class TestNoFlywheelFlag:
    @patch("verdx.cli.verdx_validate")
    def test_validate_no_flywheel(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["validate", str(target), "--no-flywheel"])
        assert mock_validate.call_args.kwargs["flywheel"] is False

    @patch("verdx.cli.verdx_validate")
    def test_validate_default_flywheel_on(self, mock_validate, tmp_path) -> None:
        mock_validate.return_value = _fake_assessment()
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["validate", str(target)])
        assert mock_validate.call_args.kwargs["flywheel"] is True

    @patch("verdx.cli.verdx_fix")
    def test_fix_no_flywheel(self, mock_fix, tmp_path) -> None:
        from verdx.types import FixResult

        assessment = _fake_assessment()
        mock_fix.return_value = FixResult(
            assessment_before=assessment,
            assessment_after=assessment,
            fixes_applied=(),
            iterations=1,
            remaining_observations=(),
        )
        runner = CliRunner()
        target = tmp_path / "target"
        target.mkdir()
        runner.invoke(main, ["fix", str(target), "--no-flywheel"])
        assert mock_fix.call_args.kwargs["flywheel"] is False

    def test_validate_help_shows_no_flywheel(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert "--no-flywheel" in result.output

    def test_fix_help_shows_no_flywheel(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["fix", "--help"])
        assert "--no-flywheel" in result.output


class TestFlywheelCLI:
    def test_flywheel_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["flywheel", "--help"])
        assert result.exit_code == 0
        assert "inspect" in result.output
        assert "search" in result.output
        assert "review" in result.output
        assert "block" in result.output
        assert "unblock" in result.output
        assert "promote" in result.output
        assert "reset" in result.output

    def test_flywheel_in_main_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "flywheel" in result.output

    def test_inspect_empty_target(self, tmp_path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["flywheel", "inspect", str(tmp_path)])
        assert result.exit_code == 0
        assert "Patterns:" in result.output

    def test_inspect_json(self, tmp_path) -> None:
        import json as json_mod

        runner = CliRunner()
        result = runner.invoke(main, ["flywheel", "inspect", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = json_mod.loads(result.output)
        assert "patterns_summary" in data
        assert "corrections_summary" in data

    def test_search_no_match(self, tmp_path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["flywheel", "search", "nonexistent", str(tmp_path)])
        assert result.exit_code == 0
        assert "No matching" in result.output

    def test_review_no_issues(self, tmp_path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["flywheel", "review", str(tmp_path)])
        assert result.exit_code == 0
