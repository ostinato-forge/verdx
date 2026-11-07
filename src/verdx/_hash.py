"""Versioned decision hashes for domain assessments."""

from __future__ import annotations

from typing import Any

from verdx._content import canonical_hash

DECISION_HASH_SCHEMA = "verdx.decision.v2"


def compute_decision_hash(
    *,
    target_content_hash: str,
    profile: str,
    validator_ids: list[str],
    observation_signatures: list[str],
    override_signatures: list[str],
    score: int,
    threshold: int,
    verdict: str,
    coverage_status: str,
    confidence: str,
    validator_results: list[dict[str, Any]],
    coverage_gaps: list[dict[str, Any]],
) -> str:
    """Compute a stable SHA256 from the canonical inputs that produced an assessment.

    Include every input to the final verdict, checker versions and outcomes,
    and the content identity. Run IDs, timestamps, and durations are excluded.
    """
    return canonical_hash(
        {
            "schema_version": DECISION_HASH_SCHEMA,
            "content_hash": target_content_hash,
            "observation_signatures": sorted(observation_signatures),
            "override_signatures": sorted(override_signatures),
            "profile": profile,
            "score": score,
            "validator_ids": sorted(validator_ids),
            "threshold": threshold,
            "verdict": verdict,
            "coverage_status": coverage_status,
            "confidence": confidence,
            "validator_results": sorted(validator_results, key=canonical_hash),
            "coverage_gaps": sorted(coverage_gaps, key=canonical_hash),
        },
    )
