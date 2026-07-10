"""Tests for output formatting — text and JSON renderers."""

from __future__ import annotations

import json

from verdx._output import format_json, format_text
from verdx.types import Assessment, Observation, Override


def _make_assessment(**kwargs: object) -> Assessment:
    defaults = {
        "score": 85,
        "verdict": "pass",
        "observations": (),
        "decision_hash": "a" * 64,
        "overrides_applied": (),
        "validators_run": ("v1", "v2"),
        "metadata": {"target": "test", "profile": "standard"},
    }
    defaults.update(kwargs)
    return Assessment(**defaults)  # type: ignore[arg-type]


class TestFormatJson:
    def test_valid_json(self) -> None:
        a = _make_assessment()
        output = format_json(a)
        data = json.loads(output)
        assert data["score"] == 85
        assert data["verdict"] == "pass"

    def test_observations_included(self) -> None:
        obs = Observation(
            severity="high",
            category="structure",
            location="file:10",
            message="Problem found",
            fixable=False,
            signature="sig1",
        )
        a = _make_assessment(observations=(obs,))
        data = json.loads(format_json(a))
        assert len(data["observations"]) == 1
        assert data["observations"][0]["severity"] == "high"

    def test_overrides_included(self) -> None:
        ov = Override(
            signature="sig1",
            decision="false-positive",
            justification="Not real",
            by="me",
            date="2026-01-01",
        )
        a = _make_assessment(overrides_applied=(ov,))
        data = json.loads(format_json(a))
        assert len(data["overrides_applied"]) == 1


class TestFormatText:
    def test_score_line(self) -> None:
        a = _make_assessment(score=85, verdict="pass")
        output = format_text(a)
        assert "85/100" in output
        assert "PASS" in output

    def test_block_verdict(self) -> None:
        a = _make_assessment(score=60, verdict="block")
        output = format_text(a)
        assert "BLOCK" in output

    def test_findings_section(self) -> None:
        obs = Observation(
            severity="critical",
            category="structure",
            location="file.adoc:5",
            message="Missing required section",
            fixable=False,
            signature="sig1",
        )
        a = _make_assessment(observations=(obs,))
        output = format_text(a)
        assert "Findings (1)" in output
        assert "CRITICAL" in output
        assert "Missing required section" in output
        assert "file.adoc:5" in output

    def test_no_findings_no_section(self) -> None:
        a = _make_assessment()
        output = format_text(a)
        assert "Findings" not in output

    def test_hash_shown(self) -> None:
        a = _make_assessment()
        output = format_text(a)
        assert "Hash:" in output

    def test_validators_count(self) -> None:
        a = _make_assessment(validators_run=("v1", "v2", "v3"))
        output = format_text(a)
        assert "Validators: 3" in output

    def test_overrides_shown(self) -> None:
        ov = Override(
            signature="abc123def456",
            decision="false-positive",
            justification="Not applicable",
            by="me",
            date="2026-01-01",
        )
        a = _make_assessment(overrides_applied=(ov,))
        output = format_text(a)
        assert "Overrides applied: 1" in output
        assert "false-positive" in output
