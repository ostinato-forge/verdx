"""Flywheel data types — corrections, patterns, and annotations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Correction:
    """Record of a developer overriding verdx's assessment."""

    observation_signature: str
    target_key: str
    initial_severity: str
    final_decision: str
    description: str
    domain: str
    timestamp: str


@dataclass(slots=True)
class Pattern:
    """A learned symptom-diagnosis-resolution template."""

    id: str
    symptoms: list[str]
    diagnosis: str
    resolution: str
    category: str
    domain: str
    seen_count: int
    observation_signatures: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    source: str = ""
    created: str = ""
    updated: str = ""


@dataclass(frozen=True, slots=True)
class Annotation:
    """Flywheel context attached to an observation."""

    observation_signature: str
    pattern_id: str | None
    match_score: float
    suggested_decision: str | None
    confidence: str
    rationale: str
