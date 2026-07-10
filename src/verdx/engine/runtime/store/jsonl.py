"""JSONL reader/writer with advisory locking.

Append-only log for history and disposition storage.
Tolerates trailing malformed lines (crash recovery).
"""

from __future__ import annotations

import fcntl
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


class MalformedLineError(Exception):
    """Raised when a non-trailing line is malformed."""

    def __init__(self, line_number: int, content: str) -> None:
        self.line_number = line_number
        self.content = content
        truncated = content[:100] + "..." if len(content) > 100 else content
        super().__init__(f"Malformed JSON at line {line_number}: {truncated!r}")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, tolerating only trailing malformed line.

    Returns list of parsed JSON objects.
    Raises MalformedLineError for non-trailing malformed lines.
    """
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []

    lines = text.rstrip("\n").split("\n")
    records: list[dict[str, Any]] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            is_last = i == len(lines) - 1
            if is_last:
                break
            raise MalformedLineError(i + 1, stripped) from None

    return records


def append_jsonl(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    """Append records to a JSONL file with advisory locking.

    1. Acquire exclusive lock
    2. Append one JSON object per line
    3. Flush and fsync
    4. Release lock
    """
    if not records:
        return

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            for record in records:
                line = json.dumps(record, separators=(",", ":"), sort_keys=True)
                f.write(line + "\n")
            f.flush()
            import os

            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
