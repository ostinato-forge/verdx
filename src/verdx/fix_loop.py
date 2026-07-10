"""Bounded fix loop: validate, fix, re-validate until clean or stuck."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from verdx.fixers import FixerSpec, find_fixer
from verdx.types import Assessment, FixAction, FixResult, Observation

_log = logging.getLogger(__name__)

MAX_ITERATIONS = 5


def run_fix_loop(
    *,
    target: Path,
    validate_fn: object,
    fixers: list[FixerSpec],
    max_iterations: int = MAX_ITERATIONS,
    dry_run: bool = False,
) -> FixResult:
    """Run a bounded, per-fix transactional repair loop.

    Each candidate fix is applied against a fresh snapshot and kept only if it
    raises the score; otherwise its changes are rolled back on disk. A fixer
    that raises, or that mutates files and returns None, is rolled back too, so
    the loop never returns, or leaves behind, an assessment worse than the one
    it started with, and never keeps an unrecorded change. ``termination_reason``
    on the result records why the loop stopped.

    Rollback covers the target tree only. A fixer that writes outside the target
    (through a symlink that escapes it, or an absolute path) is out of contract
    and cannot be undone, the same as any process writing elsewhere on disk.
    """
    from collections.abc import Callable

    validate: Callable[..., Assessment] = validate_fn  # type: ignore[assignment]

    before = validate(target)
    best = before
    all_fixes: list[FixAction] = []
    iterations = 0
    termination_reason = "max_iterations"

    for _ in range(max_iterations):
        iterations += 1
        fixable = _find_fixable(best.observations, fixers)
        if not fixable:
            termination_reason = "clean" if not best.observations else "no_fixable"
            break

        if dry_run:
            for obs, spec in fixable:
                snapshot = _snapshot_target(target)
                try:
                    action = spec.fixer.fix(target, obs)
                except Exception:
                    action = None
                finally:
                    _restore_target(target, snapshot)
                    _discard_snapshot(snapshot)
                if action is not None:
                    all_fixes.append(
                        FixAction(
                            observation_signature=obs.signature,
                            fixer_id=spec.fixer_id,
                            description=f"[dry-run] would fix: {action.description}",
                            files_modified=action.files_modified,
                        )
                    )
            termination_reason = "dry_run"
            break

        progressed = False
        attempted = False
        stopped = False
        live_sigs = {o.signature for o in best.observations}
        for obs, spec in fixable:
            if obs.signature not in live_sigs:
                # An earlier kept fix this pass already resolved this finding;
                # do not run its now-stale fixer.
                continue

            snapshot = _snapshot_target(target)
            kept = False
            try:
                try:
                    action = spec.fixer.fix(target, obs)
                except Exception:
                    _log.warning(
                        "fixer %s raised on %s; skipping",
                        spec.fixer_id,
                        obs.signature,
                        exc_info=True,
                    )
                    action = None
                if action is not None:
                    attempted = True
                    try:
                        candidate = validate(target)
                    except Exception:
                        # A validation failure is infrastructure, not a fix
                        # result. Stop gracefully and keep the best so far rather
                        # than aborting and losing the fixes already applied.
                        _log.warning("validate raised during fix loop; stopping", exc_info=True)
                        termination_reason = "validation_error"
                        stopped = True
                    else:
                        if candidate.score > best.score:
                            best = candidate
                            all_fixes.append(action)
                            progressed = True
                            kept = True
                            live_sigs = {o.signature for o in best.observations}
            finally:
                if not kept:
                    _restore_target(target, snapshot)
                _discard_snapshot(snapshot)

            if stopped:
                break

        if stopped:
            break
        if not progressed:
            termination_reason = "no_improvement" if attempted else "no_fixes_applied"
            break

    # If the loop ran out of iterations but the target is actually clean now,
    # report that rather than the default "max_iterations".
    if termination_reason == "max_iterations" and not _find_fixable(best.observations, fixers):
        termination_reason = "clean" if not best.observations else "no_fixable"

    return FixResult(
        assessment_before=before,
        assessment_after=best,
        fixes_applied=tuple(all_fixes),
        iterations=max(iterations, 1),
        remaining_observations=best.observations,
        termination_reason=termination_reason,
    )


def _find_fixable(
    observations: tuple[Observation, ...],
    fixers: list[FixerSpec],
) -> list[tuple[Observation, FixerSpec]]:
    """Match observations to fixers, skipping findings a human dispositioned."""
    result: list[tuple[Observation, FixerSpec]] = []
    for obs in observations:
        if obs.disposition is not None:
            continue
        spec = find_fixer(obs, fixers)
        if spec is not None:
            result.append((obs, spec))
    return result


def _snapshot_target(target: Path) -> Path:
    """Copy the target into a temp directory so a candidate fix can be undone.

    Preserves symlinks rather than following them, tolerates the target being a
    file, symlink, directory, or absent, and cleans up its temp dir if the copy
    fails partway.
    """
    snap_root = Path(tempfile.mkdtemp(prefix="verdx-fix-snap-"))
    try:
        _materialize(target, snap_root / "tree")
    except BaseException:
        shutil.rmtree(snap_root, ignore_errors=True)
        raise
    return snap_root


def _restore_target(target: Path, snap_root: Path) -> None:
    """Restore the target from a snapshot, undoing any change since it was taken.

    Non-destructive: the current target is moved aside, the snapshot is
    recreated, and the moved-aside copy is removed only once restore succeeds.
    If restore fails partway, the original is put back so the target is never
    lost. Recreates a parent directory a fixer may have deleted.

    Directories in _SNAPSHOT_IGNORE (e.g. .git) are not part of the snapshot;
    they are preserved in-place during rollback so version control state
    survives the fix loop.
    """
    snap = snap_root / "tree"
    target.parent.mkdir(parents=True, exist_ok=True)

    backup: Path | None = None
    if target.is_symlink() or target.exists():
        backup = target.parent / f".{target.name}.verdx-rollback-{snap_root.name}"
        _remove_path(backup)
        os.rename(target, backup)

    try:
        if snap.is_symlink() or snap.exists():
            _materialize(snap, target)
        elif target.is_dir() is False:
            target.mkdir(parents=True, exist_ok=True)
        if backup is not None and backup.is_dir() and target.is_dir():
            for name in _SNAPSHOT_IGNORE:
                src_path = backup / name
                dst_path = target / name
                if src_path.exists() and not dst_path.exists():
                    os.rename(src_path, dst_path)
    except BaseException:
        _remove_path(target)
        if backup is not None:
            os.rename(backup, target)
        raise
    else:
        if backup is not None:
            _remove_path(backup)


_SNAPSHOT_IGNORE = frozenset({".git", ".venv", "__pycache__", ".cache", ".mypy_cache"})


def _materialize(src: Path, dest: Path) -> None:
    """Recreate src at dest as a file, symlink, or directory tree."""
    if src.is_symlink():
        os.symlink(os.readlink(src), dest)
    elif src.is_dir():
        shutil.copytree(
            src,
            dest,
            symlinks=True,
            ignore=shutil.ignore_patterns(*_SNAPSHOT_IGNORE),
        )
    elif src.exists():
        shutil.copy2(src, dest, follow_symlinks=False)


def _remove_path(path: Path) -> None:
    """Remove a file, symlink, or directory tree if it exists."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _discard_snapshot(snap_root: Path) -> None:
    """Delete a snapshot directory."""
    shutil.rmtree(snap_root, ignore_errors=True)
