"""Tests for verdx record and inspect CLI commands."""

import json

import pytest
from click.testing import CliRunner

from verdx.engine.runtime.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def initialized(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
    runner.invoke(main, ["init"])
    return tmp_path


SAMPLE_ARTIFACT = {"score": {"overall": 85}, "checks_run": 10, "checks_passed": 9}


class TestRecord:
    def test_record_from_file(self, runner, initialized, tmp_path):
        artifact_file = tmp_path / "assessment.json"
        artifact_file.write_text(json.dumps(SAMPLE_ARTIFACT))

        result = runner.invoke(
            main,
            ["record", "-i", "T-001", "-t", "RunAssessment", "-f", str(artifact_file)],
        )
        assert result.exit_code == 0
        assert "Recorded:" in result.output
        assert "T-001/RunAssessment.json" in result.output

    def test_record_from_stdin(self, runner, initialized):
        result = runner.invoke(
            main,
            ["record", "-i", "T-001", "-t", "RunAssessment"],
            input=json.dumps(SAMPLE_ARTIFACT),
        )
        assert result.exit_code == 0
        assert "Recorded:" in result.output

    def test_record_invalid_json_rejected(self, runner, initialized):
        result = runner.invoke(
            main,
            ["record", "-i", "T-001", "-t", "RunAssessment"],
            input="not json",
        )
        assert result.exit_code != 0
        assert "Invalid JSON" in result.output

    def test_record_unknown_artifact_type_rejected(self, runner, initialized):
        result = runner.invoke(
            main,
            ["record", "-i", "T-001", "-t", "Bogus"],
            input=json.dumps(SAMPLE_ARTIFACT),
        )
        assert result.exit_code != 0

    def test_record_without_init_fails(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
        result = runner.invoke(
            main,
            ["record", "-i", "T-001", "-t", "RunAssessment"],
            input=json.dumps(SAMPLE_ARTIFACT),
        )
        assert result.exit_code != 0
        assert "Not initialized" in result.output


class TestInspect:
    def test_inspect_round_trip(self, runner, initialized, tmp_path):
        artifact_file = tmp_path / "data.json"
        artifact_file.write_text(json.dumps(SAMPLE_ARTIFACT))

        runner.invoke(
            main,
            ["record", "-i", "T-001", "-t", "RunAssessment", "-f", str(artifact_file)],
        )

        result = runner.invoke(main, ["artifact", "-i", "T-001", "-t", "RunAssessment"])
        assert result.exit_code == 0
        loaded = json.loads(result.output)
        assert loaded == SAMPLE_ARTIFACT

    def test_inspect_missing_artifact(self, runner, initialized):
        result = runner.invoke(main, ["artifact", "-i", "T-001", "-t", "RunAssessment"])
        assert result.exit_code != 0
        assert "No RunAssessment artifact" in result.output

    def test_inspect_without_init_fails(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
        result = runner.invoke(main, ["artifact", "-i", "T-001", "-t", "RunAssessment"])
        assert result.exit_code != 0
        assert "Not initialized" in result.output
