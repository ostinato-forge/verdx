"""Tests for the verdx CLI -- emit command and integration."""

import json
import subprocess
import typing

import pytest
import yaml
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


def _emit(runner, args, base_commit="abc1234", actor="tester"):
    base = [
        "emit",
        "--base-commit",
        base_commit,
        "--actor",
        actor,
    ]
    return runner.invoke(main, base + args)


class TestEmitHappyPath:
    def test_emit_intent_created(self, runner, initialized):
        result = _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        assert result.exit_code == 0
        assert "Emitted:" in result.output
        assert "Clarifying" in result.output

    def test_emit_sequential_transitions(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "clarify_completed",
                "-r",
                "requester",
                "--artifact-ref",
                "IntentSummary:T-001/v1",
            ],
        )
        assert result.exit_code == 0
        assert "SpecDrafting" in result.output

    def test_emit_with_artifact_ref(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "clarify_completed",
                "-r",
                "requester",
                "--artifact-ref",
                "IntentSummary:T-001/v1",
            ],
        )
        assert result.exit_code == 0

    def test_emit_multiple_artifact_refs(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "clarify_completed",
                "-r",
                "requester",
                "--artifact-ref",
                "IntentSummary:T-001/v1",
                "--artifact-ref",
                "IntentSummary:T-001/v2",
            ],
        )
        assert result.exit_code == 0

    def test_emit_with_optional_metadata(self, runner, initialized):
        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "requester",
                "--branch",
                "intent/T-001",
                "--pr-id",
                "ROLE-123",
                "--detail",
                "Test intent",
                "--elapsed-seconds",
                "60",
                "--attention-minutes",
                "1",
            ],
        )
        assert result.exit_code == 0


class TestEmitValidation:
    def test_invalid_transition_rejected(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        result = _emit(runner, ["-i", "T-001", "-e", "build_completed", "-r", "implementer"])
        assert result.exit_code != 0
        assert "No valid transition" in result.output

    def test_wrong_role_rejected(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "clarify_completed",
                "-r",
                "requester",
                "--artifact-ref",
                "IntentSummary:T-001/v1",
            ],
        )

        _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "spec_completed",
                "-r",
                "implementer",
                "--artifact-ref",
                "SpecPlan:T-001/v1",
            ],
        )

        result = _emit(
            runner,
            ["-i", "T-001", "-e", "approval_granted", "-r", "implementer"],
        )
        assert result.exit_code != 0
        assert "not permitted" in result.output

    def test_missing_gate_artifact_rejected(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        result = _emit(
            runner,
            ["-i", "T-001", "-e", "clarify_completed", "-r", "requester"],
        )
        assert result.exit_code != 0
        assert "Gate artifact" in result.output

    def test_unknown_event_type_rejected(self, runner, initialized):
        result = runner.invoke(
            main,
            [
                "emit",
                "-i",
                "T-001",
                "-e",
                "bogus",
                "-r",
                "requester",
                "--actor",
                "tester",
                "--base-commit",
                "abc1234",
            ],
        )
        assert result.exit_code != 0

    def test_unknown_role_rejected(self, runner, initialized):
        result = runner.invoke(
            main,
            [
                "emit",
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "bogus",
                "--actor",
                "tester",
                "--base-commit",
                "abc1234",
            ],
        )
        assert result.exit_code != 0

    def test_emit_without_init_fails(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._find_root", lambda: tmp_path)
        result = _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        assert result.exit_code != 0
        assert "Not initialized" in result.output


class TestEmitBlocked:
    def test_block_and_unblock(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        result = _emit(
            runner,
            ["-i", "T-001", "-e", "blocked", "-r", "implementer"],
        )
        assert result.exit_code == 0
        assert "Blocked" in result.output

        result = _emit(
            runner,
            ["-i", "T-001", "-e", "unblocked", "-r", "implementer"],
        )
        assert result.exit_code == 0
        assert "Clarifying" in result.output


class TestEmitDefaults:
    def test_auto_detects_base_commit(self, runner, initialized, monkeypatch):
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "deadbeef")
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_actor", lambda: "autouser")
        result = runner.invoke(
            main,
            ["emit", "-i", "T-001", "-e", "intent_created", "-r", "requester"],
        )
        assert result.exit_code == 0
        assert "autouser" in result.output

    def test_explicit_overrides_git(self, runner, initialized, monkeypatch):
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "auto123")
        result = _emit(
            runner,
            ["-i", "T-001", "-e", "intent_created", "-r", "requester"],
            base_commit="e1e2e3f",
            actor="manualuser",
        )
        assert result.exit_code == 0
        assert "manualuser" in result.output

    def test_missing_actor_and_no_git_fails(self, runner, initialized, monkeypatch):
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_actor", lambda: None)
        result = runner.invoke(
            main,
            [
                "emit",
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "requester",
                "--base-commit",
                "abc1234",
            ],
        )
        assert result.exit_code != 0
        assert "Cannot detect actor" in result.output

    def test_explicit_timestamp(self, runner, initialized):
        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "requester",
                "--timestamp",
                "2026-01-15T10:30:00+00:00",
            ],
        )
        assert result.exit_code == 0

    def test_invalid_timestamp_rejected(self, runner, initialized):
        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "requester",
                "--timestamp",
                "not-a-date",
            ],
        )
        assert result.exit_code != 0
        assert "Invalid timestamp" in result.output

    def test_invalid_base_commit_rejected(self, runner, initialized):
        result = _emit(
            runner,
            ["-i", "T-001", "-e", "intent_created", "-r", "requester"],
            base_commit="ZZZZZZZ",
        )
        assert result.exit_code != 0

    def test_missing_base_commit_and_no_git_fails(self, runner, initialized, monkeypatch):
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: None)
        result = runner.invoke(
            main,
            [
                "emit",
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "requester",
                "--actor",
                "tester",
            ],
        )
        assert result.exit_code != 0
        assert "Cannot detect base commit" in result.output


