"""Property-based tests for fingerprint stability.

Proves that issue fingerprints are deterministic and independent of
fields that should not affect cross-run tracking.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings
from hypothesis import strategies as st

from verdx.engine.validation.fingerprint import compute_issue_fingerprint

nonempty = st.text(
    alphabet=st.characters(categories=("L", "N", "P")),
    min_size=1,
    max_size=30,
)


class TestFingerprintStability:
    @given(
        target_key=nonempty,
        rule_id=nonempty,
        location=st.one_of(st.none(), nonempty),
        category=nonempty,
    )
    @settings(max_examples=100)
    def test_same_inputs_same_fingerprint(self, target_key, rule_id, location, category):
        fp1 = compute_issue_fingerprint(target_key, rule_id, location, category)
        fp2 = compute_issue_fingerprint(target_key, rule_id, location, category)
        assert fp1 == fp2

    @given(
        target_key=nonempty,
        rule_id=nonempty,
        location=st.one_of(st.none(), nonempty),
        category=nonempty,
    )
    @settings(max_examples=50)
    def test_fingerprint_has_expected_prefix(self, target_key, rule_id, location, category):
        fp = compute_issue_fingerprint(target_key, rule_id, location, category)
        assert fp.startswith("qu:v1:")
        assert len(fp) == len("qu:v1:") + 16

    @given(
        target_key=nonempty,
        rule_id=nonempty,
        category=nonempty,
    )
    @settings(max_examples=50)
    def test_none_location_same_as_empty_string(self, target_key, rule_id, category):
        fp_none = compute_issue_fingerprint(target_key, rule_id, None, category)
        fp_empty = compute_issue_fingerprint(target_key, rule_id, "", category)
        assert fp_none == fp_empty

    @given(
        target_key=nonempty,
        rule_id=nonempty,
        location=nonempty,
        cat1=nonempty,
        cat2=nonempty,
    )
    @settings(max_examples=50)
    def test_different_categories_different_fingerprints(
        self, target_key, rule_id, location, cat1, cat2
    ):
        if cat1 == cat2:
            return
        fp1 = compute_issue_fingerprint(target_key, rule_id, location, cat1)
        fp2 = compute_issue_fingerprint(target_key, rule_id, location, cat2)
        assert fp1 != fp2
