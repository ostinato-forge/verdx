"""Contract surface completeness tests.

Verifies that verdx.contracts exports the curated public API
and that domain consumers can import exclusively from it.
"""

from __future__ import annotations

import verdx.contracts as contracts


def test_contracts_all_is_complete() -> None:
    expected = {
        "ActionItem",
        "AppliedDisposition",
        "AwarenessItem",
        "Confidence",
        "ConfigError",
        "CoverageGap",
        "CoverageStatus",
        "DimLevel",
        "Disposition",
        "DomainConfig",
        "DomainSpec",
        "EscalationFloor",
        "EscalationLevel",
        "EscalationPolicy",
        "EscalationRule",
        "Evidence",
        "EvidenceLimits",
        "EvidenceSummary",
        "EvidenceTruncation",
        "ExpectedCoverageSubject",
        "GateEvidence",
        "IndependenceRecord",
        "Observation",
        "ObservationDraft",
        "RecoveryProjection",
        "RuleMatch",
        "RunAssessment",
        "SUBJECT_SCHEMA_VERSION",
        "ScoringConfig",
        "Severity",
        "SubjectDiscoveryResult",
        "SubjectModelHealth",
        "SubjectResolver",
        "SubjectSelector",
        "ValidationContext",
        "ValidationSubject",
        "Validator",
        "ValidatorResult",
        "ValidatorStatus",
        "check_from_validator",
        "derive_subject_key",
        "iter_target_files",
        "resolve_within",
    }
    assert set(contracts.__all__) == expected


def test_all_exports_are_importable() -> None:
    for name in contracts.__all__:
        obj = getattr(contracts, name)
        assert obj is not None, f"{name} resolved to None"


def test_domain_config_is_the_contracts_type() -> None:
    from verdx.contracts.config import DomainConfig as DirectImport

    assert contracts.DomainConfig is DirectImport


def test_escalation_policy_is_the_contracts_type() -> None:
    from verdx.contracts.escalation import EscalationPolicy as DirectImport

    assert contracts.EscalationPolicy is DirectImport


def test_backward_compat_runtime_config_reexport() -> None:
    from verdx.engine.runtime.config import DomainConfig as RuntimeImport

    assert RuntimeImport is contracts.DomainConfig


def test_backward_compat_validation_escalation_reexport() -> None:
    from verdx.engine.validation.escalation import EscalationPolicy as ValidationImport

    assert ValidationImport is contracts.EscalationPolicy
