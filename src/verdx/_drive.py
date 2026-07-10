"""Drive loop: verdx owns the loop and the verdict; an external fix command is the fixer.

This inverts the usual, unreliable flow where a human asks a fix command to remember to
run verdx. Here verdx runs its own validation (including live checks against the lab), and
while the verdict is ``block`` it invokes the external fix command to repair the findings,
then re-validates. The loop is the single authority on "done": it exits green only when
verdx's own verdict is ``pass``. The fix command is a subroutine that repairs; it never
decides completion, so the QA's reliability no longer inherits the fix command's.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from verdx.types import Assessment, DriveResult

# Given the target dir, the current (blocking) assessment, and the 1-based iteration,
# make the target pass verdx. Raises on invocation failure.
FixFn = Callable[[Path, Assessment, int], None]

_FIX_TIMEOUT_S = 1800


def default_fix_fn(fix_cmd: str) -> FixFn:
    """Build a fixer that shells the external fix command, passing the prompt on stdin.

    The command runs in the target directory and receives the fix prompt on stdin, so a
    large finding set never hits ``ARG_MAX`` and its contents do not leak through process
    listings. A non-zero exit, a missing executable, or a timeout all surface to the loop
    as a terminal error rather than a false pass.
    """
    parts = shlex.split(fix_cmd)
    if not parts:
        raise ValueError("fix_cmd must not be empty")

    def _run(target: Path, assessment: Assessment, iteration: int) -> None:
        prompt = build_prompt(target, assessment, iteration)
        subprocess.run(
            parts,
            cwd=str(target),
            check=True,
            timeout=_FIX_TIMEOUT_S,
            input=prompt,
            text=True,
        )

    return _run


def build_prompt(target: Path, assessment: Assessment, iteration: int) -> str:
    """Render verdx's blocking findings into a fix instruction for the fix command."""
    header = [
        "You are fixing a project so it passes verdx's validation.",
        f"Working directory: {target}",
        f"Attempt {iteration}. verdx's verdict is BLOCK (score {assessment.score}).",
        "",
        "Make the minimal, correct change for each finding below in the project source.",
        "Do NOT declare the work done and do NOT run verdx yourself: verdx re-validates after",
        "you finish and is the only thing that decides whether this passes.",
        "",
        "Findings:",
    ]
    findings = [
        f"- [{obs.severity}] {obs.category}: {obs.message}"
        + (f" ({obs.location})" if obs.location else "")
        for obs in assessment.observations
        if obs.disposition is None
    ]
    return "\n".join(header + (findings or ["- (no itemized findings; see verdx output)"]))


def _observed(assessment: Assessment) -> set[tuple[str, str, str]]:
    """Content fingerprint of an assessment's findings, for no-progress detection."""
    return {(o.signature, o.severity, o.message) for o in assessment.observations}


def run_drive_loop(
    *,
    target: Path,
    validate_fn: Callable[[], Assessment],
    fix_fn: FixFn,
    max_iterations: int = 5,
) -> DriveResult:
    """Validate, then while blocked let the fix command repair and re-validate, until pass.

    ``validate_fn`` is verdx's own (live) validation; ``fix_fn`` is invoked only when the
    verdict is ``block``. Terminates on: pass, exhausted iterations, fix-command error, or
    no-progress (a re-validation with identical findings), never on the fix command's say-so.
    """
    before = validate_fn()
    current = before
    invocations = 0

    if current.verdict == "pass":
        return DriveResult(before, current, 0, 0, True, "passed")

    for i in range(1, max_iterations + 1):
        # Count the attempt before running it, so a timeout or crash is still reported
        # as an invocation that happened rather than one that never launched.
        invocations += 1
        try:
            fix_fn(target, current, i)
        except subprocess.TimeoutExpired:
            return DriveResult(before, current, i, invocations, False, "fix_timeout")
        except (subprocess.CalledProcessError, OSError):
            return DriveResult(before, current, i, invocations, False, "fix_error")

        previous = current
        current = validate_fn()

        if current.verdict == "pass":
            return DriveResult(before, current, i, invocations, True, "passed")

        # Stop only when the fix command changed nothing verdx can see. Compare content, not
        # just signatures: a genuinely different failure at the same rule and location (a
        # missing file becoming a wrong file) must count as progress and keep the loop going.
        if _observed(current) == _observed(previous):
            return DriveResult(before, current, i, invocations, False, "no_progress")

    return DriveResult(before, current, max_iterations, invocations, False, "max_iterations")
