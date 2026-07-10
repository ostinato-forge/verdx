"""Tests for porcelain types — construction, validation, immutability."""

from __future__ import annotations

import pytest

from verdx.types import Assessment, Observation, Override


class TestObservation:
    def test_construction(self) -> None:
        obs = Observation(
            severity="high",
            category="structure",
            location="content/ch01/ge.adoc:5",
            message="Missing outcomes section",
            fixable=False,
            signature="abc123",
        )
        assert obs.severity == "high"
        assert obs.message == "Missing outcomes section"

    def test_none_location(self) -> None:
        obs = Observation(
            severity="low",
            category="style",
            location=None,
            message="Minor style issue",
            fixable=False,
            signature="def456",
        )
        assert obs.location is None

    def test_invalid_severity(self) -> None:
        with pytest.raises(ValueError, match="severity must be one of"):
            Observation(
                severity="info",
                category="test",
                location=None,
                message="test",
                fixable=False,
                signature="sig",
            )

    def test_empty_category(self) -> None:
        with pytest.raises(ValueError, match="category must be non-empty"):
            Observation(
                severity="high",
                category="",
                location=None,
                message="test",
                fixable=False,
                signature="sig",
            )

    def test_empty_message(self) -> None:
        with pytest.raises(ValueError, match="message must be non-empty"):
            Observation(
                severity="high",
                category="cat",
                location=None,
                message="",
                fixable=False,
                signature="sig",
            )

    def test_empty_signature(self) -> None:
        with pytest.raises(ValueError, match="signature must be non-empty"):
            Observation(
                severity="high",
                category="cat",
                location=None,
                message="msg",
                fixable=False,
                signature="",
            )

    def test_frozen(self) -> None:
        obs = Observation(
            severity="high",
            category="cat",
            location=None,
            message="msg",
            fixable=False,
            signature="sig",
        )
        with pytest.raises(AttributeError):
            obs.severity = "low"  # type: ignore[misc]


class TestOverride:
    def test_construction(self) -> None:
        ov = Override(
            signature="abc123",
            decision="false-positive",
            justification="Not applicable in this context",
            by="eparenti",
            date="2026-06-19",
        )
        assert ov.decision == "false-positive"
        assert ov.expires is None

    def test_with_expiry(self) -> None:
        ov = Override(
            signature="abc123",
            decision="deferred",
            justification="Will fix next sprint",
            by="eparenti",
            date="2026-06-19",
            expires="2026-07-01",
        )
        assert ov.expires == "2026-07-01"

    def test_invalid_decision(self) -> None:
        with pytest.raises(ValueError, match="decision must be one of"):
            Override(
                signature="sig",
                decision="wontfix",
                justification="just because",
                by="me",
                date="2026-01-01",
            )

    def test_empty_justification(self) -> None:
        with pytest.raises(ValueError, match="justification must be non-empty"):
            Override(
                signature="sig",
                decision="false-positive",
                justification="",
                by="me",
                date="2026-01-01",
            )


class TestAssessment:
    def _make(self, **kwargs: object) -> Assessment:
        defaults = {
            "score": 85,
            "verdict": "pass",
            "observations": (),
            "decision_hash": "abc123def456",
            "overrides_applied": (),
            "validators_run": ("v1", "v2"),
            "metadata": {"target": "test", "profile": "standard"},
        }
        defaults.update(kwargs)
        return Assessment(**defaults)  # type: ignore[arg-type]

    def test_construction(self) -> None:
        a = self._make()
        assert a.score == 85
        assert a.verdict == "pass"

    def test_score_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="score must be 0-100"):
            self._make(score=101)

    def test_negative_score(self) -> None:
        with pytest.raises(ValueError, match="score must be 0-100"):
            self._make(score=-1)

    def test_invalid_verdict(self) -> None:
        with pytest.raises(ValueError, match="verdict must be"):
            self._make(verdict="warn")

    def test_empty_hash(self) -> None:
        with pytest.raises(ValueError, match="decision_hash must be non-empty"):
            self._make(decision_hash="")

    def test_zero_score_valid(self) -> None:
        a = self._make(score=0, verdict="block")
        assert a.score == 0

    def test_hundred_score_valid(self) -> None:
        a = self._make(score=100)
        assert a.score == 100
