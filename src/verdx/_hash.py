"""Decision hash — deterministic SHA256 of validation inputs and result."""

from __future__ import annotations

import hashlib
import json


def compute_decision_hash(
    *,
    target_content_hash: str,
    profile: str,
    validator_ids: list[str],
    observation_signatures: list[str],
    override_signatures: list[str],
    score: int,
) -> str:
    """Compute a stable SHA256 from the canonical inputs that produced an assessment.

    The hash covers what was validated (content hash), how (profile, validators),
    what was found (observation signatures), what was suppressed (overrides),
    and the final score. Two runs with identical inputs produce the same hash.
    """
    canonical = json.dumps(
        {
            "content_hash": target_content_hash,
            "observation_signatures": sorted(observation_signatures),
            "override_signatures": sorted(override_signatures),
            "profile": profile,
            "score": score,
            "validator_ids": sorted(validator_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
