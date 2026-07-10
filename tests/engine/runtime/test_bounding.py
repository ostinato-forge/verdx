"""Tests for evidence bounding."""

from __future__ import annotations

from verdx.contracts.observation import Evidence, ObservationDraft
from verdx.engine.runtime.bounding import DEFAULT_MAX_DEPTH, bound_drafts, bound_evidence


def _evidence(**overrides) -> Evidence:
    defaults = {
        "source": "lint",
        "kind": "error",
        "details": "Something went wrong",
        "data": {"line": 42, "column": 1},
    }
    return Evidence(**(defaults | overrides))


class TestBoundEvidence:
    def test_small_evidence_unchanged(self):
        e = _evidence()
        bounded = bound_evidence(e)
        assert bounded.data == e.data
        assert bounded.truncated is False
        assert bounded.original_size is None

    def test_long_string_truncated(self):
        long_str = "x" * 10000
        e = _evidence(data={"content": long_str})
        bounded = bound_evidence(e, max_string_length=100)
        assert len(bounded.data["content"]) == 100
        assert bounded.data["content"].endswith("...")
        assert bounded.truncated is True
        assert bounded.original_size is not None

    def test_many_keys_truncated(self):
        data = {f"key_{i}": i for i in range(200)}
        e = _evidence(data=data)
        bounded = bound_evidence(e, max_data_entries=10)
        assert len([k for k in bounded.data if not k.startswith("_")]) <= 10
        assert bounded.data["_truncated_keys"] is True
        assert bounded.data["_total_keys"] == 200
        assert bounded.truncated is True

    def test_nested_strings_bounded(self):
        e = _evidence(data={"nested": {"deep": "x" * 10000}})
        bounded = bound_evidence(e, max_string_length=50)
        assert len(bounded.data["nested"]["deep"]) == 50

    def test_details_truncated(self):
        e = _evidence(details="x" * 10000)
        bounded = bound_evidence(e, max_string_length=100)
        assert len(bounded.details) == 100
        assert bounded.details.endswith("...")

    def test_already_truncated_preserved(self):
        e = _evidence(truncated=True, original_size=50000)
        bounded = bound_evidence(e)
        assert bounded.truncated is True

    def test_oversized_data_replaced(self):
        huge = {"k": "x" * 100000}
        e = _evidence(data=huge)
        bounded = bound_evidence(e, max_bytes=1000)
        assert bounded.data["_bounded"] is True
        assert bounded.truncated is True

    def test_list_values_bounded(self):
        e = _evidence(data={"items": ["x" * 10000, "y" * 10000]})
        bounded = bound_evidence(e, max_string_length=50)
        for item in bounded.data["items"]:
            assert len(item) <= 50

    def test_scalar_preserved(self):
        e = _evidence(data={"count": 42, "flag": True, "rate": 3.14})
        bounded = bound_evidence(e, max_string_length=10)
        assert bounded.data["count"] == 42
        assert bounded.data["flag"] is True
        assert bounded.data["rate"] == 3.14