class TestEmitSampleGolden:
    """Drive the entire sample scenario through the emit CLI invocations."""

    INTENT = "intent-001"

    def _emit_event(self, runner, event_type, actor, role, base="a1b2c3d", **kwargs):
        args = [
            "-i",
            self.INTENT,
            "-e",
            event_type,
            "-r",
            role,
            "--parent-intent-id",
            "intent-parent",
            "--child-unit-type",
            "section",
            "--branch",
            "intent/intent-001",
            "--pr-id",
            "ROLE-4521",
        ]
        for ref in kwargs.get("artifact_refs", []):
            args.extend(["--artifact-ref", ref])
        for ref in kwargs.get("consumed_refs", []):
            args.extend(["--consumed-ref", ref])
        if kwargs.get("detail"):
            args.extend(["--detail", kwargs["detail"]])
        for tag in kwargs.get("reason_tags", []):
            args.extend(["--reason-tag", tag])
        if kwargs.get("pr_id"):
            args.extend(["--pr-id", kwargs["pr_id"]])

        return _emit(runner, args, base_commit=base, actor=actor)

    def test_full_scenario(self, runner, initialized):
        # Event 1: intent_created
        r = self._emit_event(runner, "intent_created", "owner-a", "requester")
        assert r.exit_code == 0, r.output

        # Event 2: clarify_completed
        r = self._emit_event(
            runner,
            "clarify_completed",
            "owner-a",
            "requester",
            artifact_refs=["IntentSummary:intent-001/v1"],
        )
        assert r.exit_code == 0, r.output

        # Event 3: spec_completed
        r = self._emit_event(
            runner,
            "spec_completed",
            "builder-a",
            "implementer",
            artifact_refs=["SpecPlan:intent-001/v1"],
            consumed_refs=["IntentSummary:intent-001/v1"],
        )
        assert r.exit_code == 0, r.output

        # Event 4: approval_granted
        r = self._emit_event(
            runner,
            "approval_granted",
            "owner-a",
            "scope_owner",
            consumed_refs=["SpecPlan:intent-001/v1"],
        )
        assert r.exit_code == 0, r.output

        # Event 5: build_completed
        r = self._emit_event(
            runner,
            "build_completed",
            "builder-a",
            "implementer",
            artifact_refs=["BuildReport:intent-001/v1"],
            consumed_refs=["SpecPlan:intent-001/v1"],
        )
        assert r.exit_code == 0, r.output

        # Event 6: override_requested
        r = self._emit_event(
            runner,
            "override_requested",
            "checker-a",
            "implementer",
            artifact_refs=["RunAssessment:intent-001/v1"],
            consumed_refs=["BuildReport:intent-001/v1"],
            reason_tags=["coverage_gap"],
        )
        assert r.exit_code == 0, r.output

        # Event 7: override_granted
        r = self._emit_event(
            runner,
            "override_granted",
            "owner-a",
            "tech_lead",
            artifact_refs=["OverrideDecision:intent-001/v1"],
            consumed_refs=["RunAssessment:intent-001/v1"],
        )
        assert r.exit_code == 0, r.output

        # Event 8: review_submitted
        r = self._emit_event(
            runner,
            "review_submitted",
            "reviewer-a",
            "reviewer",
            artifact_refs=["ReviewDecision:intent-001/v1"],
            consumed_refs=[
                "RunAssessment:intent-001/v1",
                "BuildReport:intent-001/v1",
                "OverrideDecision:intent-001/v1",
            ],
            pr_id="PR-127",
        )
        assert r.exit_code == 0, r.output

        # Event 9: rebase_required
        r = self._emit_event(
            runner,
            "rebase_required",
            "merge-a",
            "merge_actor",
            pr_id="PR-127",
        )
        assert r.exit_code == 0, r.output

        # Event 10: rebase_completed (new base commit)
        r = self._emit_event(
            runner,
            "rebase_completed",
            "builder-a",
            "implementer",
            base="e5f6a7b",
            pr_id="PR-127",
        )
        assert r.exit_code == 0, r.output

        # Event 11: revalidation_completed
        r = self._emit_event(
            runner,
            "revalidation_completed",
            "checker-a",
            "implementer",
            base="e5f6a7b",
            artifact_refs=["RunAssessment:intent-001/v2"],
            consumed_refs=["BuildReport:intent-001/v1"],
            pr_id="PR-127",
        )
        assert r.exit_code == 0, r.output

        # Event 12: carry_forward_granted
        r = self._emit_event(
            runner,
            "carry_forward_granted",
            "reviewer-a",
            "reviewer",
            base="e5f6a7b",
            artifact_refs=["ApprovalCarryForward:intent-001/v1"],
            consumed_refs=[
                "ReviewDecision:intent-001/v1",
                "RunAssessment:intent-001/v2",
            ],
            pr_id="PR-127",
        )
        assert r.exit_code == 0, r.output

        # Event 13: integration_completed
        r = self._emit_event(
            runner,
            "integration_completed",
            "merge-a",
            "merge_actor",
            base="e5f6a7b",
            artifact_refs=["IntegrationReceipt:intent-001/v1"],
            consumed_refs=["ApprovalCarryForward:intent-001/v1"],
            pr_id="PR-127",
        )
        assert r.exit_code == 0, r.output

        # Verify: replay is clean
        replay_result = runner.invoke(main, ["replay", "-i", self.INTENT])
        assert replay_result.exit_code == 0, replay_result.output
        assert "Replay clean." in replay_result.output

        # Verify: status shows Integrated
        status_result = runner.invoke(main, ["status", "-i", self.INTENT])
        assert status_result.exit_code == 0
        assert "Integrated" in status_result.output


