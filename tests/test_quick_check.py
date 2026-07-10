"""Tests for porcelain quick_check()."""

from __future__ import annotations

from unittest.mock import patch

from verdx.api import quick_check
from verdx.types import Assessment, Observation


def _obs(signature: str, message: str = "finding") -> Observation:
    return Observation(
        severity="medium",
        category="correctness",
        location="file.yml:1",
        message=message,
        fixable=False,
        signature=signature,
    )


def _assessment(*signatures: str, score: int = 80) -> Assessment:
    observations = tuple(_obs(sig) for sig in signatures)
    return Assessment(
        score=score,
        verdict="pass" if score >= 80 else "block",
        observations=observations,
        decision_hash="hash-test",
        overrides_applied=(),
        validators_run=("test-validator",),
        metadata={"target": "test"},
    )


class TestPorcelainQuickCheck:
    def test_returns_only_new_findings(self) -> None:
        baseline = _assessment("sig-a", "sig-b")
        current = _assessment("sig-b", "sig-c")

        with patch("verdx.api.validate", return_value=current):
            result = quick_check("/tmp", baseline)

        sigs = {obs.signature for obs in result.observations}
        assert sigs == {"sig-c"}

    def test_metadata_has_counts(self) -> None:
        baseline = _assessment("sig-a", "sig-b")
        current = _assessment("sig-b", "sig-c")

        with patch("verdx.api.validate", return_value=current):
            result = quick_check("/tmp", baseline)

        qc = result.metadata["quick_check"]
        assert qc["new_count"] == 1
        assert qc["resolved_count"] == 1
        assert qc["persistent_count"] == 1

    def test_no_changes_empty_observations(self) -> None:
        baseline = _assessment("sig-a", "sig-b")
        current = _assessment("sig-a", "sig-b")

        with patch("verdx.api.validate", return_value=current):
            result = quick_check("/tmp", baseline)

        assert result.observations == ()
        assert result.metadata["quick_check"]["new_count"] == 0

    def test_all_resolved_no_new(self) -> None:
        baseline = _assessment("sig-a", "sig-b")
        current = _assessment()

        with patch("verdx.api.validate", return_value=current):
            result = quick_check("/tmp", baseline)

        assert result.observations == ()
        qc = result.metadata["quick_check"]
        assert qc["resolved_count"] == 2
        assert qc["new_count"] == 0

    def test_preserves_score_and_verdict(self) -> None:
        baseline = _assessment("sig-a", score=60)
        current = _assessment("sig-a", "sig-b", score=45)

        with patch("verdx.api.validate", return_value=current):
            result = quick_check("/tmp", baseline)

        assert result.score == 45
        assert result.verdict == "block"
