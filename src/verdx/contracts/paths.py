"""Path-safety helpers for gate authors.

A gate validator receives a ``target`` that may be a directory OR a single
file, and a fixer reconstructs a file path from an observation message. Both
operations need to stay inside the target and must not follow symlinks out of
it. These helpers put the safe pattern on the gate-author surface so every
gate (and the copy-me example) shares one correct implementation instead of
re-deriving it.

Import from ``verdx.contracts``::

    from verdx.contracts import iter_target_files, resolve_within
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def iter_target_files(target: Path, suffix: str) -> Iterator[Path]:
    """Yield files under ``target`` whose name ends with ``suffix``.

    Handles both shapes of target:

    - If ``target`` is a file whose name ends with ``suffix``, yield just that
      file. This is what makes single-file targets actually get checked
      instead of silently passing. An explicitly named file target is validated
      even when it is a symlink: the caller chose that path, skipping it would
      silently pass, and reading is safe. (The paired ``resolve_within`` still
      refuses to WRITE through a symlinked target, so a fixer cannot follow it
      out of bounds.)
    - If ``target`` is a directory, walk it in sorted order (for deterministic
      output) yielding matching regular files, SKIPPING symlinked files and not
      descending into symlinked directories. A symlink discovered mid-walk was
      not named by the caller and could point outside the target, so following
      it would let a check read (or a fixer later write) out of bounds.

    Anything else (missing path, non-matching file) yields nothing.
    """
    if target.is_file():
        if target.name.endswith(suffix):
            yield target
        return

    if not target.is_dir():
        return

    yield from _walk_dir(target, suffix)


def _walk_dir(directory: Path, suffix: str) -> Iterator[Path]:
    """Recursively yield matching non-symlink files, not descending symlinked dirs."""
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink():
            # Skip symlinked files and do not descend into symlinked dirs:
            # either could escape the target root.
            continue
        if entry.is_dir():
            yield from _walk_dir(entry, suffix)
        elif entry.is_file() and entry.name.endswith(suffix):
            yield entry


def resolve_within(target: Path, rel: str) -> Path | None:
    """Resolve ``target / rel`` only if it stays inside ``target`` and is not a symlink.

    A fixer reconstructs the file to repair from the observation message. A
    crafted message like ``../evil.md`` would otherwise let the fixer write
    outside the target. This returns the resolved path only when it is safe:

    - the resolved path is inside ``target`` (after resolving both), and
    - the path is not itself a symlink (which could redirect the write out).

    Returns ``None`` when the reconstructed path escapes the target or is a
    symlink; the caller should then skip the fix rather than write blindly.

    When ``target`` is itself a single file (a single-file validation target),
    the only accepted ``rel`` is that file's own name, which resolves to the
    file itself; anything else is rejected.
    """
    target_resolved = target.resolve()

    if target.is_file():
        # Single-file target: the message names the file itself. Accept only
        # its own name (or "."), never a path that would step elsewhere.
        if rel in (target.name, ".") and not target.is_symlink():
            return target_resolved
        return None

    candidate = (target / rel).resolve()

    if not candidate.is_relative_to(target_resolved):
        return None

    # Refuse if the reconstructed path's final component is a symlink: even a
    # symlink whose resolved target is inside the root could later be swapped,
    # so writing through it is unsafe.
    if (target / rel).is_symlink():
        return None

    return candidate
