"""Fingerprint and observation ID computation.

Pure functions. No I/O, no randomness, no current-time reads.
Inputs are explicit; outputs are deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from verdx.contracts.observation import OBSERVATION_SCHEMA_VERSION

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_location(location: str | None) -> str | None:
    """Normalize a location string for stable fingerprinting.

    - Strip surrounding whitespace
    - Normalize path separators to /
    - Collapse repeated whitespace
    - Preserve semantic identifiers (exercise:, step:, etc.)
    - None stays None
    """
    if location is None:
        return None
    loc = location.strip()
    if not loc:
        return None
    loc = loc.replace("\\", "/")
    loc = _WHITESPACE_RUN.sub(" ", loc)
    return loc


def compute_issue_fingerprint(
    target_key: str,
    rule_id: str,
    location: str | None,
    category: str,
) -> str:
    """Compute stable issue fingerprint from identity fields.

    Uses canonical JSON (sorted keys) as hash input.
    Format: qu:<schema-version-suffix>:<sha256-hex-prefix>

    The fingerprint is deliberately independent of summary, evidence,
    severity, and confidence -- those can change across runs without
    breaking cross-run tracking or disposition matching.
    """
    normalized = normalize_location(location)
    canonical = json.dumps(
        {
            "category": category,
            "location": normalized,
            "rule_id": rule_id,
            "target_key": target_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    version_tag = OBSERVATION_SCHEMA_VERSION.rsplit(".", 1)[-1]
    return f"qu:{version_tag}:{digest}"


def compute_observation_id(
    run_id: str,
    issue_fingerprint: str,
    evidence_hash: str,
    validator_id: str,
    validator_version: str,
) -> str:
    """Compute unique observation ID for a specific sighting in a run.

    Format: obs:<run-id>:<sha256-hex-prefix>

    Same run + same bounded drafts + same provenance = same observation ID.
    """
    canonical = json.dumps(
        {
            "evidence_hash": evidence_hash,
            "issue_fingerprint": issue_fingerprint,
            "run_id": run_id,
            "validator_id": validator_id,
            "validator_version": validator_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"obs:{run_id}:{digest}"


def compute_validator_set_hash(
    validators: list[tuple[str, str]],
) -> str:
    """Compute a deterministic hash of sorted (validator_id, validator_version) tuples."""
    canonical = json.dumps(
        sorted(validators),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_evidence_hash(evidence_dicts: list[dict[str, Any]]) -> str:
    """Compute a stable hash over serialized evidence."""
    canonical = json.dumps(
        evidence_dicts,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
