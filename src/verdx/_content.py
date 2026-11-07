"""Portable content manifests with full file digests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

CONTENT_SCHEMA = "verdx.content.v2"
GENERATED_DIRECTORIES = frozenset(
    {
        ".git",
        ".verdx",
        ".venv",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".hypothesis",
        "__pycache__",
        "node_modules",
        "htmlcov",
        "dist",
        "build",
        ".eggs",
    }
)


def canonical_hash(value: object) -> str:
    """Hash JSON data without depending on dictionary insertion order."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    """Read all bytes in bounded chunks, including files larger than 10 MiB."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns)


def _walk_error(error: OSError) -> None:
    raise error


def content_manifest(
    target: Path,
    *,
    include: tuple[str, ...] = ("**",),
    exclude: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Describe source files without following directory symlinks.

    Hidden source files are included. Known build, dependency, test, and Verdx
    output directories are excluded so a run does not hash its own outputs.
    Symlinks record the link text. Callers that execute arbitrary commands
    must reject them if they cannot bind the referenced content.
    """
    if target.is_file():
        paths = [target]
        root = target.parent
    elif target.is_dir():
        root = target
        paths = []
        for directory, dirs, files in os.walk(target, onerror=_walk_error):
            current = Path(directory)
            kept = []
            for name in sorted(dirs):
                path = current / name
                if name in GENERATED_DIRECTORIES or name.endswith(".egg-info"):
                    continue
                if path.is_symlink():
                    paths.append(path)
                else:
                    kept.append(name)
            dirs[:] = kept
            paths.extend(current / name for name in files)
    else:
        raise FileNotFoundError(f"target does not exist: {target}")

    records: list[dict[str, Any]] = []
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if path.name == ".coverage" or path.name.startswith(".coverage."):
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        if not matches(relative, include) or matches(relative, exclude):
            continue
        if path.is_symlink():
            value = os.readlink(path).encode("utf-8")
            records.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "size": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "executable": False,
                }
            )
            continue
        before = path.stat()
        if not path.is_file():
            raise ValueError(f"input is not a regular file: {relative}")
        digest = file_digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError(f"input changed while hashing: {relative}")
        records.append(
            {
                "path": relative,
                "kind": "file",
                "size": after.st_size,
                "sha256": digest,
                "executable": bool(after.st_mode & 0o111),
            }
        )
    return sorted(records, key=lambda record: record["path"])


def manifest_hash(records: list[dict[str, Any]]) -> str:
    return "sha256:" + canonical_hash({"schema_version": CONTENT_SCHEMA, "files": records})


def compute_content_hash(target: Path) -> str:
    return manifest_hash(content_manifest(target))
