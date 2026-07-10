"""Override loading — thin wrapper over FlywheelStore.load_overrides()."""

from __future__ import annotations

from pathlib import Path

from verdx.engine.flywheel.store import OverrideLoadError as OverrideError
from verdx.types import Override

__all__ = ["OverrideError", "load_overrides"]


def load_overrides(target_key: str, *, data_root: Path | None = None) -> list[Override]:
    """Load overrides for a target, filtering expired entries."""
    from verdx.engine.flywheel.store import FlywheelStore

    return FlywheelStore(target_key, data_root=data_root).load_overrides()
