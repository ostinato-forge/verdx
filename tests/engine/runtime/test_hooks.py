"""Tests for event hooks — config, registry, dispatch, and CLI commands."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.hooks import (
    VALID_EVENTS,
    HookConfig,
    HookRegistry,
    dispatch_hook,
)


class TestHookConfig:
    def test_valid_config(self):
        hook = HookConfig(url="https://example.com/hook")
        assert hook.url == "https://example.com/hook"
        assert hook.events == VALID_EVENTS
        assert hook.headers == {}

    def test_custom_events(self):
        hook = HookConfig(
            url="https://example.com/hook",
            events=frozenset({"run-start", "run-complete"}),
        )
        assert hook.events == frozenset({"run-start", "run-complete"})

    def test_custom_headers(self):
        hook = HookConfig(
            url="https://example.com/hook",
            headers={"Authorization": "Bearer tok"},
        )
        assert hook.headers == {"Authorization": "Bearer tok"}

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="url must be non-empty"):
            HookConfig(url="")

    def test_unknown_events_raises(self):
        with pytest.raises(ValueError, match="unknown events"):
            HookConfig(url="https://example.com", events=frozenset({"bogus"}))


class TestHookRegistry:
    def test_empty_registry(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        assert registry.hooks == []

    def test_add_hook(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        hook = HookConfig(url="https://example.com/hook")
        registry.add(hook)
        assert len(registry.hooks) == 1
        assert registry.hooks[0].url == "https://example.com/hook"

    def test_add_persists_to_yaml(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(HookConfig(url="https://a.test/h"))
        raw = yaml.safe_load((tmp_path / "hooks.yml").read_text())
        assert len(raw["hooks"]) == 1
        assert raw["hooks"][0]["url"] == "https://a.test/h"

    def test_loads_from_existing_yaml(self, tmp_path: Path):
        (tmp_path / "hooks.yml").write_text(
            yaml.dump(
                {
                    "hooks": [
                        {"url": "https://a.test/h", "events": ["run-start"]},
                    ]
                }
            ),
        )
        registry = HookRegistry(tmp_path)
        assert len(registry.hooks) == 1
        assert registry.hooks[0].events == frozenset({"run-start"})

    def test_remove_existing_hook(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(HookConfig(url="https://a.test/h"))
        assert registry.remove("https://a.test/h") is True
        assert registry.hooks == []

    def test_remove_nonexistent_returns_false(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        assert registry.remove("https://nope.test") is False

    def test_hooks_returns_copy(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(HookConfig(url="https://a.test/h"))
        hooks = registry.hooks
        hooks.clear()
        assert len(registry.hooks) == 1


class TestDispatchHook:
    def test_dispatches_matching_event(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(HookConfig(url="https://a.test/h"))

        with patch("verdx.engine.runtime.hooks.urllib.request.urlopen") as mock_open:
            dispatch_hook(registry, "run-start", {"run_id": "r1"})
            assert mock_open.call_count == 1
            req = mock_open.call_args[0][0]
            assert req.full_url == "https://a.test/h"
            body = json.loads(req.data)
            assert body == {"run_id": "r1"}

    def test_skips_non_matching_event(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(
            HookConfig(
                url="https://a.test/h",
                events=frozenset({"run-complete"}),
            )
        )

        with patch("verdx.engine.runtime.hooks.urllib.request.urlopen") as mock_open:
            dispatch_hook(registry, "run-start", {"run_id": "r1"})
            mock_open.assert_not_called()

    def test_includes_custom_headers(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(
            HookConfig(
                url="https://a.test/h",
                headers={"X-Token": "secret"},
            )
        )

        with patch("verdx.engine.runtime.hooks.urllib.request.urlopen") as mock_open:
            dispatch_hook(registry, "run-start", {})
            req = mock_open.call_args[0][0]
            assert req.get_header("X-token") == "secret"
            assert req.get_header("Content-type") == "application/json"

    def test_failure_does_not_raise(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(HookConfig(url="https://a.test/h"))

        with patch(
            "verdx.engine.runtime.hooks.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            dispatch_hook(registry, "run-start", {"run_id": "r1"})

    def test_dispatches_to_multiple_hooks(self, tmp_path: Path):
        registry = HookRegistry(tmp_path)
        registry.add(HookConfig(url="https://a.test/h1"))
        registry.add(HookConfig(url="https://a.test/h2"))

        with patch("verdx.engine.runtime.hooks.urllib.request.urlopen") as mock_open:
            dispatch_hook(registry, "run-start", {})
            assert mock_open.call_count == 2


class TestHookCli:
    def test_add_and_list(self, tmp_path: Path):
        runner = CliRunner()
        state = str(tmp_path)

        result = runner.invoke(
            main,
            [
                "hook",
                "add",
                "https://example.com/h",
                "--state-dir",
                state,
            ],
        )
        assert result.exit_code == 0
        assert "Hook added" in result.output

        result = runner.invoke(main, ["hook", "list", "--state-dir", state])
        assert result.exit_code == 0
        assert "https://example.com/h" in result.output

    def test_add_with_events(self, tmp_path: Path):
        runner = CliRunner()
        state = str(tmp_path)

        result = runner.invoke(
            main,
            [
                "hook",
                "add",
                "https://example.com/h",
                "--events",
                "run-start,run-complete",
                "--state-dir",
                state,
            ],
        )
        assert result.exit_code == 0
        assert "run-complete" in result.output
        assert "run-start" in result.output

    def test_add_with_headers(self, tmp_path: Path):
        runner = CliRunner()
        state = str(tmp_path)

        result = runner.invoke(
            main,
            [
                "hook",
                "add",
                "https://example.com/h",
                "--header",
                "Authorization:Bearer tok",
                "--state-dir",
                state,
            ],
        )
        assert result.exit_code == 0

        result = runner.invoke(main, ["hook", "list", "--state-dir", state])
        assert "Authorization" in result.output

    def test_add_invalid_event(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "hook",
                "add",
                "https://example.com/h",
                "--events",
                "bogus",
                "--state-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "unknown events" in result.output

    def test_add_invalid_header_format(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "hook",
                "add",
                "https://example.com/h",
                "--header",
                "no-colon-here",
                "--state-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "invalid header" in result.output

    def test_remove_existing(self, tmp_path: Path):
        runner = CliRunner()
        state = str(tmp_path)

        runner.invoke(
            main,
            [
                "hook",
                "add",
                "https://example.com/h",
                "--state-dir",
                state,
            ],
        )
        result = runner.invoke(
            main,
            [
                "hook",
                "remove",
                "https://example.com/h",
                "--state-dir",
                state,
            ],
        )
        assert result.exit_code == 0
        assert "Hook removed" in result.output

    def test_remove_nonexistent(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "hook",
                "remove",
                "https://nope.test",
                "--state-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_list_empty(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "hook",
                "list",
                "--state-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "No hooks configured" in result.output