class TestBoundDrafts:
    def _draft(self, **evidence_overrides) -> ObservationDraft:
        from verdx.contracts.dimensions import (
            Confidence,
            DimLevel,
            Severity,
        )

        return ObservationDraft(
            target_key="sample/ch04/ge01",
            location="step:1",
            rule_id="test-rule",
            category="correctness",
            severity=Severity.HIGH,
            confidence=Confidence.CONFIRMED,
            dimensions={"correctness": DimLevel.HIGH},
            summary="Test observation",
            evidence=(_evidence(**evidence_overrides),),
        )

    def test_bounds_evidence_in_drafts(self):
        draft = self._draft(data={"content": "x" * 10000})
        bounded = bound_drafts([draft], max_string_length=100)
        assert len(bounded) == 1
        assert bounded[0].evidence[0].truncated is True

    def test_preserves_non_evidence_fields(self):
        draft = self._draft()
        bounded = bound_drafts([draft])
        assert bounded[0].target_key == "sample/ch04/ge01"
        assert bounded[0].rule_id == "test-rule"
        assert bounded[0].summary == "Test observation"

    def test_empty_drafts(self):
        assert bound_drafts([]) == []

    def test_draft_without_evidence(self):
        from verdx.contracts.dimensions import (
            Confidence,
            DimLevel,
            Severity,
        )

        draft = ObservationDraft(
            target_key="sample/ch04/ge01",
            location="step:1",
            rule_id="test-rule",
            category="correctness",
            severity=Severity.HIGH,
            confidence=Confidence.CONFIRMED,
            dimensions={"correctness": DimLevel.HIGH},
            summary="No evidence",
        )
        bounded = bound_drafts([draft])
        assert len(bounded) == 1
        assert bounded[0].evidence == ()


def _nested_dict(depth: int) -> dict:
    """Build a dict nested to *depth* levels: {"a": {"a": ... {"a": "leaf"}}}."""
    result: dict = {"a": "leaf"}
    for _ in range(depth - 1):
        result = {"a": result}
    return result


def _nested_list(depth: int) -> dict:
    """Build a dict whose value is a list nested *depth* levels."""
    inner: list | str = "leaf"
    for _ in range(depth):
        inner = [inner]
    return {"items": inner}


class TestDepthBounding:
    """Recursion depth limit prevents stack overflow on deep structures."""

    def test_deeply_nested_dict_bounded_without_error(self):
        data = _nested_dict(150)
        e = _evidence(data=data)
        bounded = bound_evidence(e, max_depth=DEFAULT_MAX_DEPTH)
        # Should complete without RecursionError
        assert bounded is not None

    def test_deeply_nested_list_bounded_without_error(self):
        data = _nested_list(150)
        e = _evidence(data=data)
        bounded = bound_evidence(e, max_depth=DEFAULT_MAX_DEPTH)
        assert bounded is not None

    def test_normal_depth_preserved(self):
        data = {"a": {"b": {"c": "value"}}}
        e = _evidence(data=data)
        bounded = bound_evidence(e, max_depth=DEFAULT_MAX_DEPTH)
        assert bounded.data == {"a": {"b": {"c": "value"}}}

    def test_exact_boundary_dict(self):
        """nested(max_depth + 2) is the shallowest structure that triggers
        the sentinel.  The outermost dict is handled by _bound_data (no
        depth cost), then _bound_value starts at depth=0 for each value.
        """
        max_depth = 3
        exceeds = _nested_dict(max_depth + 2)  # 5 dicts total
        e = _evidence(data=exceeds)
        bounded = bound_evidence(e, max_depth=max_depth)

        # Walk through all dict levels to reach the sentinel value.
        # nested(5) produces 5 "a" keys; the 5th value is the sentinel.
        node = bounded.data
        for _ in range(max_depth + 2):
            assert isinstance(node, dict)
            node = node["a"]

        assert node == {"_bounded": True, "_reason": "max depth exceeded"}

    def test_within_limit_not_bounded(self):
        """nested(max_depth + 1) is the deepest structure that passes
        through without any sentinel.
        """
        max_depth = 3
        within = _nested_dict(max_depth + 1)  # 4 dicts total
        e = _evidence(data=within)
        bounded = bound_evidence(e, max_depth=max_depth)

        # Walk all 4 dict levels to reach the leaf string
        node = bounded.data
        for _ in range(max_depth + 1):
            assert isinstance(node, dict)
            node = node["a"]

        assert node == "leaf"

    def test_depth_bounding_sets_truncated_flag(self):
        data = _nested_dict(DEFAULT_MAX_DEPTH + 10)
        e = _evidence(data=data)
        bounded = bound_evidence(e, max_depth=DEFAULT_MAX_DEPTH)
        assert bounded.truncated is True
