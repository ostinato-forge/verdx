"""Read acceptance rules without importing or executing project code."""

from __future__ import annotations

import tomllib
from pathlib import Path

from verdx._content import canonical_hash
from verdx.acceptance.models import Policy


def parse_policy(raw: bytes) -> Policy:
    return Policy.model_validate(tomllib.loads(raw.decode("utf-8")))


def load_policy(path: Path) -> tuple[Policy, bytes]:
    if not path.is_file():
        raise ValueError(f"no acceptance policy at {path}; create verdx.toml first")
    raw = path.read_bytes()
    return parse_policy(raw), raw


def policy_hash(policy: Policy) -> str:
    return canonical_hash(policy.model_dump(mode="json"))
