"""example-gate validators: the smallest possible domain validator.

Copy this file to start writing your own gate validator. A validator is a
function that:
  1. Receives the target directory as a Path.
  2. Walks the files it cares about.
  3. Returns a list of human-readable problem strings (one per finding).

The @simple_validator decorator wraps the function into the full protocol the
engine expects (CheckSpec, PythonAdapter, category, version, etc.) without you
touching any engine internals. The module-level name after decoration IS the
wrapper, so ``check_trailing_blank_lines.fn(path)`` calls the raw logic and
``check_trailing_blank_lines(check_context)`` is what the engine calls at
runtime.

Key rule: validators are PURE. They read, decide, and return messages. They
never write, delete, or mutate anything. All mutation belongs in a fixer (see
fixers.py). This separation lets the engine run validators in any order, in
parallel, or in dry-run mode without risk.
"""

from __future__ import annotations

# Path is the only type gate code needs for file operations.
from pathlib import Path

# iter_target_files is the safe way to walk a target: it handles a single-FILE
# target (not just a directory) and skips symlinks that could escape the root.
from verdx.contracts import iter_target_files

# simple_validator is the public decorator for gate authors. It lives in
# verdx.simple, not in the engine internals.
from verdx.simple import simple_validator


# ---------------------------------------------------------------------------
# Validator: trailing blank lines in .txt files
# ---------------------------------------------------------------------------
# The decorator is applied IN PLACE: the module-level name (check_trailing_blank_lines)
# becomes the wrapper, not the raw function. The wrapper:
#   - adopts __module__ and __qualname__ from the wrapped function, so the
#     engine can resolve the import path for the PythonAdapter.
#   - exposes the raw function as .fn, useful in unit tests and ad-hoc scripts.
#   - is callable as a validator (engine path) OR via .fn (test path).
#
# Parameters to @simple_validator:
#   id       - stable, unique identifier for this check across all gates.
#              Use the format "<gate>-<what>" to avoid collisions.
#   version  - semver string; bump when the check logic changes in a way that
#              could change verdicts on the same input.
#   category - groups related checks. The escalation policy in __init__.py
#              references this name for floor/rule matching.
@simple_validator(id="example-trailing-blank", version="1.0.0", category="example")
def check_trailing_blank_lines(target: Path) -> list[str]:
    """Flag .txt files whose last line is blank.

    Trailing blank lines are a common hygiene issue in plain-text files: they
    make diffs noisier and can confuse tools that process the last line. This
    check is intentionally tiny so you can read the whole validator in one
    screen and understand what every line does.

    Returns a list of problem strings, one per affected file. An empty list
    means the target is clean for this check.
    """
    # Collect problem messages. Start empty; add one per affected file.
    issues: list[str] = []

    # iter_target_files walks all .txt files under target (or yields target
    # itself when target is a single .txt file), in sorted order for
    # deterministic output, and skips symlinks that could escape the root.
    for txt in iter_target_files(target, ".txt"):
        # Read the whole file and split into lines. splitlines() strips the
        # trailing newline from each line, so "content\n\n" → ["content", ""].
        lines = txt.read_text(encoding="utf-8").splitlines()

        # An empty file has no lines, so there is nothing to flag.
        if not lines:
            continue

        # The last line is blank if it contains only whitespace (or nothing).
        # strip() handles spaces, tabs, and carriage returns uniformly.
        if lines[-1].strip() == "":
            # Use a path the paired fixer can reconstruct with resolve_within:
            # for a single-file target that is the file's name; for a directory
            # target it is the path relative to the target root.
            rel = txt.name if target.is_file() else str(txt.relative_to(target))
            issues.append(f"{rel} ends with a trailing blank line")

    # Return the full list. The engine wraps each string in an Observation;
    # the fixer reads observation.message to find the file path to repair.
    return issues
