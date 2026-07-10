"""Artifact types for gate transitions in the intent-fulfillment cycle."""

from enum import StrEnum


class ArtifactType(StrEnum):
    IntentSummary = "IntentSummary"
    SpecPlan = "SpecPlan"
    BuildReport = "BuildReport"
    RunAssessment = "RunAssessment"
    ReviewDecision = "ReviewDecision"
    OverrideDecision = "OverrideDecision"
    ApprovalCarryForward = "ApprovalCarryForward"
    FastPathAuthorization = "FastPathAuthorization"
    FastPathEligibility = "FastPathEligibility"
    IntegrationReceipt = "IntegrationReceipt"
