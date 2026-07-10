"""Validator result cache — skip re-execution on unchanged content.

Two-layer design:
1. **Validator execution cache** (this module): caches raw
   ValidatorResult per (content_hash, validator_id, validator_version).
   Running validators (especially live ones with SSH/lab access) is the
   expensive operation worth caching.
2. **Assessment derivation** (always recomputed): canonicalization,
   escalation, scoring, and reporting run fresh every time from cached
   validator results + current policy + current dispositions.  These are
   pure functions — fast and must always reflect current config.

The old design cached the full assessment JSON keyed by
(content_hash, validator_set_hash).  That meant policy, disposition,
or scoring config changes did NOT invalidate the cache — a cached
"PASS" could be wrong after policy updates.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.store.atomic import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


class CacheStore:
    """Read/write cached ValidatorResult JSON by per-validator cache key.

    Key: ``(content_hash, validator_id, validator_version)``

    Each entry stores a single ``ValidatorResult`` serialized as JSON.
    Assessment derivation (scoring, escalation, dispositions) is always
    recomputed from these cached results + current config.
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "cache"

    def _key_path(
        self,
        content_hash: str,
        validator_id: str,
        validator_version: str,
    ) -> Path:
        # Sanitize validator_id for filesystem safety (replace / with _)
        safe_id = validator_id.replace("/", "_")
        return self._dir / f"{content_hash}_{safe_id}_{validator_version}.json"

    def get(
        self,
        content_hash: str,
        validator_id: str,
        validator_version: str,
    ) -> ValidatorResult | None:
        """Retrieve a cached ValidatorResult, or None on miss/corruption."""
        path = self._key_path(content_hash, validator_id, validator_version)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            return ValidatorResult.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def put(
        self,
        content_hash: str,
        validator_id: str,
        validator_version: str,
        result: ValidatorResult,
    ) -> None:
        """Cache a ValidatorResult."""
        path = self._key_path(content_hash, validator_id, validator_version)
        atomic_write_text(path, json.dumps(result.to_dict(), sort_keys=True))

    def clear(self) -> int:
        """Remove all cached entries. Returns count of entries removed."""
        if not self._dir.exists():
            return 0
        count = 0
        for path in self._dir.glob("*.json"):
            path.unlink()
            count += 1
        return count