class TestValidate:
    HANDLER_OUTPUT: typing.ClassVar[dict[str, object]] = {
        "checks_run": 10,
        "checks_passed": 9,
        "coverage_status": "complete",
        "score": {"overall": 85},
    }

    def _setup_manifest(self, tmp_path):
        manifest = {
            "schema_version": "handler-manifest/v1",
            "handlers": [
                {
                    "id": "test-validator",
                    "version": "1.0.0",
                    "command": ["test-validate"],
                    "stage": "Validate",
                    "output_artifact": "RunAssessment",
                }
            ],
        }
        (tmp_path / ".agentic" / "handlers.yml").write_text(yaml.dump(manifest))

    def _advance_to_building(self, runner):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "clarify_completed",
                "-r",
                "requester",
                "--artifact-ref",
                "IntentSummary:T-001/v1",
            ],
        )
        _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "spec_completed",
                "-r",
                "implementer",
                "--artifact-ref",
                "SpecPlan:T-001/v1",
            ],
        )
        _emit(runner, ["-i", "T-001", "-e", "approval_granted", "-r", "scope_owner"])
        _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "build_completed",
                "-r",
                "implementer",
                "--artifact-ref",
                "BuildReport:T-001/v1",
            ],
        )

    def test_validate_happy_path(self, runner, initialized, monkeypatch):
        self._setup_manifest(initialized)
        self._advance_to_building(runner)

        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(self.HANDLER_OUTPUT), stderr=""
            )

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)

        result = runner.invoke(main, ["dispatch", "-i", "T-001", "--target", "/content"])
        assert result.exit_code == 0, result.output
        assert "Stored:" in result.output
        assert "Score: 85" in result.output
        assert "verdx emit" in result.output

    def test_validate_no_manifest(self, runner, initialized):
        self._advance_to_building(runner)
        result = runner.invoke(main, ["dispatch", "-i", "T-001", "--target", "/content"])
        assert result.exit_code != 0
        assert "No handler manifest" in result.output

    def test_validate_wrong_state(self, runner, initialized):
        self._setup_manifest(initialized)
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        result = runner.invoke(main, ["dispatch", "-i", "T-001", "--target", "/content"])
        assert result.exit_code != 0
        assert "Clarifying" in result.output

    def test_validate_unknown_intent(self, runner, initialized):
        self._setup_manifest(initialized)
        result = runner.invoke(main, ["dispatch", "-i", "NOPE", "--target", "/content"])
        assert result.exit_code != 0
        assert "No events found" in result.output

    def test_validate_then_emit_then_replay(self, runner, initialized, monkeypatch):
        """End-to-end: emit -> validate -> emit validation_completed -> replay clean."""
        self._setup_manifest(initialized)
        self._advance_to_building(runner)

        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(self.HANDLER_OUTPUT), stderr=""
            )

        monkeypatch.setattr("verdx.engine.runtime.dispatch.subprocess.run", mock_run)

        result = runner.invoke(main, ["dispatch", "-i", "T-001", "--target", "/content"])
        assert result.exit_code == 0, result.output

        inspect_result = runner.invoke(main, ["artifact", "-i", "T-001", "-t", "RunAssessment"])
        assert inspect_result.exit_code == 0
        assert json.loads(inspect_result.output) == self.HANDLER_OUTPUT

        emit_result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "validation_completed",
                "-r",
                "implementer",
                "--artifact-ref",
                "RunAssessment:T-001/v1",
            ],
        )
        assert emit_result.exit_code == 0, emit_result.output
        assert "AwaitingReview" in emit_result.output


