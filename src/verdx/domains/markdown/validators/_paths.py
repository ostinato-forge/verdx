"""Shared path-display helper for the markdown validators.

The message a validator emits must name the file in a form the paired fixer can
reconstruct with ``resolve_within``. For a directory target that is the path
relative to the target root; for a single-file target it is just the file's
name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def display_rel(file: Path, target: Path) -> str:
    """Return the message path for ``file`` under ``target``.

    Directory target -> path relative to the target. Single-file target
    (``file == target``) -> the file's own name, which ``resolve_within`` maps
    back to the file.
    """
    if target.is_file():
        return file.name
    return str(file.relative_to(target))
