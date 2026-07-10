"""Centralized flywheel storage — seed, org, and per-repo layers under data/flywheel/."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from verdx.engine.flywheel.ids import generate_run_id
from verdx.engine.flywheel.types import Correction, Pattern
from verdx.types import VALID_DECISIONS, Override

_log = logging.getLogger(__name__)

REQUIRED_OVERRIDE_FIELDS = frozenset({"signature", "decision", "justification", "by", "date"})


class OverrideLoadError(Exception):
    """Raised when an overrides.json file is malformed."""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                entries.append(json.loads(stripped))
    return entries


def _load_patterns_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    result: list[dict[str, Any]] = data.get("patterns", [])
    return result


def _dict_to_correction(d: dict[str, Any]) -> Correction | None:
    required = ("observation_signature", "target_key", "initial_severity", "final_decision")
    if not all(d.get(k) for k in required):
        missing = [k for k in required if not d.get(k)]
        _log.warning("skipping malformed correction: missing %s", missing)
        return None
    return Correction(
        observation_signature=d["observation_signature"],
        target_key=d["target_key"],
        initial_severity=d["initial_severity"],
        final_decision=d["final_decision"],
        description=d.get("description", ""),
        domain=d.get("domain", "unknown"),
        timestamp=d.get("timestamp", ""),
    )


def _dict_to_pattern(d: dict[str, Any]) -> Pattern | None:
    if not d.get("id") or not d.get("symptoms"):
        _log.warning("skipping malformed pattern: %s", d.get("id", "?"))
        return None
    return Pattern(
        id=d["id"],
        symptoms=d.get("symptoms", []),
        diagnosis=d.get("diagnosis", ""),
        resolution=d.get("resolution", ""),
        category=d.get("category", ""),
        domain=d.get("domain", "unknown"),
        seen_count=d.get("seen_count", 1),
        observation_signatures=d.get("observation_signatures", []),
        targets=d.get("targets", []),
        source=d.get("source", ""),
        created=d.get("created", ""),
        updated=d.get("updated", ""),
    )


def _find_repo_root() -> Path | None:
    """Walk up from this file to find the verdx repo root (pyproject.toml + src/verdx)."""
    candidate = Path(__file__).resolve().parent
    for _ in range(10):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "verdx").is_dir():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


def _looks_like_data_repo(path: Path) -> bool:
    """Check if a directory looks like a verdx-data repo."""
    return any((path / d).is_dir() for d in ("org", "repos", "seed"))


def _resolve_data_root(explicit: Path | None) -> Path:
    """Find the flywheel data directory.

    Resolution order: explicit param > VERDX_FLYWHEEL_DATA env > sibling verdx-data
    clone > local fallback.
    """
    if explicit is not None:
        return explicit

    env = os.environ.get("VERDX_FLYWHEEL_DATA")
    if env:
        return Path(env)

    repo_root = _find_repo_root()
    if repo_root is not None:
        data_repo = repo_root.parent / "verdx-data"
        if data_repo.is_dir() and _looks_like_data_repo(data_repo):
            return data_repo

    fallback = Path.home() / ".local" / "share" / "verdx" / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _atomic_write_json(dest: Path, data: Any, *, prefix: str = ".tmp-") -> Path:
    """Write JSON atomically via tempfile + os.replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp", prefix=prefix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, separators=(",", ": "))
            f.write("\n")
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return dest


def _parse_overrides_array(
    raw: Any,
    *,
    source_label: str,
) -> list[Override]:
    """Validate and parse a JSON array of override dicts, filtering expired entries."""
    if not isinstance(raw, list):
        raise OverrideLoadError(f"{source_label}: expected a JSON array, got {type(raw).__name__}")

    today = date.today().isoformat()
    result: list[Override] = []

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise OverrideLoadError(
                f"{source_label}[{i}]: expected object, got {type(entry).__name__}"
            )

        missing = REQUIRED_OVERRIDE_FIELDS - entry.keys()
        if missing:
            raise OverrideLoadError(f"{source_label}[{i}]: missing fields: {sorted(missing)}")

        if entry["decision"] not in VALID_DECISIONS:
            raise OverrideLoadError(f"{source_label}[{i}]: invalid decision {entry['decision']!r}")

        expires = entry.get("expires")
        if expires is not None and expires < today:
            continue

        result.append(
            Override(
                signature=entry["signature"],
                decision=entry["decision"],
                justification=entry["justification"],
                by=entry["by"],
                date=entry["date"],
                expires=expires,
            )
        )

    return result


