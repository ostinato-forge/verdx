"""Tests for fingerprint and observation ID computation."""

from __future__ import annotations

from verdx.engine.validation.fingerprint import (
    compute_evidence_hash,
    compute_issue_fingerprint,
    compute_observation_id,
    normalize_location,
)

# ===========================================================================
# Location normalization
# ===========================================================================


class TestNormalizeLocation:
    def test_none_stays_none(self):
        assert normalize_location(None) is None

    def test_empty_becomes_none(self):
        assert normalize_location("") is None

    def test_whitespace_only_becomes_none(self):
        assert normalize_location("   ") is None

    def test_strips_whitespace(self):
        assert normalize_location("  exercise:ge01  ") == "exercise:ge01"

    def test_normalizes_backslashes(self):
        assert normalize_location(r"exercise\ge01\step:3") == "exercise/ge01/step:3"

    def test_collapses_whitespace(self):
        assert normalize_location("exercise:ge01   step:3") == "exercise:ge01 step:3"

    def test_preserves_semantic_identifiers(self):
        loc = "exercise:ge01/step:3"
        assert normalize_location(loc) == "exercise:ge01/step:3"

    def test_idempotent(self):
        loc = "exercise:ge01/step:3"
        assert normalize_location(normalize_location(loc)) == normalize_location(loc)


# ===========================================================================
# Issue fingerprint
# ===========================================================================


class TestIssueFingerprint:
    def test_format(self):
        fp = compute_issue_fingerprint(
            target_key="sample/ch04/ge01",
            rule_id="yaml-syntax",
            location="exercise:ge01/step:3",
            category="correctness",
        )
        assert fp.startswith("qu:v1:")
        parts = fp.split(":")
        assert len(parts) == 3
        assert len(parts[2]) == 16

    def test_deterministic(self):
        args = {
            "target_key": "sample/ch04/ge01",
            "rule_id": "yaml-syntax",
            "location": "exercise:ge01/step:3",
            "category": "correctness",
        }
        fp1 = compute_issue_fingerprint(**args)
        fp2 = compute_issue_fingerprint(**args)
        assert fp1 == fp2

    def test_differs_by_target_key(self):
        base = {
            "rule_id": "yaml-syntax",
            "location": "step:1",
            "category": "correctness",
        }
        fp1 = compute_issue_fingerprint(target_key="sample/ch04/ge01", **base)
        fp2 = compute_issue_fingerprint(target_key="sample/ch04/ge02", **base)
        assert fp1 != fp2

    def test_differs_by_rule_id(self):
        base = {
            "target_key": "sample/ch04/ge01",
            "location": "step:1",
            "category": "correctness",
        }
        fp1 = compute_issue_fingerprint(rule_id="yaml-syntax", **base)
        fp2 = compute_issue_fingerprint(rule_id="md-lint", **base)
        assert fp1 != fp2

    def test_differs_by_location(self):
        base = {
            "target_key": "sample/ch04/ge01",
            "rule_id": "yaml-syntax",
            "category": "correctness",
        }
        fp1 = compute_issue_fingerprint(location="step:1", **base)
        fp2 = compute_issue_fingerprint(location="step:2", **base)
        assert fp1 != fp2

    def test_differs_by_category(self):
        base = {
            "target_key": "sample/ch04/ge01",
            "rule_id": "yaml-syntax",
            "location": "step:1",
        }
        fp1 = compute_issue_fingerprint(category="correctness", **base)
        fp2 = compute_issue_fingerprint(category="completeness", **base)
        assert fp1 != fp2

    def test_none_location_produces_valid_fingerprint(self):
        fp = compute_issue_fingerprint(
            target_key="sample/ch04/ge01",
            rule_id="yaml-syntax",
            location=None,
            category="correctness",
        )
        assert fp.startswith("qu:v1:")

    def test_none_location_differs_from_empty_string(self):
        fp_none = compute_issue_fingerprint(
            target_key="t",
            rule_id="r",
            location=None,
            category="c",
        )
        fp_empty = compute_issue_fingerprint(
            target_key="t",
            rule_id="r",
            location="",
            category="c",
        )
        assert fp_none == fp_empty

    def test_location_normalization_produces_same_fingerprint(self):
        base = {
            "target_key": "sample/ch04/ge01",
            "rule_id": "yaml-syntax",
            "category": "correctness",
        }
        fp1 = compute_issue_fingerprint(location="exercise:ge01/step:3", **base)
        fp2 = compute_issue_fingerprint(
            location="  exercise:ge01/step:3  ",
            **base,
        )
        fp3 = compute_issue_fingerprint(
            location=r"exercise:ge01\step:3",
            **base,
        )
        assert fp1 == fp2
        assert fp1 == fp3


# ===========================================================================
# Observation ID
# ===========================================================================


class TestObservationId:
    def test_format(self):
        obs_id = compute_observation_id(
            run_id="run-001",
            issue_fingerprint="qu:v1:abcdef0123456789",
            evidence_hash="abcdef0123456789",
            validator_id="yaml-lint",
            validator_version="1.0.0",
        )
        assert obs_id.startswith("obs:run-001:")
        parts = obs_id.split(":")
        assert len(parts) == 3
        assert len(parts[2]) == 16

    def test_deterministic(self):
        args = {
            "run_id": "run-001",
            "issue_fingerprint": "qu:v1:abc",
            "evidence_hash": "abc",
            "validator_id": "yaml-lint",
            "validator_version": "1.0.0",
        }
        assert compute_observation_id(**args) == compute_observation_id(**args)

    def test_differs_by_run_id(self):
        base = {
            "issue_fingerprint": "qu:v1:abc",
            "evidence_hash": "abc",
            "validator_id": "yaml-lint",
            "validator_version": "1.0.0",
        }
        id1 = compute_observation_id(run_id="run-001", **base)
        id2 = compute_observation_id(run_id="run-002", **base)
        assert id1 != id2

    def test_differs_by_evidence_hash(self):
        base = {
            "run_id": "run-001",
            "issue_fingerprint": "qu:v1:abc",
            "validator_id": "yaml-lint",
            "validator_version": "1.0.0",
        }
        id1 = compute_observation_id(evidence_hash="hash-a", **base)
        id2 = compute_observation_id(evidence_hash="hash-b", **base)
        assert id1 != id2


# ===========================================================================
# Evidence hash
# ===========================================================================


class TestEvidenceHash:
    def test_deterministic(self):
        data = [{"source": "lint", "kind": "error", "details": "bad"}]
        assert compute_evidence_hash(data) == compute_evidence_hash(data)

    def test_differs_by_content(self):
        h1 = compute_evidence_hash([{"a": 1}])
        h2 = compute_evidence_hash([{"a": 2}])
        assert h1 != h2

    def test_empty_list(self):
        h = compute_evidence_hash([])
        assert len(h) == 16

    def test_key_order_does_not_matter(self):
        h1 = compute_evidence_hash([{"a": 1, "b": 2}])
        h2 = compute_evidence_hash([{"b": 2, "a": 1}])
        assert h1 == h2
