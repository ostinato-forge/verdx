"""The single verdict gate for an assessment.

An assessment passes only with an adequate score, coverage that is not degraded,
and confidence that is not low. Shared by the porcelain translation layer and
the engine CLI so the two surfaces always agree on pass/block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus

if TYPE_CHECKING:
    from verdx.contracts.assessment import RunAssessment


def passes_gate(assessment: RunAssessment, threshold: int) -> bool:
    """Return True if the assessment clears the gate at the given threshold.

    The gate is intentionally stricter than score alone: a high score with
    degraded coverage or low confidence does not pass, because the verdict
    cannot be trusted.
    """
    return (
        assessment.score.overall >= threshold
        and assessment.coverage_status in (CoverageStatus.FULL, CoverageStatus.PARTIAL)
        and assessment.confidence != AssessmentConfidence.LOW
    )