def _advance_full_lifecycle(runner, intent_id="T-001", commit="abc1234"):
    """Advance an intent through the full happy path to Integrating."""
    _emit(runner, ["-i", intent_id, "-e", "intent_created", "-r", "requester"], base_commit=commit)
    _emit(
        runner,
        [
            "-i",
            intent_id,
            "-e",
            "clarify_completed",
            "-r",
            "requester",
            "--artifact-ref",
            f"IntentSummary:{intent_id}/v1",
        ],
        base_commit=commit,
    )
    _emit(
        runner,
        [
            "-i",
            intent_id,
            "-e",
            "spec_completed",
            "-r",
            "implementer",
            "--artifact-ref",
            f"SpecPlan:{intent_id}/v1",
        ],
        base_commit=commit,
    )
    _emit(
        runner,
        ["-i", intent_id, "-e", "approval_granted", "-r", "scope_owner"],
        base_commit=commit,
    )
    _emit(
        runner,
        [
            "-i",
            intent_id,
            "-e",
            "build_completed",
            "-r",
            "implementer",
            "--artifact-ref",
            f"BuildReport:{intent_id}/v1",
        ],
        base_commit=commit,
    )
    _emit(
        runner,
        [
            "-i",
            intent_id,
            "-e",
            "validation_completed",
            "-r",
            "implementer",
            "--artifact-ref",
            f"RunAssessment:{intent_id}/v1",
        ],
        base_commit=commit,
    )
    _emit(
        runner,
        [
            "-i",
            intent_id,
            "-e",
            "review_submitted",
            "-r",
            "reviewer",
            "--artifact-ref",
            f"ReviewDecision:{intent_id}/v1",
        ],
        base_commit=commit,
    )


class TestStale:
    def test_stale_detects_mismatch(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "fff9999")
        result = runner.invoke(main, ["stale"])
        assert result.exit_code == 1
        assert "STALE" in result.output
        assert "rebase_required" in result.output

    def test_stale_fresh_intent(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")
        result = runner.invoke(main, ["stale"])
        assert result.exit_code == 0
        assert "fresh" in result.output

    def test_stale_skips_terminal(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        _emit(runner, ["-i", "T-001", "-e", "abandoned", "-r", "scope_owner"])
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "fff9999")
        result = runner.invoke(main, ["stale"])
        assert result.exit_code == 0

    def test_stale_filter_by_intent(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "fff9999")
        result = runner.invoke(main, ["stale", "-i", "T-001"])
        assert result.exit_code == 1
        assert "STALE" in result.output


class TestMetricsCommand:
    def test_metrics_output(self, runner, initialized):
        _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "intent_created",
                "-r",
                "requester",
                "--elapsed-seconds",
                "60",
                "--attention-minutes",
                "5",
            ],
        )
        result = runner.invoke(main, ["metrics"])
        assert result.exit_code == 0
        assert "Attention:" in result.output
        assert "5.0" in result.output

    def test_metrics_no_events(self, runner, initialized):
        result = runner.invoke(main, ["metrics"])
        assert result.exit_code == 0
        assert "No events" in result.output

    def test_metrics_filter_by_intent(self, runner, initialized):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])
        _emit(runner, ["-i", "T-002", "-e", "intent_created", "-r", "requester"])
        result = runner.invoke(main, ["metrics", "-i", "T-001"])
        assert result.exit_code == 0
        assert "T-001" in result.output
        assert "T-002" not in result.output


