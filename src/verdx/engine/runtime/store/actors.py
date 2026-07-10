"""Actor directory: minimal v1 implementation.

Stores opaque ID -> metadata mappings. Never required for normal
validation. Observations remain readable if actor entries are deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from verdx.engine.runtime.store.atomic import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ActorEntry:
    """Metadata for a known actor (role-level, never individual identity)."""

    opaque_id: str
    role: str
    kind: str
    team: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "opaque_id": self.opaque_id,
            "role": self.role,
            "kind": self.kind,
        }
        if self.team is not None:
            result["team"] = self.team
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActorEntry:
        return cls(
            opaque_id=data["opaque_id"],
            role=data["role"],
            kind=data["kind"],
            team=data.get("team"),
        )


class ActorDirectory:
    """File-backed actor directory."""

    def __init__(self, state_root: Path) -> None:
        self._path = state_root / "actors" / "directory.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        data: dict[str, dict[str, Any]] = json.loads(self._path.read_text(encoding="utf-8"))
        return data

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        atomic_write_text(
            self._path,
            json.dumps(data, indent=2, sort_keys=True),
        )

    def put(self, entry: ActorEntry) -> None:
        """Add or update an actor entry."""
        data = self._load()
        data[entry.opaque_id] = entry.to_dict()
        self._save(data)

    def get(self, opaque_id: str) -> ActorEntry | None:
        """Look up an actor by opaque ID."""
        data = self._load()
        if opaque_id not in data:
            return None
        return ActorEntry.from_dict(data[opaque_id])

    def delete(self, opaque_id: str) -> bool:
        """Remove an actor entry. Returns True if it existed."""
        data = self._load()
        if opaque_id not in data:
            return False
        del data[opaque_id]
        self._save(data)
        return True

    def list_all(self) -> list[ActorEntry]:
        """List all actor entries."""
        data = self._load()
        return [ActorEntry.from_dict(v) for v in data.values()]
