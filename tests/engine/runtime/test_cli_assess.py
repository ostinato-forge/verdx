"""Tests for the 'assess' CLI subcommand."""

from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from verdx.engine.runtime.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path / "state"


@pytest.fixture()
def target_dir(tmp_path):
    """Create a minimal target directory."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("# Overview\n\nSome content.\n")
    return target


@pytest.fixture()
def pipeline_file(tmp_path):
    """Create a simple pipeline config."""
    p = tmp_path / "pipeline.yml"
    p.write_text(
        yaml.dump(
            {
                "version": 1,
                "pipeline": {
                    "name": "test",
                    "stages": [
                        {
                            "name": "check",
                            "validators": ["markdown-headings", "markdown-links"],
                            "gate": {"type": "score", "threshold": 50},
                            "on_pass": {
                                "labels": ["passed"],
                                "advance": "done",
                            },
                            "on_fail": {
                                "labels": ["failed"],
                                "comment": "report",
                            },
                        },
                    ],
                },
            }
        )
    )
    return p


class TestAssessCommand:
    def test_assess_target_not_found(self, runner, pipeline_file, state_dir):
        result = runner.invoke(
            main,
            [
                "assess",
                "--stage=check",
                "--target=/nonexistent/path",
                f"--pipeline={pipeline_file}",
                f"--state-dir={state_dir}",
            ],
        )
        assert result.exit_code != 0

    def test_assess_unknown_domain(self, runner, target_dir, pipeline_file, state_dir):
        result = runner.invoke(
            main,
            [
                "assess",
                "--stage=check",
                f"--target={target_dir}",
                f"--pipeline={pipeline_file}",
                f"--state-dir={state_dir}",
                "--domain=nope",
            ],
        )
        assert result.exit_code != 0
        assert "unknown validator domain 'nope'" in result.output

    def test_assess_unknown_stage(self, runner, target_dir, pipeline_file, state_dir):
        result = runner.invoke(
            main,
            [
                "assess",
                "--stage=nonexistent",
                f"--target={target_dir}",
                f"--pipeline={pipeline_file}",
                f"--state-dir={state_dir}",
            ],
        )
        assert result.exit_code != 0
