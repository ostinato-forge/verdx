"""Tests for decision hash — determinism and stability."""

from __future__ import annotations

from verdx._hash import compute_decision_hash


def _base_inputs() -> dict:
    return {
        "target_content_hash": "sha256:abc123",
        "profile": "standard",
        "validator_ids": ["v1", "v2"],
        "observation_signatures": ["obs1", "obs2"],
        "override_signatures": [],
        "score": 85,
        "threshold": 80,
        "verdict": "pass",
        "coverage_status": "full",
        "confidence": "strong",
        "validator_results": [],
        "coverage_gaps": [],
    }


def test_deterministic() -> None:
    h1 = compute_decision_hash(**_base_inputs())
    h2 = compute_decision_hash(**_base_inputs())
    assert h1 == h2


def test_stable_across_calls() -> None:
    hashes = [compute_decision_hash(**_base_inputs()) for _ in range(10)]
    assert len(set(hashes)) == 1


def test_order_independent() -> None:
    inputs = _base_inputs()
    h1 = compute_decision_hash(**inputs)
    inputs["validator_ids"] = ["v2", "v1"]
    inputs["observation_signatures"] = ["obs2", "obs1"]
    h2 = compute_decision_hash(**inputs)
    assert h1 == h2


def test_different_inputs_different_hash() -> None:
    h1 = compute_decision_hash(**_base_inputs())
    modified = _base_inputs()
    modified["score"] = 90
    h2 = compute_decision_hash(**modified)
    assert h1 != h2


def test_override_changes_hash() -> None:
    h1 = compute_decision_hash(**_base_inputs())
    modified = _base_inputs()
    modified["override_signatures"] = ["ov1"]
    h2 = compute_decision_hash(**modified)
    assert h1 != h2


def test_hash_is_hex_sha256() -> None:
    h = compute_decision_hash(**_base_inputs())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
