"""ULID-style ID generation — timestamp-sortable, collision-free across machines."""

from __future__ import annotations

import secrets
import time


def generate_id(prefix: str = "") -> str:
    """Generate a ULID-style ID: 12-char timestamp hex + 10-char random hex.

    With prefix: "pat-018fc3a1b2c0ab3d4e5f6a"
    Without:     "018fc3a1b2c0ab3d4e5f6a"
    """
    ts = f"{int(time.time() * 1000):012x}"
    rand = secrets.token_hex(8)
    raw = f"{ts}{rand}"
    return f"{prefix}-{raw}" if prefix else raw


def generate_run_id() -> str:
    """Generate a run-scoped ID for per-run file sharding."""
    return generate_id()
