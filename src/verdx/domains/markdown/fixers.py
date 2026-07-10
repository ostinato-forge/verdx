"""Deterministic fixers for the markdown gate."""

from __future__ import annotations

from pathlib import Path

from verdx.contracts import resolve_within
from verdx.fixers import FixerSpec
from verdx.simple import simple_fixer
from verdx.types import Observation


@simple_fixer(
    id="fix-md-trailing-whitespace",
    categories={"style"},
    message_contains="trailing whitespace",
)
def fix_trailing_whitespace(target: Path, observation: Observation) -> str | None:
    """Strip trailing whitespace from the file referenced in the observation message.

    Returns a description string on success, None if the observation does not apply.
    """
    msg = observation.message
    if "trailing whitespace" not in msg:
        return None
    # message format: "path/to.md:12 trailing whitespace"
    rel = msg.split(":", 1)[0]
    # resolve_within refuses paths that escape the target or point through a
    # symlink, so a crafted message like "../evil.md:1 ..." cannot make the
    # fixer write outside the target.
    f = resolve_within(target, rel)
    if f is None or not f.is_file():
        return None
    lines = f.read_text(encoding="utf-8").splitlines()
    stripped = [ln.rstrip() for ln in lines]
    if stripped == lines:
        return None
    f.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    return f"Stripped trailing whitespace in {rel}"


def get_fixers() -> tuple[FixerSpec, ...]:
    """Return all FixerSpec objects for the markdown gate."""
    return (fix_trailing_whitespace.as_spec("Strip trailing whitespace from Markdown files"),)
