"""Internal link resolution checks for Markdown files."""

from __future__ import annotations

import re
from pathlib import Path

from verdx.contracts import iter_target_files
from verdx.domains.markdown.validators._paths import display_rel
from verdx.simple import simple_validator

_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@simple_validator(id="md-links", version="1.0.0", category="links")
def check_links(target: Path) -> list[str]:
    """Check that relative links resolve and stay inside the target root."""
    issues: list[str] = []
    # A single-file target has no directory root to bound links against; use
    # the file's own directory in that case.
    root = (target.parent if target.is_file() else target).resolve()
    for md in iter_target_files(target, ".md"):
        for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for dest in _LINK.findall(line):
                dest = dest.split()[0].strip()  # drop optional "title"
                if dest.startswith(("http://", "https://", "mailto:", "#")):
                    continue  # external and same-page anchors are out of scope
                path_part = dest.split("#", 1)[0]
                if not path_part:
                    continue
                resolved = (md.parent / path_part).resolve()
                rel = display_rel(md, target)
                if not resolved.is_relative_to(root):
                    issues.append(f"{rel}:{lineno} link escapes target root: {dest}")
                elif not resolved.exists():
                    issues.append(f"{rel}:{lineno} broken link to {dest}")
    return issues
