"""Changed-file detection via git merge-base."""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT = 30


def _run_git(args: list[str], cwd: Path) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip()


def _default_branch(cwd: Path) -> str:
    """Detect the default branch (origin/HEAD → fallback to main)."""
    ref = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd)
    if ref:
        return ref.removeprefix("refs/remotes/origin/")
    return "main"


def get_changed_files(target: Path) -> frozenset[str] | None:
    """Return files changed since the merge-base with the default branch.

    Returns None if the target is not inside a git repo (meaning: validate
    everything, don't scope). Includes staged and untracked files.
    """
    toplevel = _run_git(["rev-parse", "--show-toplevel"], target)
    if toplevel is None:
        return None

    repo_root = Path(toplevel)
    default = _default_branch(repo_root)

    merge_base = _run_git(["merge-base", "HEAD", f"origin/{default}"], repo_root)
    if merge_base is None:
        merge_base = _run_git(["merge-base", "HEAD", default], repo_root)
    if merge_base is None:
        return None

    changed: set[str] = set()

    diff_output = _run_git(["diff", "--name-only", merge_base, "HEAD"], repo_root)
    if diff_output:
        changed.update(diff_output.splitlines())

    staged_output = _run_git(["diff", "--name-only", "--cached"], repo_root)
    if staged_output:
        changed.update(staged_output.splitlines())

    untracked_output = _run_git(["ls-files", "--others", "--exclude-standard"], repo_root)
    if untracked_output:
        changed.update(untracked_output.splitlines())

    return frozenset(changed)
