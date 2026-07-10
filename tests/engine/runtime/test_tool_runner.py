"""Tests for tool_runner — ToolAdapter subprocess execution and parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from verdx.contracts.check import (
    BatchMode,
    ExitCodePolicy,
    FileInputSpec,
    JsonPathParser,
    RegexParser,
    SarifParser,
    ToolAdapter,
)
from verdx.contracts.context import CheckContext
from verdx.contracts.dimensions import DimLevel, Severity
from verdx.contracts.plan import ResolvedCheck
from verdx.engine.runtime.tool_runner import (
    _build_command,
    _chunk_files,
    _classify_exit,
    _parse_output,
    _resolve_file_inputs,
    run_tool_check,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _tool_adapter(**overrides: Any) -> ToolAdapter:
    defaults: dict[str, Any] = {
        "command": ("echo", "ok"),
        "parser": SarifParser(),
    }
    return ToolAdapter(**(defaults | overrides))


def _resolved_check(**overrides: Any) -> ResolvedCheck:
    from verdx.contracts.check import CheckSpec

    spec_overrides = overrides.pop("spec_overrides", {})
    adapter = overrides.pop("adapter", _tool_adapter())
    spec_defaults: dict[str, Any] = {
        "check_id": "lint-check",
        "domain": "training",
        "title": "Lint Check",
        "category": "structure",
        "version": "1.0.0",
        "adapter": adapter,
        "dimensions": {"correctness": DimLevel.HIGH},
    }
    spec = CheckSpec(**(spec_defaults | spec_overrides))

    check_defaults: dict[str, Any] = {
        "domain": "training",
        "check_id": "lint-check",
        "check_version": "1.0.0",
        "spec": spec,
        "effective_config": {},
        "config_hash": "sha256:abc",
        "capabilities": frozenset({"process"}),
        "critical": False,
        "cacheable": True,
    }
    return ResolvedCheck(**(check_defaults | overrides))


def _check_context(tmp_path: Path, **overrides: Any) -> CheckContext:
    defaults: dict[str, Any] = {
        "run_id": "run-test123",
        "target_key": str(tmp_path),
        "target": tmp_path,
        "target_content_hash": "sha256:abc",
        "domain": "training",
        "check_id": "lint-check",
        "check_version": "1.0.0",
        "config": {},
        "timeout_seconds": 300,
    }
    return CheckContext(**(defaults | overrides))


# ===========================================================================
# Exit code classification
# ===========================================================================


class TestClassifyExit:
    def test_success_code(self) -> None:
        policy = ExitCodePolicy(success=(0,), findings=(1,))
        assert _classify_exit(0, policy) == "success"

    def test_findings_code(self) -> None:
        policy = ExitCodePolicy(success=(0,), findings=(1,))
        assert _classify_exit(1, policy) == "findings"

    def test_failure_code(self) -> None:
        policy = ExitCodePolicy(success=(0,), failure=(2,))
        assert _classify_exit(2, policy) == "failure"

    def test_unknown_defaults_to_failure(self) -> None:
        policy = ExitCodePolicy(success=(0,))
        assert _classify_exit(99, policy) == "failure"

    def test_unknown_not_failure_when_disabled(self) -> None:
        policy = ExitCodePolicy(success=(0,), unknown_is_failure=False)
        assert _classify_exit(99, policy) == "findings"


# ===========================================================================
# File input resolution
# ===========================================================================


class TestResolveFileInputs:
    def test_glob_matching(self, tmp_path: Path) -> None:
        (tmp_path / "a.yml").write_text("")
        (tmp_path / "b.yml").write_text("")
        (tmp_path / "c.txt").write_text("")
        spec = FileInputSpec(globs=("*.yml",))
        ctx = _check_context(tmp_path)
        files = _resolve_file_inputs(spec, ctx)
        assert len(files) == 2
        assert all(f.suffix == ".yml" for f in files)

    def test_excludes_applied(self, tmp_path: Path) -> None:
        (tmp_path / "a.yml").write_text("")
        (tmp_path / "b.yml").write_text("")
        spec = FileInputSpec(globs=("*.yml",), excludes=("b.yml",))
        ctx = _check_context(tmp_path)
        files = _resolve_file_inputs(spec, ctx)
        assert len(files) == 1
        assert files[0].name == "a.yml"

    def test_sorted_output(self, tmp_path: Path) -> None:
        for name in ("z.txt", "a.txt", "m.txt"):
            (tmp_path / name).write_text("")
        spec = FileInputSpec(globs=("*.txt",))
        ctx = _check_context(tmp_path)
        files = _resolve_file_inputs(spec, ctx)
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_scratch_root(self, tmp_path: Path) -> None:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        (scratch / "out.json").write_text("{}")
        spec = FileInputSpec(globs=("*.json",), root="scratch")
        ctx = _check_context(tmp_path, scratch_dir=scratch)
        files = _resolve_file_inputs(spec, ctx)
        assert len(files) == 1

    def test_no_scratch_dir_returns_empty(self, tmp_path: Path) -> None:
        spec = FileInputSpec(globs=("*.json",), root="scratch")
        ctx = _check_context(tmp_path)
        files = _resolve_file_inputs(spec, ctx)
        assert files == []


# ===========================================================================
# Batch chunking
# ===========================================================================


class TestChunkFiles:
    def test_none_strategy_single_chunk(self) -> None:
        files = [Path(f"/f{i}") for i in range(5)]
        batch = BatchMode(strategy="none")
        chunks = _chunk_files(files, batch)
        assert len(chunks) == 1
        assert len(chunks[0]) == 5

    def test_argv_max_files(self) -> None:
        files = [Path(f"/f{i}") for i in range(10)]
        batch = BatchMode(strategy="argv", max_files_per_invocation=3)
        chunks = _chunk_files(files, batch)
        assert len(chunks) == 4
        assert len(chunks[0]) == 3
        assert len(chunks[-1]) == 1

    def test_empty_files_returns_single_empty_chunk(self) -> None:
        batch = BatchMode(strategy="argv")
        chunks = _chunk_files([], batch)
        assert chunks == [[]]


# ===========================================================================
# Command construction
# ===========================================================================


class TestBuildCommand:
    def test_no_files_returns_base_command(self) -> None:
        adapter = _tool_adapter(command=("ruff", "check"))
        cmd = _build_command(adapter, [])
        assert cmd == ["ruff", "check"]

    def test_argv_appends_files(self) -> None:
        adapter = _tool_adapter(
            command=("ruff", "check"),
            batch=BatchMode(strategy="argv"),
        )
        files = [Path("/a.py"), Path("/b.py")]
        cmd = _build_command(adapter, files)
        assert cmd == ["ruff", "check", "/a.py", "/b.py"]

    def test_argv_placeholder_substitution(self) -> None:
        adapter = _tool_adapter(
            command=("tool", "--input", "{files}", "--output", "result"),
            batch=BatchMode(strategy="argv", placeholder="{files}"),
        )
        files = [Path("/x.txt")]
        cmd = _build_command(adapter, files)
        assert cmd == ["tool", "--input", "/x.txt", "--output", "result"]


# ===========================================================================
# Output parsing — SARIF
# ===========================================================================


class TestParseSarif:
    def test_basic_sarif(self, tmp_path: Path) -> None:
        sarif = {
            "runs": [
                {
                    "results": [
                        {
                            "ruleId": "no-eval",
                            "level": "error",
                            "message": {"text": "eval is dangerous"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "app.py"},
                                        "region": {"startLine": 42},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        }
        adapter = _tool_adapter(parser=SarifParser())
        check = _resolved_check()
        ctx = _check_context(tmp_path)
        drafts = _parse_output(json.dumps(sarif), adapter, check, ctx)
        assert len(drafts) == 1
        assert drafts[0].rule_id == "lint-check/no-eval"
        assert drafts[0].location == "app.py:42"
        assert drafts[0].severity == Severity.HIGH

    def test_empty_sarif(self, tmp_path: Path) -> None:
        sarif = {"runs": [{"results": []}]}
        adapter = _tool_adapter(parser=SarifParser())
        drafts = _parse_output(
            json.dumps(sarif), adapter, _resolved_check(), _check_context(tmp_path)
        )
        assert drafts == []

    def test_severity_map_override(self, tmp_path: Path) -> None:
        sarif = {
            "runs": [{"results": [{"ruleId": "r1", "level": "note", "message": {"text": "x"}}]}]
        }
        parser = SarifParser(severity_map={"note": Severity.CRITICAL})
        adapter = _tool_adapter(parser=parser)
        drafts = _parse_output(
            json.dumps(sarif), adapter, _resolved_check(), _check_context(tmp_path)
        )
        assert drafts[0].severity == Severity.CRITICAL


# ===========================================================================
# Output parsing — JSON path
# ===========================================================================


class TestParseJsonPath:
    def test_basic_jsonpath(self, tmp_path: Path) -> None:
        output = json.dumps(
            {
                "issues": [
                    {"id": "trailing-ws", "msg": "trailing whitespace", "file": "a.txt"},
                    {"id": "long-line", "msg": "line too long", "file": "b.txt"},
                ]
            }
        )
        parser = JsonPathParser(
            observations_path="issues",
            rule_id_path="id",
            message_path="msg",
            location_path="file",
        )
        adapter = _tool_adapter(parser=parser)
        drafts = _parse_output(output, adapter, _resolved_check(), _check_context(tmp_path))
        assert len(drafts) == 2
        assert drafts[0].rule_id == "lint-check/trailing-ws"
        assert drafts[0].location == "a.txt"

    def test_missing_observations_returns_empty(self, tmp_path: Path) -> None:
        parser = JsonPathParser(
            observations_path="results",
            rule_id_path="id",
            message_path="msg",
        )
        adapter = _tool_adapter(parser=parser)
        drafts = _parse_output("{}", adapter, _resolved_check(), _check_context(tmp_path))
        assert drafts == []


# ===========================================================================
# Output parsing — regex
# ===========================================================================


class TestParseRegex:
    def test_basic_regex(self, tmp_path: Path) -> None:
        output = "file.py:10: error: undefined name 'foo'\nfile.py:20: warning: unused var\n"
        parser = RegexParser(pattern=r"(?P<location>\S+:\d+): (?P<severity>\w+): (?P<message>.+)")
        adapter = _tool_adapter(parser=parser)
        drafts = _parse_output(output, adapter, _resolved_check(), _check_context(tmp_path))
        assert len(drafts) == 2
        assert drafts[0].location == "file.py:10"
        assert "undefined" in drafts[0].summary

    def test_no_matches(self, tmp_path: Path) -> None:
        parser = RegexParser(pattern=r"ERROR: (?P<message>.+)")
        adapter = _tool_adapter(parser=parser)
        drafts = _parse_output("all good\n", adapter, _resolved_check(), _check_context(tmp_path))
        assert drafts == []


# ===========================================================================
# run_tool_check integration
# ===========================================================================


class TestRunToolCheck:
    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_successful_tool(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sarif = {"runs": [{"results": []}]}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tool"], returncode=0, stdout=json.dumps(sarif), stderr=""
        )
        adapter = _tool_adapter()
        check = _resolved_check()
        ctx = _check_context(tmp_path)
        result, recovery = run_tool_check(adapter, ctx, check)
        assert result.status == "completed"
        assert recovery is None

    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_command_not_found(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.side_effect = FileNotFoundError("tool not found")
        result, recovery = run_tool_check(
            _tool_adapter(), _check_context(tmp_path), _resolved_check()
        )
        assert result.status == "failed"
        assert "command not found" in (result.error or "")
        assert recovery is not None
        assert recovery.kind == "blocked"

    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_timeout(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tool", timeout=5)
        result, recovery = run_tool_check(
            _tool_adapter(), _check_context(tmp_path), _resolved_check()
        )
        assert result.status == "failed"
        assert "timed out" in (result.error or "")
        assert recovery is not None
        assert recovery.kind == "blocked"

    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_failure_exit_code(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tool"], returncode=2, stdout="", stderr="crash"
        )
        adapter = _tool_adapter(exit_codes=ExitCodePolicy(success=(0,), failure=(2,)))
        result, recovery = run_tool_check(adapter, _check_context(tmp_path), _resolved_check())
        assert result.status == "failed"
        assert recovery is not None
        assert recovery.kind == "retryable"

    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_no_shell_true(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tool"], returncode=0, stdout='{"runs":[{"results":[]}]}', stderr=""
        )
        run_tool_check(_tool_adapter(), _check_context(tmp_path), _resolved_check())
        call_kwargs = mock_run.call_args
        assert "shell" not in call_kwargs.kwargs or call_kwargs.kwargs["shell"] is False

    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_env_merged(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tool"], returncode=0, stdout='{"runs":[{"results":[]}]}', stderr=""
        )
        adapter = _tool_adapter(env={"CUSTOM_VAR": "custom_value"})
        run_tool_check(adapter, _check_context(tmp_path), _resolved_check())
        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env")
        assert env is not None
        assert env["CUSTOM_VAR"] == "custom_value"

    @patch("verdx.engine.runtime.tool_runner.subprocess.run")
    def test_findings_exit_code_parses_output(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sarif = {
            "runs": [
                {"results": [{"ruleId": "r1", "level": "warning", "message": {"text": "issue"}}]}
            ]
        }
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tool"], returncode=1, stdout=json.dumps(sarif), stderr=""
        )
        adapter = _tool_adapter(exit_codes=ExitCodePolicy(success=(0,), findings=(1,)))
        result, recovery = run_tool_check(adapter, _check_context(tmp_path), _resolved_check())
        assert result.status == "completed"
        assert len(result.drafts) == 1
        assert recovery is None
