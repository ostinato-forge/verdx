"""Auto-commit and optionally push flywheel data to the verdx-data repo."""

from __future__ import annotations

import logging
from pathlib import Path

from verdx._changed import _run_git

_log = logging.getLogger(__name__)


def _find_data_repo() -> Path | None:
    """Find the verdx-data repo root (only if it is a git repo)."""
    import os

    env = os.environ.get("VERDX_FLYWHEEL_DATA")
    if env:
        p = Path(env)
        if _run_git(["rev-parse", "--git-dir"], p) is not None:
            return p
        return None

    from verdx.engine.flywheel.store import _find_repo_root, _looks_like_data_repo

    repo_root = _find_repo_root()
    if repo_root is not None:
        data_repo = repo_root.parent / "verdx-data"
        if (
            data_repo.is_dir()
            and _looks_like_data_repo(data_repo)
            and _run_git(["rev-parse", "--git-dir"], data_repo) is not None
        ):
            return data_repo

    return None


def sync_flywheel(*, push: bool = False) -> bool:
    """Stage and commit flywheel data changes in the verdx-data repo.

    Returns True if a commit was created, False otherwise.
    Never raises — all errors are logged and swallowed.

    When push=True, pulls with rebase first, then pushes after committing.
    """
    try:
        return _sync(push=push)
    except Exception:
        _log.debug("flywheel sync failed", exc_info=True)
        return False


def _sync(*, push: bool = False) -> bool:
    repo = _find_data_repo()
    if repo is None:
        return False

    committed = False
    status = _run_git(["status", "--porcelain"], repo)
    if status:
        _run_git(["add", "-A"], repo)

        staged = _run_git(["diff", "--cached", "--name-only"], repo)
        if staged:
            from verdx import __version__

            msg = f"chore(flywheel): sync learned patterns\n\nUpdated by verdx {__version__}."

            result = _run_git(["commit", "-m", msg], repo)
            committed = result is not None

    if push:
        _run_git(["pull", "--rebase"], repo)
        push_result = _run_git(["push"], repo)
        if push_result is None:
            _log.warning("flywheel push failed")

    return committed or push
