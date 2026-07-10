"""Gate artifact type registry — maps artifact types to the transitions they gate."""

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.events import EventType
from verdx.contracts.states import State

GATE_ARTIFACTS: dict[tuple[State | None, State], ArtifactType] = {
    # Clarify -> Spec requires IntentSummary
    (State.Clarifying, State.SpecDrafting): ArtifactType.IntentSummary,
    # Spec -> Approve requires SpecPlan
    (State.SpecDrafting, State.AwaitingApproval): ArtifactType.SpecPlan,
    # Build -> Validate requires BuildReport
    (State.Building, State.Validating): ArtifactType.BuildReport,
    # Validate -> Review requires RunAssessment
    (State.Validating, State.AwaitingReview): ArtifactType.RunAssessment,
    # Validate -> Override requires RunAssessment (with blocking failures)
    (State.Validating, State.OverridePending): ArtifactType.RunAssessment,
    # Override -> Review requires OverrideDecision
    (State.OverridePending, State.AwaitingReview): ArtifactType.OverrideDecision,
    # Review -> Integrate requires ReviewDecision (default for this state pair)
    (State.AwaitingReview, State.Integrating): ArtifactType.ReviewDecision,
    # Re-review carry-forward -> Integrate requires ApprovalCarryForward
    (State.NeedsRereview, State.Integrating): ArtifactType.ApprovalCarryForward,
    # Revalidation completed requires RunAssessment (v2)
    (State.NeedsRevalidate, State.NeedsRereview): ArtifactType.RunAssessment,
    (State.NeedsRevalidate, State.AwaitingReview): ArtifactType.RunAssessment,
    # Integration completed requires IntegrationReceipt
    (State.Integrating, State.Integrated): ArtifactType.IntegrationReceipt,
}

# Event-specific overrides for transitions where (from, to) is ambiguous
_EVENT_ARTIFACT_OVERRIDES: dict[EventType, ArtifactType] = {
    EventType.fast_path_authorized: ArtifactType.FastPathAuthorization,
    EventType.fast_path_eligible: ArtifactType.FastPathEligibility,
}

# Review rejections require ReviewDecision (with reason tags)
REJECTION_ARTIFACTS: dict[tuple[State, State], ArtifactType] = {
    (State.AwaitingReview, State.Building): ArtifactType.ReviewDecision,
    (State.AwaitingReview, State.SpecDrafting): ArtifactType.ReviewDecision,
    (State.AwaitingReview, State.Clarifying): ArtifactType.ReviewDecision,
}


def required_artifact(
    from_state: State | None,
    to_state: State,
    event_type: EventType | None = None,
) -> ArtifactType | None:
    if event_type is not None and event_type in _EVENT_ARTIFACT_OVERRIDES:
        return _EVENT_ARTIFACT_OVERRIDES[event_type]
    if from_state is None:
        return None
    key = (from_state, to_state)
    return GATE_ARTIFACTS.get(key) or REJECTION_ARTIFACTS.get(key)
