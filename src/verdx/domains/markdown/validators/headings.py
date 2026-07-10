"""Heading hygiene checks for Markdown files."""

from __future__ import annotations

import re
from pathlib import Path

from verdx.contracts import iter_target_files
from verdx.domains.markdown.validators._paths import display_rel
from verdx.simple import simple_validator

_HEADING = re.compile(r"^(#{1,6})\s+\S")


@simple_validator(id="md-headings", version="1.0.0", category="structure")
def check_headings(target: Path) -> list[str]:
    """Check heading hierarchy hygiene in all Markdown files under target."""
    issues: list[str] = []
    for md in iter_target_files(target, ".md"):
        prev = 0
        h1_count = 0
        for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            m = _HEADING.match(line)
            if not m:
                continue
            level = len(m.group(1))
            if level == 1:
                h1_count += 1
            if prev and level > prev + 1:
                rel = display_rel(md, target)
                issues.append(f"{rel}:{lineno} heading jumps from h{prev} to h{level}")
            prev = level
        if h1_count > 1:
            rel = display_rel(md, target)
            issues.append(f"{rel} has {h1_count} level-1 headings; expected exactly one")
    return issues
