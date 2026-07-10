"""Focused stability tests for small public core contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.assessment import AssessmentConfidence, Trend
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION, Disposition
from verdx.contracts.independence import IndependenceRecord
from verdx.engine.runtime.store.dispositions import DispositionStore
from verdx.engine.runtime.store.history import _target_key_hash

if TYPE_CHECKING:
    from pathlib import Path


def _disposition(**overrides: object) -> Disposition:
    defaults: dict[str, object] = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "issue_fingerprint": "fp-001",
        "decision": "accepted-risk",
        "reason": "Known course-context exception",
        "team": "training-content",
        "timestamp": "2026-05-01T00:00:00Z",
    }
    return Disposition(**(defaults | overrides))  # type: ignore[arg-type]


class TestCoreEnumStability:
    def test_dimension_enum_values_are_ordered_and_stable(self) -> None:
        assert [member.value for member in Severity] == [
            "critical",
            "high",
            "medium",
            "low",
            "info",
        ]
        assert [member.value for member in Confidence] == [
            "confirmed",
            "probable",
            "possible",
        ]
        assert [member.value for member in DimLevel] == [
            "LOW",
            "MEDIUM",
            "HIGH",
            "BLOCKING",
        ]
        assert [member.value for member in EscalationLevel] == [
            "L0",
            "L1",
            "L2",
            "L3",
            "L4",
        ]
        assert [member.value for member in CoverageStatus] == [
            "full",
            "partial",
            "degraded",
        ]

    def test_assessment_enum_values_are_ordered_and_stable(self) -> None:
        assert [member.value for member in AssessmentConfidence] == [
            "strong",
            "guarded",
            "low",
        ]
        assert [member.value for member in Trend] == [
            "improving",
            "stable",
            "regressing",
        ]


class TestIndependenceRecordStability:
    def test_serialized_shape_is_exact_and_privacy_safe(self) -> None:
        record = IndependenceRecord(
            producer_role="content-author",
            validator_role="training-security",
            actor_kind="agent",
            relation="independent",
            policy_id="verdx-v1",
            result="satisfied",
        )

        assert record.to_dict() == {
            "producer_role": "content-author",
            "validator_role": "training-security",
            "actor_kind": "agent",
            "relation": "independent",
            "policy_id": "verdx-v1",
            "result": "satisfied",
        }
        assert "actor_id" not in record.to_dict()
        assert "team" not in record.to_dict()
        assert IndependenceRecord.from_dict(record.to_dict()) == record


class TestDispositionTimezoneStability:
    def test_expiry_compares_non_utc_offsets_by_instant(self, tmp_path: Path) -> None:
        store = DispositionStore(tmp_path)
        target_hash = _target_key_hash("target-A")
        store.append(
            target_hash,
            _disposition(
                issue_fingerprint="fp-offset",
                expires="2026-05-01T03:30:00+03:00",
            ),
        )

        before_expiry = store.get_active(target_hash, "2026-05-01T00:29:59Z")
        at_expiry = store.get_active(target_hash, "2026-04-30T17:30:00-07:00")

        assert "fp-offset" in before_expiry
        assert "fp-offset" not in at_expiry

    def test_latest_expired_entry_does_not_hide_prior_active_entry(
        self,
        tmp_path: Path,
    ) -> None:
        store = DispositionStore(tmp_path)
        target_hash = _target_key_hash("target-A")
        store.append(
            target_hash,
            _disposition(
                issue_fingerprint="fp-repeat",
                decision="accepted-risk",
                timestamp="2026-04-01T00:00:00Z",
                expires="2026-12-31T00:00:00Z",
            ),
        )
        store.append(
            target_hash,
            _disposition(
                issue_fingerprint="fp-repeat",
                decision="false-positive",
                timestamp="2026-04-15T00:00:00Z",
                expires="2026-04-30T00:00:00Z",
            ),
        )

        active = store.get_active(target_hash, "2026-05-01T00:00:00Z")

        assert active["fp-repeat"].decision == "accepted-risk"

    def test_expiring_soon_uses_timezone_aware_instants(self, tmp_path: Path) -> None:
        store = DispositionStore(tmp_path)
        target_hash = _target_key_hash("target-A")
        store.append(
            target_hash,
            _disposition(
                issue_fingerprint="fp-expiring",
                expires="2026-05-02T01:00:00+02:00",
            ),
        )

        expiring = store.get_expiring_soon(
            target_hash,
            "2026-05-01T00:00:00Z",
            horizon_days=1,
        )

        assert [disp.issue_fingerprint for disp in expiring] == ["fp-expiring"]
