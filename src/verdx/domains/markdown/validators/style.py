"""Whitespace hygiene checks for Markdown files."""

from __future__ import annotations

from pathlib import Path

from verdx.contracts import iter_target_files
from verdx.domains.markdown.validators._paths import display_rel
from verdx.simple import simple_validator


@simple_validator(id="md-style", version="1.0.0", category="style", file_suffix=".md")
def check_style(target: Path) -> list[str]:
    """Check whitespace hygiene in all Markdown files under target."""
    issues: list[str] = []
    for md in iter_target_files(target, ".md"):
        rel = display_rel(md, target)
        for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            if line != line.rstrip():
                issues.append(f"{rel}:{lineno} trailing whitespace")
            if "\t" in line:
                issues.append(f"{rel}:{lineno} contains a tab; use spaces")
    return issues
