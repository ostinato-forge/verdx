"""Evidence bounding: cap evidence size before canonicalization.

Prevents verbose validators from producing unbounded data.
Truncated evidence is marked so data loss is visible.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from verdx.contracts.observation import Evidence, ObservationDraft

DEFAULT_MAX_EVIDENCE_BYTES = 64 * 1024
DEFAULT_MAX_DATA_ENTRIES = 50
DEFAULT_MAX_STRING_LENGTH = 4096
DEFAULT_MAX_DEPTH = 20


def bound_evidence(
    evidence: Evidence,
    max_bytes: int = DEFAULT_MAX_EVIDENCE_BYTES,
    max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
    max_data_entries: int = DEFAULT_MAX_DATA_ENTRIES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Evidence:
    """Bound a single evidence item.

    - Truncate long strings in data
    - Limit number of data entries
    - Cap recursion depth for nested structures
    - Track original size and truncation flag
    """
    data = evidence.data
    original_size = len(json.dumps(data, default=str))
    was_truncated = evidence.truncated

    bounded_data = _bound_data(data, max_string_length, max_data_entries, max_depth)
    bounded_details = _truncate_string(evidence.details, max_string_length)

    bounded_size = len(json.dumps(bounded_data, default=str))
    data_changed = bounded_size < original_size
    details_changed = bounded_details != evidence.details
    truncated = was_truncated or data_changed or details_changed

    if bounded_size > max_bytes:
        original_keys = sorted(data.keys()) if isinstance(data, dict) else None
        bounded_data = {"_bounded": True, "_original_keys": original_keys}
        truncated = True

    return Evidence(
        source=evidence.source,
        kind=evidence.kind,
        details=bounded_details,
        data=bounded_data,
        truncated=truncated,
        original_size=original_size if truncated else evidence.original_size,
    )


def bound_drafts(
    drafts: list[ObservationDraft],
    max_evidence_bytes: int = DEFAULT_MAX_EVIDENCE_BYTES,
    max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
    max_data_entries: int = DEFAULT_MAX_DATA_ENTRIES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[ObservationDraft]:
    """Bound all evidence in a list of drafts."""
    result: list[ObservationDraft] = []
    for draft in drafts:
        bounded_evidence = tuple(
            bound_evidence(e, max_evidence_bytes, max_string_length, max_data_entries, max_depth)
            for e in draft.evidence
        )
        result.append(replace(draft, evidence=bounded_evidence))
    return result


def _truncate_string(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def _bound_data(
    data: dict[str, Any],
    max_string_length: int,
    max_data_entries: int,
    max_depth: int,
) -> dict[str, Any]:
    """Bound data dict entries."""
    result: dict[str, Any] = {}
    keys = list(data.keys())

    if len(keys) > max_data_entries:
        keys = keys[:max_data_entries]
        result["_truncated_keys"] = True
        result["_total_keys"] = len(data)

    for key in keys:
        result[key] = _bound_value(data[key], max_string_length, max_depth, depth=0)

    return result


def _bound_value(value: Any, max_string_length: int, max_depth: int, *, depth: int) -> Any:
    if depth > max_depth:
        return {"_bounded": True, "_reason": "max depth exceeded"}
    if isinstance(value, str):
        return _truncate_string(value, max_string_length)
    if isinstance(value, dict):
        return {
            k: _bound_value(v, max_string_length, max_depth, depth=depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_bound_value(v, max_string_length, max_depth, depth=depth + 1) for v in value]
    return value
