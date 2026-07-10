"""Tests for gate artifact type registry."""

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.states import State
from verdx.engine.lifecycle.gates import (
    GATE_ARTIFACTS,
    REJECTION_ARTIFACTS,
    required_artifact,
)


def test_has_10_artifact_types():
    assert len(ArtifactType) == 10


def test_all_artifact_types_present():
    expected = {
        "IntentSummary",
        "SpecPlan",
        "BuildReport",
        "RunAssessment",
        "ReviewDecision",
        "OverrideDecision",
        "ApprovalCarryForward",
        "FastPathAuthorization",
        "FastPathEligibility",
        "IntegrationReceipt",
    }
    assert {a.value for a in ArtifactType} == expected


class TestGateArtifacts:
    def test_clarify_to_spec_requires_intent_summary(self):
        assert required_artifact(State.Clarifying, State.SpecDrafting) == ArtifactType.IntentSummary

    def test_spec_to_approval_requires_spec_plan(self):
        assert (
            required_artifact(State.SpecDrafting, State.AwaitingApproval) == ArtifactType.SpecPlan
        )

    def test_build_to_validate_requires_build_report(self):
        assert required_artifact(State.Building, State.Validating) == ArtifactType.BuildReport

    def test_validate_to_review_requires_run_assessment(self):
        assert (
            required_artifact(State.Validating, State.AwaitingReview) == ArtifactType.RunAssessment
        )

    def test_validate_to_override_requires_run_assessment(self):
        assert (
            required_artifact(State.Validating, State.OverridePending) == ArtifactType.RunAssessment
        )

    def test_override_to_review_requires_override_decision(self):
        assert (
            required_artifact(State.OverridePending, State.AwaitingReview)
            == ArtifactType.OverrideDecision
        )

    def test_review_to_integrate_requires_review_decision(self):
        assert (
            required_artifact(State.AwaitingReview, State.Integrating)
            == ArtifactType.ReviewDecision
        )

    def test_carry_forward_to_integrate_requires_approval_carry_forward(self):
        assert (
            required_artifact(State.NeedsRereview, State.Integrating)
            == ArtifactType.ApprovalCarryForward
        )

    def test_revalidation_to_rereview_requires_run_assessment(self):
        assert (
            required_artifact(State.NeedsRevalidate, State.NeedsRereview)
            == ArtifactType.RunAssessment
        )

    def test_integration_requires_integration_receipt(self):
        assert (
            required_artifact(State.Integrating, State.Integrated)
            == ArtifactType.IntegrationReceipt
        )


class TestRejectionArtifacts:
    def test_review_rejection_to_building(self):
        assert (
            required_artifact(State.AwaitingReview, State.Building) == ArtifactType.ReviewDecision
        )

    def test_review_rejection_to_spec(self):
        assert (
            required_artifact(State.AwaitingReview, State.SpecDrafting)
            == ArtifactType.ReviewDecision
        )

    def test_review_rejection_to_clarify(self):
        assert (
            required_artifact(State.AwaitingReview, State.Clarifying) == ArtifactType.ReviewDecision
        )


class TestNonGatedTransitions:
    def test_approval_to_building_no_gate(self):
        assert required_artifact(State.AwaitingApproval, State.Building) is None

    def test_approval_rejected_no_gate(self):
        assert required_artifact(State.AwaitingApproval, State.SpecDrafting) is None

    def test_build_failed_no_gate(self):
        assert required_artifact(State.Building, State.Building) is None

    def test_blocked_no_gate(self):
        assert required_artifact(State.Building, State.Blocked) is None

    def test_abandoned_no_gate(self):
        assert required_artifact(State.Building, State.Abandoned) is None

    def test_rebase_no_gate(self):
        assert required_artifact(State.NeedsRebase, State.NeedsRevalidate) is None


class TestGateArtifactsCoverage:
    def test_gate_artifacts_covers_all_forward_gates(self):
        assert len(GATE_ARTIFACTS) == 11

    def test_rejection_artifacts_count(self):
        assert len(REJECTION_ARTIFACTS) == 3
