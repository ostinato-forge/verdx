"""Default config loader for the markdown gate."""

from __future__ import annotations

from verdx.contracts.config import DomainConfig, EvidenceLimits
from verdx.contracts.dimensions import DimLevel, EscalationLevel
from verdx.contracts.escalation import (
    EscalationFloor,
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)


def load_default_markdown() -> DomainConfig:
    """Return a minimal valid DomainConfig for the markdown gate."""
    policy = EscalationPolicy(
        version=1,
        floors=(EscalationFloor(category="structure", minimum=EscalationLevel.L1),),
        rules=(
            EscalationRule(
                name="high-finding",
                match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
                level=EscalationLevel.L3,
            ),
            EscalationRule(
                name="fallback",
                match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                level=EscalationLevel.L0,
            ),
        ),
    )
    return DomainConfig(
        version=1,
        policy=policy,
        critical_validators=frozenset(),
        dimensions=("structure", "links", "style"),
        evidence_limits=EvidenceLimits(
            max_evidence_bytes=65536,
            max_string_length=4096,
            max_data_entries=50,
        ),
        timeout_seconds=300,
        dimension_map=None,
        scoring_config=None,
    )
