"""Public contract surface for verdx.

Domain gates should import from ``verdx.contracts``
rather than reaching into engine internals.

Organized by use-case:

- **Domain authoring:** DomainSpec, DomainConfig, SubjectDiscoveryResult, etc.
- **Validator authoring:** Validator, ValidationContext, ValidatorResult, etc.
- **Observation types:** Evidence, ObservationDraft, Observation
- **Dimensions/enums:** Severity, Confidence, DimLevel, EscalationLevel, etc.
- **Assessment & evidence:** RunAssessment, GateEvidence, etc.
- **Escalation policy:** EscalationPolicy, EscalationRule, etc.
- **Config types:** DomainConfig, ConfigError, EvidenceLimits, ScoringConfig
- **Subject model:** ValidationSubject, SubjectSelector, etc.
"""

# --- Check authoring ---
# --- Assessment ---
from verdx.contracts.assessment import (
    CoverageGap,
    RunAssessment,
    ValidatorStatus,
)
from verdx.contracts.check import check_from_validator

# --- Configuration ---
from verdx.contracts.config import (
    ConfigError,
    DomainConfig,
    EvidenceLimits,
    ScoringConfig,
)

# --- Decision dimensions ---
from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)

# --- Disposition ---
from verdx.contracts.disposition import Disposition
from verdx.contracts.domain import (
    DomainSpec,
    SubjectDiscoveryResult,
    SubjectResolver,
)

# --- Escalation policy ---
from verdx.contracts.escalation import (
    EscalationFloor,
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

# --- Agent-facing evidence ---
from verdx.contracts.evidence import (
    ActionItem,
    AwarenessItem,
    EvidenceSummary,
    EvidenceTruncation,
    GateEvidence,
    RecoveryProjection,
)

# --- Independence ---
from verdx.contracts.independence import IndependenceRecord

# --- Observation types ---
from verdx.contracts.observation import (
    AppliedDisposition,
    Evidence,
    Observation,
    ObservationDraft,
)

# --- Path safety (gate-author helpers) ---
from verdx.contracts.paths import iter_target_files, resolve_within

# --- Subject model ---
from verdx.contracts.subjects import (
    SUBJECT_SCHEMA_VERSION,
    ExpectedCoverageSubject,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
    derive_subject_key,
)

# --- Validator authoring ---
from verdx.contracts.validator import (
    ValidationContext,
    Validator,
    ValidatorResult,
)

__all__ = [
    "SUBJECT_SCHEMA_VERSION",
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
]