def _load_overrides_file(path: Path) -> list[Override]:
    """Load and validate a single overrides.json file."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise OverrideLoadError(f"cannot read {path}: {exc}") from exc
    return _parse_overrides_array(raw, source_label=str(path))


_SAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _validate_target_key(target_key: str) -> None:
    """Reject a target key that could escape the data root.

    The key becomes a path component under ``repos/``. A key that is empty,
    contains a path separator, or is ``..`` could write outside the data root,
    so it is rejected here as defense in depth (the porcelain layer sanitizes
    keys before they reach the store, but the store must not trust that).
    """
    if not target_key:
        raise ValueError("target_key must be non-empty")
    if "/" in target_key or "\\" in target_key or os.sep in target_key:
        raise ValueError(f"target_key must not contain a path separator: {target_key!r}")
    if target_key == ".." or ".." in Path(target_key).parts:
        raise ValueError(f"target_key must not contain '..': {target_key!r}")


def sanitize_target_key(raw: str) -> str:
    """Map an arbitrary user-supplied key string to a safe, stable store key.

    Used where a caller may hand in a path-like or otherwise unsafe string
    (e.g. the flywheel CLI accepts a path or a bare key). The result is
    non-empty and free of path separators and ``..``. To stay stable and
    collision-free it combines a sanitized basename with a short hash of the
    original string, mirroring how the porcelain layer derives target keys.

    A string that is already a valid bare key (no separator, no ``..``) is
    returned unchanged so existing keys keep resolving to the same store dir.
    """
    try:
        _validate_target_key(raw)
    except ValueError:
        pass
    else:
        return raw

    base = _SAFE_KEY_CHARS.sub("-", Path(raw).name).strip("-.")
    if not base or base == "..":
        base = "target"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


class FlywheelStore:
    """Centralized flywheel storage: seed + org + per-repo layers."""

    def __init__(self, target_key: str, *, data_root: Path | None = None) -> None:
        _validate_target_key(target_key)
        self._target_key = target_key
        self._root = _resolve_data_root(data_root)
        self._run_id = generate_run_id()

    def _seed_dir(self) -> Path:
        return self._root / "seed"

    def _org_dir(self) -> Path:
        return self._root / "org"

    def _repo_dir(self) -> Path:
        return self._root / "repos" / self._target_key

    def _corrections_dir(self) -> Path:
        return self._repo_dir() / "corrections"

    # -- blocked --

    def load_blocked(self) -> list[dict[str, Any]]:
        """Load blocked pattern IDs from org layer."""
        path = self._org_dir() / "blocked.json"
        if not path.is_file():
            return []
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        result: list[dict[str, Any]] = data.get("blocked", [])
        return result

    def save_blocked(self, entries: list[dict[str, Any]]) -> Path:
        """Write blocked.json to org layer (atomic)."""
        dest = self._org_dir() / "blocked.json"
        return _atomic_write_json(dest, {"blocked": entries}, prefix=".blocked-")

    def blocked_ids(self) -> frozenset[str]:
        """Return the set of blocked pattern IDs."""
        return frozenset(e["pattern_id"] for e in self.load_blocked())

    # -- corrections --

    def append_correction(self, correction: Correction) -> Path:
        """Append to per-run corrections file: corrections/<run_id>.jsonl."""
        corr_dir = self._corrections_dir()
        corr_dir.mkdir(parents=True, exist_ok=True)
        dest = corr_dir / f"{self._run_id}.jsonl"
        with dest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(correction), separators=(",", ":")) + "\n")
        return dest

    def load_corrections(self) -> list[Correction]:
        """Load all corrections from corrections/*.jsonl, sorted by timestamp."""
        corr_dir = self._corrections_dir()
        if not corr_dir.is_dir():
            return []
        all_corrections: list[Correction] = []
        for jsonl_file in sorted(corr_dir.glob("*.jsonl")):
            for entry in _load_jsonl(jsonl_file):
                c = _dict_to_correction(entry)
                if c is not None:
                    all_corrections.append(c)
        all_corrections.sort(key=lambda c: c.timestamp)
        return all_corrections

    def save_corrections(self, corrections: list[Correction]) -> Path:
        """Write ALL corrections into a single corrections/<run_id>.jsonl (for compaction)."""
        corr_dir = self._corrections_dir()
        corr_dir.mkdir(parents=True, exist_ok=True)
        dest = corr_dir / f"{self._run_id}.jsonl"
        fd, tmp = tempfile.mkstemp(dir=str(corr_dir), suffix=".tmp", prefix=".corrections-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for c in corrections:
                    f.write(json.dumps(asdict(c), separators=(",", ":")) + "\n")
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return dest

    # -- patterns --

    def load_patterns(self) -> list[Pattern]:
        """Load patterns: seed -> org (per-file) -> repo, shadowing by ID."""
        by_id: dict[str, Pattern] = {}

        for entry in _load_patterns_file(self._seed_dir() / "patterns.json"):
            pattern = _dict_to_pattern(entry)
            if pattern is not None:
                by_id[pattern.id] = pattern

        for org_pat in self.load_org_patterns():
            by_id[org_pat.id] = org_pat

        for entry in _load_patterns_file(self._repo_dir() / "patterns.json"):
            pattern = _dict_to_pattern(entry)
            if pattern is not None:
                by_id[pattern.id] = pattern

        return list(by_id.values())

    def load_repo_patterns(self) -> list[Pattern]:
        """Load patterns from repo layer only."""
        path = self._repo_dir() / "patterns.json"
        return [p for d in _load_patterns_file(path) if (p := _dict_to_pattern(d)) is not None]

    def save_patterns(self, patterns: list[Pattern]) -> Path:
        """Write to repo-layer patterns.json (atomic)."""
        dest = self._repo_dir() / "patterns.json"
        return _atomic_write_json(
            dest, {"patterns": [asdict(p) for p in patterns]}, prefix=".patterns-"
        )

    # -- org patterns (per-file format) --

    def load_org_patterns(self) -> list[Pattern]:
        """Load org patterns from org/patterns/*.json (each file is {\"pattern\": {...}})."""
        patterns_dir = self._org_dir() / "patterns"
        if not patterns_dir.is_dir():
            return []
        result: list[Pattern] = []
        for pat_file in sorted(patterns_dir.glob("*.json")):
            try:
                with pat_file.open(encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                _log.warning("skipping unreadable org pattern file: %s", pat_file)
                continue
            pat_dict = data.get("pattern")
            if pat_dict is None:
                _log.warning("skipping org pattern file without 'pattern' key: %s", pat_file)
                continue
            pattern = _dict_to_pattern(pat_dict)
            if pattern is not None:
                result.append(pattern)
        return result

    def save_org_pattern(self, pattern: Pattern) -> Path:
        """Write a single org pattern to org/patterns/<pattern.id>.json (atomic)."""
        dest = self._org_dir() / "patterns" / f"{pattern.id}.json"
        return _atomic_write_json(dest, {"pattern": asdict(pattern)}, prefix=".org-pat-")

    # -- overrides --

    def load_overrides(self) -> list[Override]:
        """Load overrides from org + repo layers, filtering expired. Repo wins on collision."""
        org_overrides = _load_overrides_file(self._org_dir() / "overrides.json")
        repo_overrides = _load_overrides_file(self._repo_dir() / "overrides.json")

        by_sig: dict[str, Override] = {}
        for o in org_overrides:
            by_sig[o.signature] = o
        for o in repo_overrides:
            by_sig[o.signature] = o

        return list(by_sig.values())

    def save_overrides(self, overrides: list[Override]) -> Path:
        """Write overrides to repo-level overrides.json (atomic)."""
        dest = self._repo_dir() / "overrides.json"
        data = [asdict(o) for o in overrides]
        return _atomic_write_json(dest, data, prefix=".overrides-")

    # -- repo listing --

    def list_repo_keys(self) -> list[str]:
        """List subdirectories of repos/."""
        repos_dir = self._root / "repos"
        if not repos_dir.is_dir():
            return []
        return sorted(
            d.name for d in repos_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
        )
