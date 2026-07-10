"""Tests for core dimension enums."""

from __future__ import annotations

from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)


class TestSeverity:
    def test_members(self):
        assert {s.name for s in Severity} == {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
            "INFO",
        }

    def test_values_lowercase(self):
        for s in Severity:
            assert s.value == s.name.lower()

    def test_ordering_by_value(self):
        ordered = [s.value for s in Severity]
        assert ordered == ["critical", "high", "medium", "low", "info"]


class TestConfidence:
    def test_members(self):
        assert {c.name for c in Confidence} == {
            "CONFIRMED",
            "PROBABLE",
            "POSSIBLE",
        }

    def test_values_lowercase(self):
        for c in Confidence:
            assert c.value == c.name.lower()


class TestDimLevel:
    def test_members(self):
        assert {d.name for d in DimLevel} == {"LOW", "MEDIUM", "HIGH", "BLOCKING"}

    def test_values_uppercase(self):
        for d in DimLevel:
            assert d.value == d.name


class TestEscalationLevel:
    def test_members(self):
        assert {e.name for e in EscalationLevel} == {"L0", "L1", "L2", "L3", "L4"}

    def test_values_match_names(self):
        for e in EscalationLevel:
            assert e.value == e.name

    def test_ordering(self):
        ordered = [e.value for e in EscalationLevel]
        assert ordered == ["L0", "L1", "L2", "L3", "L4"]


class TestCoverageStatus:
    def test_members(self):
        assert {c.name for c in CoverageStatus} == {"FULL", "PARTIAL", "DEGRADED"}

    def test_values_lowercase(self):
        for c in CoverageStatus:
            assert c.value == c.name.lower()

    def test_lookup_by_value(self):
        assert CoverageStatus("full") == CoverageStatus.FULL
        assert CoverageStatus("partial") == CoverageStatus.PARTIAL
        assert CoverageStatus("degraded") == CoverageStatus.DEGRADED
