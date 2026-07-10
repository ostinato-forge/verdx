"""Fast-path eligibility classifier — pure function, no I/O.

Mechanically proves whether a change qualifies for reduced ceremony
(skip Spec and Approval). If the classifier cannot prove eligibility,
the change enters the normal seven-stage cycle.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ChangeDescription:
    files_changed: tuple[str, ...]
    lines_modified: int
    change_types: frozenset[str]
    diff_digest: str
    base_commit: str


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]
    denylist_hits: tuple[str, ...]
    unchecked: tuple[str, ...]


ELIGIBLE_CHANGE_TYPES = frozenset({"text", "comment", "metadata", "formatting"})

MAX_LINES = 10

DEFAULT_DENYLIST: tuple[str, ...] = (
    ".github/workflows/**",
    ".gitlab-ci.yml",
    ".circleci/**",
    "Dockerfile*",
    "docker-compose*",
    "Makefile",
    "CMakeLists.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "*.lock",
    "requirements*.txt",
    "Pipfile*",
    "package.json",
    "go.mod",
    "go.sum",
    ".agentic/policy.yml",
    "**/migrations/**",
    "*.schema.*",
)

_UNCHECKED_CRITERIA: tuple[str, ...] = (
    "Tests required by policy for this file type and change class",
    "Cross-file references or generated outputs affected by the change",
    "Domain-specific semantics (requires .agentic/policy.yml)",
)


def _matches_pattern(path: str, pattern: str) -> bool:
    p = PurePosixPath(path)
    if p.match(pattern):
        return True
    # PurePosixPath.match() doesn't match root-level paths against **/X patterns
    if pattern.startswith("**/"):
        return p.match(pattern[3:])
    return False


def classify(
    change: ChangeDescription,
    extra_denylist: tuple[str, ...] = (),
) -> EligibilityResult:
    reasons: list[str] = []
    denylist_hits: list[str] = []

    n_files = len(change.files_changed)
    if n_files == 0:
        reasons.append("No files changed")
    elif n_files > 1:
        reasons.append(f"{n_files} files changed — fast-path requires single file")

    if change.lines_modified < 1:
        reasons.append("No content lines modified")
    elif change.lines_modified > MAX_LINES:
        reasons.append(f"{change.lines_modified} lines modified — fast-path limit is {MAX_LINES}")

    unsafe = change.change_types - ELIGIBLE_CHANGE_TYPES
    if unsafe:
        reasons.append(
            f"Unsafe change types: {', '.join(sorted(unsafe))} "
            f"— eligible types: {', '.join(sorted(ELIGIBLE_CHANGE_TYPES))}"
        )

    all_patterns = DEFAULT_DENYLIST + extra_denylist
    for file_path in change.files_changed:
        for pattern in all_patterns:
            if _matches_pattern(file_path, pattern):
                denylist_hits.append(f"{file_path} matches {pattern}")

    if denylist_hits:
        reasons.append(f"Protected path: {denylist_hits[0]}")

    eligible = len(reasons) == 0
    if eligible:
        reasons.append(
            f"Single file, {change.lines_modified} line(s) modified, "
            f"{', '.join(sorted(change.change_types))}-only change, no denylist matches"
        )

    return EligibilityResult(
        eligible=eligible,
        reasons=tuple(reasons),
        denylist_hits=tuple(denylist_hits),
        unchecked=_UNCHECKED_CRITERIA,
    )
