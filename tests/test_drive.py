"""Tests for the drive loop (verdx owns the loop; the fix command is the fixer)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from verdx._drive import build_prompt, default_fix_fn, run_drive_loop
from verdx.types import Assessment, Observation


def _obs(sig: str, msg: str = "problem") -> Observation:
    return Observation(
        severity="high",
        category="test",
        location=None,
        message=msg,
        fixable=False,
        signature=sig,
    )


def _assess(verdict: str, obs: tuple[Observation, ...] = ()) -> Assessment:
    return Assessment(
        score=100 if verdict == "pass" else 50,
        verdict=verdict,
        observations=obs,
        decision_hash="hash",
        overrides_applied=(),
        validators_run=(),
        metadata={},
    )


def _noop_fix(_t: Path, _a: Assessment, _i: int) -> None:
    return None


def test_passes_immediately_without_invoking_fix_command() -> None:
    calls: list[int] = []

    def fix(_t: Path, _a: Assessment, i: int) -> None:
        calls.append(i)

    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: _assess("pass"),
        fix_fn=fix,
        max_iterations=5,
    )
    assert result.converged
    assert result.iterations == 0
    assert result.fix_invocations == 0
    assert result.termination_reason == "passed"
    assert calls == []


def test_converges_after_one_fix() -> None:
    seq = iter([_assess("block", (_obs("a"),)), _assess("pass")])
    calls: list[int] = []

    def fix(_t: Path, _a: Assessment, i: int) -> None:
        calls.append(i)

    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: next(seq),
        fix_fn=fix,
        max_iterations=5,
    )
    assert result.converged
    assert result.iterations == 1
    assert result.fix_invocations == 1
    assert result.termination_reason == "passed"
    assert calls == [1]


def test_stops_on_no_progress() -> None:
    # The fix command runs but verdx reports the identical finding set: stop, do not pass.
    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: _assess("block", (_obs("a"),)),
        fix_fn=_noop_fix,
        max_iterations=5,
    )
    assert not result.converged
    assert result.termination_reason == "no_progress"
    assert result.iterations == 1


def test_exhausts_max_iterations_when_never_passing() -> None:
    counter = {"n": 0}

    def validate_fn() -> Assessment:
        counter["n"] += 1
        return _assess("block", (_obs(f"sig{counter['n']}"),))

    result = run_drive_loop(
        target=Path("."),
        validate_fn=validate_fn,
        fix_fn=_noop_fix,
        max_iterations=3,
    )
    assert not result.converged
    assert result.termination_reason == "max_iterations"
    assert result.iterations == 3
    assert result.fix_invocations == 3


def test_fix_error_is_terminal_not_a_pass() -> None:
    def fix(_t: Path, _a: Assessment, _i: int) -> None:
        raise subprocess.CalledProcessError(1, "fixer")

    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: _assess("block", (_obs("a"),)),
        fix_fn=fix,
        max_iterations=5,
    )
    assert not result.converged
    assert result.termination_reason == "fix_error"
    assert result.fix_invocations == 1


def test_missing_fix_binary_is_fix_error() -> None:
    def fix(_t: Path, _a: Assessment, _i: int) -> None:
        raise FileNotFoundError(2, "No such file or directory", "fixer")

    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: _assess("block", (_obs("a"),)),
        fix_fn=fix,
        max_iterations=5,
    )
    assert not result.converged
    assert result.termination_reason == "fix_error"
    assert result.fix_invocations == 1


def test_timeout_counts_the_invocation() -> None:
    def fix(_t: Path, _a: Assessment, _i: int) -> None:
        raise subprocess.TimeoutExpired("fixer", 1)

    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: _assess("block", (_obs("a"),)),
        fix_fn=fix,
        max_iterations=5,
    )
    assert not result.converged
    assert result.termination_reason == "fix_timeout"
    assert result.fix_invocations == 1


def test_changed_finding_at_same_signature_counts_as_progress() -> None:
    # Same signature, different message: the fix command changed the failure, so the loop
    # must keep going rather than stop as no-progress. With no pass reachable, it exhausts.
    seq = iter(
        [
            _assess("block", (_obs("a", "missing file"),)),
            _assess("block", (_obs("a", "wrong file contents"),)),
            _assess("block", (_obs("a", "still wrong"),)),
        ]
    )
    result = run_drive_loop(
        target=Path("."),
        validate_fn=lambda: next(seq),
        fix_fn=_noop_fix,
        max_iterations=2,
    )
    assert not result.converged
    assert result.termination_reason == "max_iterations"


def test_empty_fix_cmd_is_rejected() -> None:
    with pytest.raises(ValueError):
        default_fix_fn("")


def test_build_prompt_lists_findings_and_forbids_self_completion() -> None:
    prompt = build_prompt(Path("/repo"), _assess("block", (_obs("a", "broken link"),)), 2)
    assert "broken link" in prompt
    assert "Attempt 2" in prompt
    assert "verdx re-validates" in prompt
