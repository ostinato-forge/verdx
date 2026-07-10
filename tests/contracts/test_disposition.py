"""Tests for the Disposition contract model."""

from __future__ import annotations

import pytest

from verdx.contracts.disposition import (
    DISPOSITION_SCHEMA_VERSION,
    VALID_DECISIONS,
    Disposition,
)


def _disposition(**overrides) -> Disposition:
    defaults = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "issue_fingerprint": "qu:abc123",
        "decision": "accepted-risk",
        "reason": "low-impact finding",
        "team": "training-team",
        "timestamp": "2026-05-01T12:00:00Z",
    }
    return Disposition(**(defaults | overrides))


class TestDispositionValidation:
    def test_valid_disposition(self):
        d = _disposition()
        assert d.schema_version == DISPOSITION_SCHEMA_VERSION
        assert d.decision == "accepted-risk"

    def test_wrong_schema_version_rejected(self):
        with pytest.raises(ValueError, match="schema_version"):
            _disposition(schema_version="wrong.v1")

    def test_empty_fingerprint_rejected(self):
        with pytest.raises(ValueError, match="issue_fingerprint"):
            _disposition(issue_fingerprint="")

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValueError, match="decision"):
            _disposition(decision="ignored")

    def test_empty_reason_rejected(self):
        with pytest.raises(ValueError, match="reason"):
            _disposition(reason="")

    def test_empty_team_rejected(self):
        with pytest.raises(ValueError, match="team"):
            _disposition(team="")

    def test_empty_timestamp_rejected(self):
        with pytest.raises(ValueError, match="timestamp"):
            _disposition(timestamp="")

    def test_all_valid_decisions_accepted(self):
        for decision in sorted(VALID_DECISIONS):
            d = _disposition(decision=decision)
            assert d.decision == decision

    def test_expires_optional(self):
        d = _disposition()
        assert d.expires is None

    def test_expires_set(self):
        d = _disposition(expires="2026-12-31T23:59:59Z")
        assert d.expires == "2026-12-31T23:59:59Z"


class TestDispositionRoundTrip:
    def test_to_dict_without_expires(self):
        d = _disposition()
        result = d.to_dict()
        assert result["schema_version"] == DISPOSITION_SCHEMA_VERSION
        assert result["issue_fingerprint"] == "qu:abc123"
        assert result["decision"] == "accepted-risk"
        assert "expires" not in result

    def test_to_dict_with_expires(self):
        d = _disposition(expires="2026-12-31T23:59:59Z")
        result = d.to_dict()
        assert result["expires"] == "2026-12-31T23:59:59Z"

    def test_round_trip_no_expires(self):
        d = _disposition()
        restored = Disposition.from_dict(d.to_dict())
        assert restored == d

    def test_round_trip_with_expires(self):
        d = _disposition(expires="2027-01-01T00:00:00Z")
        restored = Disposition.from_dict(d.to_dict())
        assert restored == d

    def test_from_dict_missing_expires_defaults_none(self):
        data = _disposition().to_dict()
        assert "expires" not in data
        restored = Disposition.from_dict(data)
        assert restored.expires is None