class TestReplayGuard:
    def test_replay_guard_blocks_stale_integration(self, runner, initialized):
        """Integration rejected when base commit changed after validation."""
        _advance_full_lifecycle(runner, commit="abc1234")
        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "integration_completed",
                "-r",
                "merge_actor",
                "--artifact-ref",
                "IntegrationReceipt:T-001/v1",
            ],
            base_commit="def5678",
        )
        assert result.exit_code != 0
        assert "Validation was against" in result.output or "rebase required" in result.output

    def test_replay_guard_allows_fresh_integration(self, runner, initialized):
        _advance_full_lifecycle(runner, commit="abc1234")
        result = _emit(
            runner,
            [
                "-i",
                "T-001",
                "-e",
                "integration_completed",
                "-r",
                "merge_actor",
                "--artifact-ref",
                "IntegrationReceipt:T-001/v1",
            ],
            base_commit="abc1234",
        )
        assert result.exit_code == 0
        assert "Integrated" in result.output


class TestClassify:
    def test_classify_eligible(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_name_only", lambda: ["README.md"]
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_diff_lines", lambda: 2)
        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_digest", lambda: "sha256:abc123"
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")

        result = runner.invoke(main, ["classify", "-i", "T-001", "--type", "text"])
        assert result.exit_code == 0, result.output
        assert "Eligible" in result.output
        assert "fast_path_eligible" in result.output

    def test_classify_ineligible_multiple_files(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_name_only", lambda: ["a.py", "b.py"]
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_diff_lines", lambda: 5)
        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_digest", lambda: "sha256:abc123"
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")

        result = runner.invoke(main, ["classify", "-i", "T-001", "--type", "text"])
        assert result.exit_code == 1
        assert "Not eligible" in result.output
        assert "clarify_completed" in result.output

    def test_classify_with_extra_denylist(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_name_only",
            lambda: ["guides/ch01.adoc"],
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_diff_lines", lambda: 1)
        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_digest", lambda: "sha256:abc123"
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")

        result = runner.invoke(
            main,
            ["classify", "-i", "T-001", "--type", "text", "--extra-denylist", "guides/**"],
        )
        assert result.exit_code == 1
        assert "Not eligible" in result.output

    def test_classify_stores_artifact(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_name_only", lambda: ["README.md"]
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_diff_lines", lambda: 1)
        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_digest", lambda: "sha256:abc123"
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")

        result = runner.invoke(main, ["classify", "-i", "T-001", "--type", "text"])
        assert result.exit_code == 0

        inspect_result = runner.invoke(
            main, ["artifact", "-i", "T-001", "-t", "FastPathEligibility"]
        )
        assert inspect_result.exit_code == 0
        data = json.loads(inspect_result.output)
        assert data["eligible"] is True
        assert data["base_commit"] == "abc1234"
        assert data["diff_digest"] == "sha256:abc123"

    def test_classify_with_policy_denylist(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_name_only", lambda: ["labs/ex01.adoc"]
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_diff_lines", lambda: 1)
        monkeypatch.setattr(
            "verdx.engine.runtime.cli._helpers._git_diff_digest", lambda: "sha256:abc123"
        )
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")

        policy_path = initialized / ".agentic" / "policy.yml"
        policy_path.write_text(yaml.dump({"fast_path": {"denylist": ["labs/**"]}}))

        result = runner.invoke(main, ["classify", "-i", "T-001", "--type", "text"])
        assert result.exit_code == 1
        assert "Not eligible" in result.output

    def test_classify_no_git_diff_fails(self, runner, initialized, monkeypatch):
        _emit(runner, ["-i", "T-001", "-e", "intent_created", "-r", "requester"])

        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_diff_name_only", lambda: None)
        monkeypatch.setattr("verdx.engine.runtime.cli._helpers._git_short_head", lambda: "abc1234")

        result = runner.invoke(main, ["classify", "-i", "T-001", "--type", "text"])
        assert result.exit_code != 0
        assert "Cannot" in result.output
