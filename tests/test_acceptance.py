"""Acceptance checks exercise real commands and replay their saved evidence."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from verdx.acceptance.policy import parse_policy
from verdx.acceptance.process import describe_artifact
from verdx.acceptance.receipt import decision_hash, derive_decision, replay
from verdx.acceptance.runner import check
from verdx.cli import main


def write_policy(root: Path, rules: list[dict], *, settings: str = "") -> Path:
    rows = ["version = 1", settings]
    for rule in rules:
        rows.append("[[checks]]")
        for key, value in rule.items():
            rows.append(f"{key} = {json.dumps(value)}")
    path = root / "verdx.toml"
    path.write_text("\n".join(rows) + "\n")
    return path


def command_rule(id: str, script: str, **kwargs) -> dict:
    return {"id": id, "command": [sys.executable, "-c", script], **kwargs}


def test_break_repair_recheck_and_offline_replay(tmp_path: Path) -> None:
    source = tmp_path / "value.txt"
    source.write_text("broken")
    write_policy(
        tmp_path,
        [
            command_rule(
                "content",
                (
                    "from pathlib import Path; import sys; "
                    "print('checking value'); "
                    "sys.exit(0 if Path('value.txt').read_text() == 'fixed' else 1)"
                ),
            )
        ],
    )
    failed, failed_path = check(tmp_path)
    assert failed.decision.outcome == "fail"
    assert failed.decision.coverage == "full"
    original = failed_path.read_bytes()
    source.write_text("fixed")
    passed, passed_path = check(tmp_path)
    assert passed.decision.outcome == "pass"
    assert failed.decision_hash != passed.decision_hash
    assert failed_path.read_bytes() == original
    assert passed_path != failed_path
    copy = tmp_path.parent / (tmp_path.name + "-evidence")
    shutil.copytree(failed_path.parent, copy)
    with patch("subprocess.Popen", side_effect=AssertionError("replay executed a command")):
        assert replay(copy / "receipt.json").decision.outcome == "fail"


def test_advisory_failure_stays_visible_without_blocking(tmp_path: Path) -> None:
    write_policy(
        tmp_path,
        [
            command_rule("required", "print('done')"),
            command_rule("advice", "raise SystemExit(1)", required=False),
        ],
    )
    result, _ = check(tmp_path)
    assert result.decision.outcome == "pass"
    assert [row.status for row in result.decision.checks] == ["pass", "fail"]


@pytest.mark.parametrize(
    "rules,expected",
    [
        (
            [command_rule("tests", "raise SystemExit(5)", not_applicable_codes=[5])],
            "not_applicable",
        ),
        (
            [
                command_rule("lint", "pass"),
                command_rule("tests", "raise SystemExit(5)", not_applicable_codes=[5]),
            ],
            "incomplete",
        ),
        ([command_rule("advice", "pass", required=False)], "not_applicable"),
        ([command_rule("tests", "raise SystemExit(3)")], "incomplete"),
        ([{"id": "missing", "command": ["verdx-test-nonexistent-command"]}], "incomplete"),
    ],
)
def test_missing_evidence_never_becomes_a_pass(
    tmp_path: Path, rules: list[dict], expected: str
) -> None:
    write_policy(tmp_path, rules)
    result, path = check(tmp_path)
    assert result.decision.outcome == expected
    assert replay(path).decision == result.decision


def test_timeout_keeps_partial_output_and_stops_children(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished"
    child = "import time; from pathlib import Path; time.sleep(2); Path('child-finished').touch()"
    script = (
        "import subprocess,sys,time; print('started', flush=True); "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    )
    write_policy(tmp_path, [command_rule("timeout", script, timeout=1)])
    result, path = check(tmp_path)
    assert result.decision.outcome == "incomplete"
    execution = result.evidence[0].execution
    assert execution.state == "timeout"
    assert "started" in (path.parent / execution.stdout.path).read_text()
    subprocess.run([sys.executable, "-c", "import time; time.sleep(1.3)"], check=True)
    assert not marker.exists()


def test_output_limit_is_incomplete_and_preserves_bounded_logs(tmp_path: Path) -> None:
    write_policy(
        tmp_path,
        [command_rule("verbose", "print('x' * 10000)")],
        settings="max_output_bytes = 1024",
    )
    result, _ = check(tmp_path)
    execution = result.evidence[0].execution
    assert execution.state == "output_limit"
    assert result.decision.outcome == "incomplete"
    assert execution.stdout.size + execution.stderr.size == 1024


def test_source_and_policy_changes_during_checks_block_acceptance(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("before")
    write_policy(
        tmp_path,
        [
            command_rule(
                "writer", "from pathlib import Path; Path('source.txt').write_text('after')"
            )
        ],
    )
    result, _ = check(tmp_path)
    assert result.decision.outcome == "incomplete"
    assert "input files changed" in result.decision.reasons[0]

    write_policy(
        tmp_path,
        [command_rule("writer", "from pathlib import Path; Path('verdx.toml').write_text('')")],
    )
    result, path = check(tmp_path)
    assert result.decision.outcome == "incomplete"
    assert "policy changed" in result.input_error
    assert replay(path).decision.outcome == "incomplete"


def test_unmatched_check_is_not_run(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    write_policy(
        tmp_path,
        [
            command_rule(
                "docs",
                "from pathlib import Path; Path('should-not-exist').touch()",
                paths=["**/*.md"],
                version_command=["missing-version-probe"],
            )
        ],
    )
    result, _ = check(tmp_path)
    assert result.decision.outcome == "not_applicable"
    assert not marker.exists()


def test_failed_version_probe_prevents_check_and_preserves_evidence(tmp_path: Path) -> None:
    write_policy(
        tmp_path,
        [
            command_rule(
                "versioned",
                "raise AssertionError('must not run')",
                version_command=[sys.executable, "-c", "raise SystemExit(2)"],
            )
        ],
    )
    result, _ = check(tmp_path)
    assert result.decision.outcome == "incomplete"
    assert result.evidence[0].execution.state == "not_run"
    assert result.evidence[0].version.returncode == 2


def test_checker_version_and_policy_change_decision_identity(tmp_path: Path) -> None:
    version = [sys.executable, "-c", "print('1.0')"]
    rule = command_rule("versioned", "print('same output')", version_command=version)
    write_policy(tmp_path, [rule])
    first, _ = check(tmp_path)
    second, _ = check(tmp_path)
    assert first.decision_hash == second.decision_hash
    write_policy(tmp_path, [{**rule, "timeout": 60}])
    third, _ = check(tmp_path)
    assert third.policy_hash != first.policy_hash
    assert third.decision_hash != first.decision_hash


@pytest.mark.parametrize(
    "mutation",
    [
        "output",
        "missing-output",
        "verdict",
        "policy",
        "missing-check",
        "duplicate-check",
        "traversal",
    ],
)
def test_replay_rejects_tampered_or_missing_evidence(tmp_path: Path, mutation: str) -> None:
    write_policy(tmp_path, [command_rule("check", "print('evidence')")])
    result, path = check(tmp_path)
    data = json.loads(path.read_text())
    artifact = path.parent / result.evidence[0].execution.stdout.path
    if mutation == "output":
        artifact.write_text("changed")
    elif mutation == "missing-output":
        artifact.unlink()
    elif mutation == "verdict":
        data["decision"]["outcome"] = "fail"
    elif mutation == "policy":
        data["policy"]["checks"][0]["required"] = False
    elif mutation == "missing-check":
        data["evidence"] = []
    elif mutation == "duplicate-check":
        data["evidence"] *= 2
    else:
        data["evidence"][0]["execution"]["stdout"]["path"] = "../outside"
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, OSError)):
        replay(path)


def test_replay_rejects_artifact_symlink(tmp_path: Path) -> None:
    write_policy(tmp_path, [command_rule("check", "print('evidence')")])
    result, path = check(tmp_path)
    artifact = path.parent / result.evidence[0].execution.stdout.path
    copy = tmp_path / "copy"
    shutil.copyfile(artifact, copy)
    artifact.unlink()
    artifact.symlink_to(copy)
    with pytest.raises(ValueError, match="symlink"):
        replay(path)


@pytest.mark.parametrize(
    "content,outcome",
    [
        ("# Guide\n\nText.\n", "pass"),
        ("# Guide\n\n[Missing](missing.md)\n", "fail"),
        (None, "not_applicable"),
    ],
)
def test_domain_findings_and_empty_scope_have_explicit_outcomes(
    tmp_path: Path, content: str | None, outcome: str
) -> None:
    if content is not None:
        (tmp_path / "README.md").write_text(content)
    write_policy(tmp_path, [{"id": "docs", "domain": "markdown"}])
    result, path = check(tmp_path)
    assert result.decision.outcome == outcome
    assert replay(path).decision.outcome == outcome


def test_domain_target_can_be_a_single_file(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Guide\n")
    (tmp_path / "bad.md").write_text("# Title\n### Broken\n")
    write_policy(tmp_path, [{"id": "docs", "domain": "markdown", "target": "README.md"}])
    result, _ = check(tmp_path)
    assert result.decision.outcome == "pass"


def test_domain_failure_preserves_missing_coverage_in_replay(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Guide\n\n[Missing](missing.md)\n")
    write_policy(tmp_path, [{"id": "docs", "domain": "markdown"}])
    result, path = check(tmp_path)
    assert result.decision.outcome == "fail"
    assert result.decision.coverage == "full"
    evidence = result.evidence[0]
    artifact = path.parent / evidence.execution.stdout.path
    assessment = json.loads(artifact.read_text())
    assessment["coverage_gaps"].append(
        {
            "validator_id": "live-check",
            "reason": "service unavailable",
            "impact": "live check did not run",
            "dimensions_affected": [],
        }
    )
    artifact.write_text(json.dumps(assessment))
    execution = evidence.execution.model_copy(
        update={"stdout": describe_artifact(artifact, path.parent)}
    )
    result = result.model_copy(
        update={"evidence": [evidence.model_copy(update={"execution": execution})]}
    )
    result = result.model_copy(update={"decision": derive_decision(result, path.parent)})
    result = result.model_copy(update={"decision_hash": decision_hash(result)})
    path.write_text(result.model_dump_json())
    replayed = replay(path)
    assert replayed.decision.outcome == "fail"
    assert replayed.decision.coverage == "incomplete"
    assert replayed.decision.checks[0].coverage == "incomplete"
    assert "coverage is incomplete" in replayed.decision.reasons[0]


def test_policy_rejects_unknown_keys_duplicate_ids_and_unsafe_paths() -> None:
    for text in (
        "version = 1\nchecks = []",
        'version = 1\nunknown = true\n[[checks]]\nid = "x"\ncommand = ["true"]',
        '[[checks]]\nid = "x"\ncommand = ["true"]\n[[checks]]\nid = "x"\ncommand = ["true"]',
        '[[checks]]\nid = "../x"\ncommand = ["true"]',
        '[[checks]]\nid = "x"\ncommand = "true"',
        '[[checks]]\nid = "x"\ncommand = ["true"]\nrequired = "false"',
        '[[checks]]\nid = "x"\ncommand = ["true"]\nsuccess_codes = [0]\nfailure_codes = [0]',
    ):
        with pytest.raises(ValueError):
            parse_policy(text.encode())


def test_cli_outputs_parseable_json_and_replays_failed_exit_code(tmp_path: Path) -> None:
    write_policy(tmp_path, [command_rule("failed", "raise SystemExit(1)")])
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--output", "json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["outcome"] == "fail"
    replayed = runner.invoke(main, ["replay", data["receipt"], "--output", "json"])
    assert replayed.exit_code == 1
    assert json.loads(replayed.stdout)["decision_hash"] == data["decision_hash"]


def test_cli_missing_policy_does_not_pass(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["check", str(tmp_path)])
    assert result.exit_code == 2
    assert "create verdx.toml" in result.output


def test_timing_does_not_enter_decision_hash(tmp_path: Path) -> None:
    write_policy(tmp_path, [command_rule("check", "pass")])
    result, _ = check(tmp_path)
    evidence = result.evidence[0]
    changed = result.model_copy(
        update={
            "run_id": "later",
            "started_at": "later",
            "finished_at": "later",
            "evidence": [
                evidence.model_copy(
                    update={
                        "execution": evidence.execution.model_copy(update={"duration_ms": 9999})
                    }
                )
            ],
        }
    )
    assert decision_hash(changed) == result.decision_hash


@pytest.mark.skipif(os.name != "posix", reason="symlinks require POSIX")
def test_symlink_inputs_and_output_directory_are_refused(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source")
    (tmp_path / "link.txt").symlink_to(tmp_path / "source.txt")
    write_policy(tmp_path, [command_rule("check", "pass")])
    result, _ = check(tmp_path)
    assert result.decision.outcome == "incomplete"
    assert result.evidence[0].execution.state == "not_run"


def test_domain_cannot_validate_files_outside_recorded_scope(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Guide\n")
    write_policy(
        tmp_path, [{"id": "docs", "domain": "markdown"}], settings='include = ["verdx.toml"]'
    )
    result, _ = check(tmp_path)
    assert result.decision.outcome == "incomplete"
    assert "outside the declared input scope" in result.decision.checks[0].reason


def test_interrupt_between_checks_retains_an_incomplete_attempt(tmp_path: Path) -> None:
    write_policy(tmp_path, [command_rule("first", "pass"), command_rule("second", "pass")])

    def interrupt(message: str) -> None:
        raise KeyboardInterrupt

    result, path = check(tmp_path, progress=interrupt)
    assert result.decision.outcome == "incomplete"
    assert [item.execution.state for item in result.evidence] == ["interrupted", "not_run"]
    assert replay(path).decision == result.decision


def test_replay_requires_explicit_schema_version(tmp_path: Path) -> None:
    write_policy(tmp_path, [command_rule("check", "pass")])
    _, path = check(tmp_path)
    data = json.loads(path.read_text())
    del data["schema_version"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema_version"):
        replay(path)


def test_checked_in_example_fails_then_repairs_without_erasing_history(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "examples" / "acceptance"
    target = tmp_path / "sample"
    shutil.copytree(source, target)
    failed, failed_path = check(target)
    assert failed.decision.outcome == "fail"
    subprocess.run([sys.executable, str(target / "repair.py")], check=True)
    passed, _ = check(target)
    assert passed.decision.outcome == "pass"
    assert replay(failed_path).decision.outcome == "fail"
