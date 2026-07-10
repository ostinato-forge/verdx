"""Tests for the verdx CLI -- trace and compliance commands."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner

from verdx.contracts.events import ArtifactRefs, EventType, StateEvent
from verdx.contracts.roles import Role
from verdx.contracts.states import State
from verdx.engine.runtime.cli import main

_T0 = datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def initialized(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
    runner.invoke(main, ["init"])
    return tmp_path


def _write_events(root, events):
    """Write events to the state.jsonl file."""
    state_file = root / ".agentic" / "state.jsonl"
    lines = [e.model_dump_json() for e in events]
    state_file.write_text("\n".join(lines) + "\n")


def _minimal_events():
    """Minimal 4-event happy path for CLI tests."""
    t = _T0
    dt = timedelta(minutes=30)
    return [
        StateEvent(
            intent_id="CLI-001",
            event_type=EventType.intent_created,
            previous_state=None,
            next_state=State.Clarifying,
            actor="alice",
            authority_role=Role.requester,
            timestamp=t,
            base_commit="abc1234",
            policy_version="ptl-v1",
            artifact_refs=ArtifactRefs(),
        ),
        StateEvent(
            intent_id="CLI-001",
            event_type=EventType.clarify_completed,
            previous_state=State.Clarifying,
            next_state=State.SpecDrafting,
            actor="alice",
            authority_role=Role.requester,
            timestamp=t + dt,
            base_commit="abc1234",
            policy_version="ptl-v1",
            artifact_refs=ArtifactRefs(created=("IntentSummary:CLI-001/v1",)),
        ),
        StateEvent(
            intent_id="CLI-001",
            event_type=EventType.spec_completed,
            previous_state=State.SpecDrafting,
            next_state=State.AwaitingApproval,
            actor="bob",
            authority_role=Role.implementer,
            timestamp=t + 2 * dt,
            base_commit="abc1234",
            policy_version="ptl-v1",
            artifact_refs=ArtifactRefs(created=("SpecPlan:CLI-001/v1",)),
        ),
        StateEvent(
            intent_id="CLI-001",
            event_type=EventType.approval_granted,
            previous_state=State.AwaitingApproval,
            next_state=State.Building,
            actor="carol",
            authority_role=Role.scope_owner,
            timestamp=t + 3 * dt,
            base_commit="abc1234",
            policy_version="ptl-v1",
            artifact_refs=ArtifactRefs(),
        ),
    ]


class TestTraceCommand:
    def test_ascii_output_contains_state_names(self, runner, initialized):
        _write_events(initialized, _minimal_events())
        result = runner.invoke(main, ["trace", "-i", "CLI-001"])
        assert result.exit_code == 0
        assert "Clarifying" in result.output
        assert "SpecDrafting" in result.output
        assert "alice" in result.output

    def test_mermaid_output_contains_state_diagram(self, runner, initialized):
        _write_events(initialized, _minimal_events())
        result = runner.invoke(main, ["trace", "-i", "CLI-001", "--format", "mermaid"])
        assert result.exit_code == 0
        assert "stateDiagram-v2" in result.output

    def test_no_events_prints_message(self, runner, initialized):
        result = runner.invoke(main, ["trace", "-i", "NONEXISTENT-001"])
        assert result.exit_code == 0
        assert "No events found." in result.output

    def test_not_initialized_raises_error(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
        result = runner.invoke(main, ["trace", "-i", "CLI-001"])
        assert result.exit_code != 0
        assert "Not initialized" in result.output


class TestComplianceCommand:
    def test_markdown_contains_section_headers(self, runner, initialized):
        _write_events(initialized, _minimal_events())
        result = runner.invoke(main, ["compliance", "-i", "CLI-001"])
        assert result.exit_code == 0
        assert "# Compliance Report: CLI-001" in result.output
        assert "## Status" in result.output
        assert "## Gate Summary" in result.output
        assert "## Approval Chain" in result.output

    def test_json_output_parses_with_expected_keys(self, runner, initialized):
        _write_events(initialized, _minimal_events())
        result = runner.invoke(main, ["compliance", "-i", "CLI-001", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["intent_id"] == "CLI-001"
        assert "total_events" in data
        assert "gates_satisfied" in data
        assert "approval_chain" in data

    def test_no_events_prints_message(self, runner, initialized):
        result = runner.invoke(main, ["compliance", "-i", "NONEXISTENT-001"])
        assert result.exit_code == 0
        assert "No events found." in result.output

    def test_not_initialized_raises_error(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
        result = runner.invoke(main, ["compliance", "-i", "CLI-001"])
        assert result.exit_code != 0
        assert "Not initialized" in result.output
