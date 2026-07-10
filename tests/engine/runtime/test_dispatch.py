"""Tests for handler manifest loading and dispatch."""

import json
import subprocess

import pytest
import yaml

from verdx.engine.runtime.dispatch import (
    DispatchError,
    HandlerEntry,
    HandlerManifest,
    find_handler,
    load_manifest,
    run_handler,
)

VALID_MANIFEST = {
    "schema_version": "handler-manifest/v1",
    "handlers": [
        {
            "id": "verdx",
            "version": "1.0.0",
            "command": ["verdx", "validate"],
            "capabilities": ["static-analysis"],
            "stage": "Validate",
            "output_artifact": "RunAssessment",
            "args_template": ["{target}", "--domain", "training", "--json"],
        }
    ],
}


class TestLoadManifest:
    def test_load_valid_manifest(self, tmp_path):
        agentic = tmp_path / ".agentic"
        agentic.mkdir()
        (agentic / "handlers.yml").write_text(yaml.dump(VALID_MANIFEST))

        manifest = load_manifest(tmp_path)
        assert manifest is not None
        assert manifest.schema_version == "handler-manifest/v1"
        assert len(manifest.handlers) == 1
        assert manifest.handlers[0].id == "verdx"

    def test_load_missing_manifest(self, tmp_path):
        (tmp_path / ".agentic").mkdir()
        assert load_manifest(tmp_path) is None

    def test_load_invalid_manifest_raises(self, tmp_path):
        agentic = tmp_path / ".agentic"
        agentic.mkdir()
        (agentic / "handlers.yml").write_text("handlers: not-a-list\n")

        with pytest.raises((TypeError, ValueError, KeyError)):
            load_manifest(tmp_path)


class TestFindHandler:
    def test_find_existing_stage(self):
        manifest = HandlerManifest.model_validate(VALID_MANIFEST)
        handler = find_handler(manifest, "Validate")
        assert handler is not None
        assert handler.id == "verdx"

    def test_find_missing_stage(self):
        manifest = HandlerManifest.model_validate(VALID_MANIFEST)
        assert find_handler(manifest, "Build") is None


class TestRunHandler:
    @pytest.fixture
    def handler(self):
        return HandlerEntry(
            id="test-handler",
            version="1.0.0",
            command=["echo-handler"],
            stage="Validate",
            output_artifact="RunAssessment",
        )

    def test_success(self, handler, monkeypatch):
        output = {"checks_run": 5, "checks_passed": 5, "coverage_status": "complete"}

        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(output), stderr="")

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)
        result = run_handler(handler, "/some/path")
        assert result == output

    def test_nonzero_exit_raises(self, handler, monkeypatch):
        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="validation error")

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)
        with pytest.raises(DispatchError, match="exited with code 1"):
            run_handler(handler, "/some/path")

    def test_invalid_json_raises(self, handler, monkeypatch):
        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)
        with pytest.raises(DispatchError, match="invalid JSON"):
            run_handler(handler, "/some/path")

    def test_timeout_raises(self, handler, monkeypatch):
        def mock_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 300))

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)
        with pytest.raises(DispatchError, match="timed out"):
            run_handler(handler, "/some/path")

    def test_command_not_found_raises(self, handler, monkeypatch):
        def mock_run(cmd, **kwargs):
            raise FileNotFoundError("No such file")

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)
        with pytest.raises(DispatchError, match="not found"):
            run_handler(handler, "/some/path")

    def test_args_template_expansion(self, monkeypatch):
        handler = HandlerEntry(
            id="test",
            version="1.0.0",
            command=["validator"],
            stage="Validate",
            output_artifact="RunAssessment",
            args_template=["{target}", "--json"],
        )
        captured_cmd = []

        def mock_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)
        run_handler(handler, "/my/path")
        assert captured_cmd == ["validator", "/my/path", "--json"]
