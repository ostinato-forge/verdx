"""example-gate fixer: the smallest possible deterministic repair.

Copy this file to add fixers to your own gate. A fixer is a function that:
  1. Receives the target directory (Path) and ONE observation (Observation).
  2. Decides whether the observation is one it knows how to fix.
  3. If yes: makes a safe, idempotent change and returns a one-line description.
  4. If no: returns None without touching anything.

Fixers are called one observation at a time by the fix loop. They must be:
  - Deterministic: same observation + same files -> same result every time.
  - Idempotent: running the fixer twice should leave the file unchanged the
    second time (or produce the same result).
  - Scoped: fix ONLY the file referenced in the observation message. Do not
    walk the target looking for other problems; that is the validator's job.

The @simple_fixer decorator is the paired counterpart to @simple_validator.
It wraps the function into a SimpleFixerWrapper that exposes .fn (raw logic)
and .as_spec(description) (produces a FixerSpec for the entry-point registry).
"""

from __future__ import annotations

from pathlib import Path

# resolve_within reconstructs the file to repair from the observation message
# WITHOUT letting a crafted path escape the target root or point through a
# symlink. A fixer must never write outside the target it was given.
from verdx.contracts import resolve_within

# FixerSpec is what the engine's fixer registry stores. get_fixers() returns
# these so the engine knows which fixers this gate provides.
from verdx.fixers import FixerSpec

# simple_fixer is the public decorator for gate authors.
from verdx.simple import simple_fixer

# Observation is the engine's finding type; fixer receives one per call.
# Read observation.message to understand what the validator found.
from verdx.types import Observation


# ---------------------------------------------------------------------------
# Fixer: remove trailing blank lines from .txt files
# ---------------------------------------------------------------------------
# Parameters to @simple_fixer:
#   id               - stable, unique identifier for this fixer.
#   categories       - set of category strings this fixer handles. The engine
#                      uses this to route observations: only observations whose
#                      check has a matching category are offered to this fixer.
#   message_contains - optional fast pre-filter: the engine skips this fixer
#                      entirely if the observation message does not contain this
#                      string. Saves calling the function for unrelated findings.
@simple_fixer(
    id="example-fix-trailing-blank",
    categories={"example"},
    message_contains="trailing blank line",
)
def fix_trailing_blank_lines(target: Path, observation: Observation) -> str | None:
    """Remove trailing blank lines from the .txt file named in the observation.

    Returns a one-line description string on success, None if the observation
    does not apply to this fixer (safety guard; the engine's pre-filter should
    prevent most non-matching calls, but the function checks explicitly too).
    """
    # Guard: only handle observations about trailing blank lines.
    # This check is redundant with message_contains= above but makes the fixer
    # safe if called directly (e.g., in tests via .fn).
    if "trailing blank line" not in observation.message:
        return None

    # The validator wrote messages like: "a.txt ends with a trailing blank line"
    # Split on the first space to extract the relative path token.
    # If the message format changes in the validator, update this parser too.
    rel = observation.message.split(" ", 1)[0]

    # Reconstruct the file via resolve_within so a crafted path (e.g.
    # "../evil.txt ...") cannot make the fixer write outside the target. It
    # returns None when the path escapes the target or points through a symlink.
    f = resolve_within(target, rel)

    # If the file is unsafe or does not exist (e.g., was deleted between
    # validate and fix), return None rather than raising. Fixers must never
    # crash the fix loop, and must never write out of bounds.
    if f is None or not f.is_file():
        return None

    # Read the file, strip ALL trailing newlines, then add exactly one back.
    # rstrip("\n") removes "\n", "\n\n", "\r\n\r\n", etc. in one call.
    # Adding "\n" back gives a properly terminated last line (POSIX convention).
    text = f.read_text(encoding="utf-8").rstrip("\n") + "\n"
    f.write_text(text, encoding="utf-8")

    # Return a human-readable description of what was done. The fix loop logs
    # these so the user can see what changed.
    return f"Removed trailing blank lines in {rel}"


def get_fixers() -> tuple[FixerSpec, ...]:
    """Return all FixerSpec objects for the example gate.

    This is the function the ``verdx.fixers`` entry-point group calls.
    as_spec(description) converts the SimpleFixerWrapper into a FixerSpec
    with a human-readable description shown in CLI output.
    """
    return (fix_trailing_blank_lines.as_spec("Remove trailing blank lines from .txt files"),)
