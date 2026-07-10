"""Check result cache with config-aware keys.

Keys include config_hash so config changes (via profiles, overrides)
correctly invalidate cached results.  Uses a separate ``check-cache/``
directory so it coexists with the old ``cache/`` store.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.store.atomic import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


class CheckCacheStore:
    """Read/write cached ValidatorResult by 4-part cache key.

    Key: ``(content_hash, check_id, check_version, config_hash)``
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "check-cache"

    def _key_path(
        self,
        content_hash: str,
        check_id: str,
        check_version: str,
        config_hash: str,
    ) -> Path:
        safe_id = check_id.replace("/", "_")
        cfg_short = config_hash.split(":", 1)[-1] if ":" in config_hash else config_hash
        return self._dir / f"{content_hash}_{safe_id}_{check_version}_{cfg_short}.json"

    def get(
        self,
        content_hash: str,
        check_id: str,
        check_version: str,
        config_hash: str,
    ) -> ValidatorResult | None:
        """Retrieve a cached ValidatorResult, or None on miss/corruption."""
        path = self._key_path(content_hash, check_id, check_version, config_hash)
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
        check_id: str,
        check_version: str,
        config_hash: str,
        result: ValidatorResult,
    ) -> None:
        """Cache a ValidatorResult atomically."""
        path = self._key_path(content_hash, check_id, check_version, config_hash)
        atomic_write_text(path, json.dumps(result.to_dict(), sort_keys=True))

    def clear(self) -> int:
        """Remove all cached entries. Returns count removed."""
        if not self._dir.exists():
            return 0
        count = 0
        for path in self._dir.glob("*.json"):
            path.unlink()
            count += 1
        return count
