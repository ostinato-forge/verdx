"""Tests for the pure state reducer."""

from datetime import UTC, datetime

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.events import ArtifactRefs, EventType, StateEvent
from verdx.contracts.roles import Role
from verdx.contracts.states import State
from verdx.engine.lifecycle.reducer import IntentState, apply, reduce


def _event(
    *,
    event_type: EventType,
    previous_state: State | None,
    next_state: State,
    role: Role = Role.implementer,
    artifact_refs: ArtifactRefs | None = None,
    base_commit: str = "abc1234",
) -> StateEvent:
    return StateEvent(
        intent_id="TEST-001",
        event_type=event_type,
        previous_state=previous_state,
        next_state=next_state,
        actor="tester",
        authority_role=role,
        timestamp=datetime.now(UTC),
        base_commit=base_commit,
        policy_version="ptl-v1",
        artifact_refs=artifact_refs or ArtifactRefs(),
    )


class TestReduceHappyPath:
    def test_intent_creation(self):
        intent = IntentState(intent_id="TEST-001")
        event = _event(
            event_type=EventType.intent_created,
            previous_state=None,
            next_state=State.Clarifying,
            role=Role.requester,
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.new_state == State.Clarifying
        assert result.violations == ()

    def test_clarify_to_spec_with_artifact(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.clarify_completed,
            previous_state=State.Clarifying,
            next_state=State.SpecDrafting,
            role=Role.requester,
            artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.gate_artifact == ArtifactType.IntentSummary
        assert result.gate_satisfied

    def test_build_to_validate_with_artifact(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Building)
        event = _event(
            event_type=EventType.build_completed,
            previous_state=State.Building,
            next_state=State.Validating,
            artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.gate_artifact == ArtifactType.BuildReport
        assert result.gate_satisfied


class TestReduceViolations:
    def test_state_mismatch(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Building)
        event = _event(
            event_type=EventType.build_completed,
            previous_state=State.Clarifying,
            next_state=State.Validating,
            artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("previous_state" in v for v in result.violations)

    def test_invalid_transition(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.build_completed,
            previous_state=State.Clarifying,
            next_state=State.Validating,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("No valid transition" in v for v in result.violations)

    def test_role_not_permitted(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.AwaitingApproval)
        event = _event(
            event_type=EventType.approval_granted,
            previous_state=State.AwaitingApproval,
            next_state=State.Building,
            role=Role.implementer,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("not permitted" in v for v in result.violations)

    def test_missing_gate_artifact(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.clarify_completed,
            previous_state=State.Clarifying,
            next_state=State.SpecDrafting,
            role=Role.requester,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert result.gate_artifact == ArtifactType.IntentSummary
        assert not result.gate_satisfied


class TestTruthTableEnforcement:
    def test_wrong_next_state_rejected(self):
        """C1: reducer must reject events that declare a next_state disagreeing with truth table."""
        intent = IntentState(intent_id="TEST-001", current_state=State.AwaitingApproval)
        event = _event(
            event_type=EventType.approval_granted,
            previous_state=State.AwaitingApproval,
            next_state=State.Abandoned,
            role=Role.scope_owner,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("truth table says" in v for v in result.violations)

    def test_correct_next_state_accepted(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.AwaitingApproval)
        event = _event(
            event_type=EventType.approval_granted,
            previous_state=State.AwaitingApproval,
            next_state=State.Building,
            role=Role.scope_owner,
        )
        result = reduce(intent, event)
        assert result.valid

    def test_fast_path_requires_fast_path_artifact(self):
        """M1: fast_path_authorized requires FastPathAuthorization, not ReviewDecision."""
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.AwaitingReview,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
        )
        event = _event(
            event_type=EventType.fast_path_authorized,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.tech_lead,
            artifact_refs=ArtifactRefs(created=("FastPathAuthorization:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.gate_artifact == ArtifactType.FastPathAuthorization
        assert result.gate_satisfied


class TestReduceBlocked:
    def test_blocked_from_active_state(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Building)
        event = _event(
            event_type=EventType.blocked,
            previous_state=State.Building,
            next_state=State.Blocked,
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.new_state == State.Blocked

    def test_unblocked_restores_state(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Blocked,
            pre_block_state=State.Building,
        )
        event = _event(
            event_type=EventType.unblocked,
            previous_state=State.Blocked,
            next_state=State.Building,
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.new_state == State.Building

    def test_unblocked_wrong_restore_state(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Blocked,
            pre_block_state=State.Building,
        )
        event = _event(
            event_type=EventType.unblocked,
            previous_state=State.Blocked,
            next_state=State.Clarifying,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("restore to" in v for v in result.violations)

    def test_unblocked_without_pre_block_state(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Blocked,
            pre_block_state=None,
        )
        event = _event(
            event_type=EventType.unblocked,
            previous_state=State.Blocked,
            next_state=State.Building,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("No pre-block state" in v for v in result.violations)


class TestApply:
    def test_apply_updates_intent_state(self):
        intent = IntentState(intent_id="TEST-001")
        event = _event(
            event_type=EventType.intent_created,
            previous_state=None,
            next_state=State.Clarifying,
            role=Role.requester,
        )
        result = apply(intent, event)
        assert result.valid
        assert intent.current_state == State.Clarifying
        assert intent.event_count == 1
        assert intent.base_commit == "abc1234"

    def test_apply_does_not_update_on_violation(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.build_completed,
            previous_state=State.Clarifying,
            next_state=State.Validating,
        )
        result = apply(intent, event)
        assert not result.valid
        assert intent.current_state == State.Clarifying
        assert intent.event_count == 0

    def test_apply_tracks_blocked_pre_block_state(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Building)
        event = _event(
            event_type=EventType.blocked,
            previous_state=State.Building,
            next_state=State.Blocked,
        )
        apply(intent, event)
        assert intent.pre_block_state == State.Building
        assert intent.current_state == State.Blocked

    def test_apply_clears_pre_block_on_unblock(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Blocked,
            pre_block_state=State.Building,
        )
        event = _event(
            event_type=EventType.unblocked,
            previous_state=State.Blocked,
            next_state=State.Building,
        )
        apply(intent, event)
        assert intent.pre_block_state is None
        assert intent.current_state == State.Building

    def test_apply_accumulates_artifacts(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.clarify_completed,
            previous_state=State.Clarifying,
            next_state=State.SpecDrafting,
            role=Role.requester,
            artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
        )
        apply(intent, event)
        assert "IntentSummary:TEST-001/v1" in intent.artifacts_created

    def test_apply_sequential_events(self):
        intent = IntentState(intent_id="TEST-001")

        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.spec_completed,
                previous_state=State.SpecDrafting,
                next_state=State.AwaitingApproval,
                artifact_refs=ArtifactRefs(created=("SpecPlan:TEST-001/v1",)),
            ),
        ]

        for event in events:
            result = apply(intent, event)
            assert result.valid

        assert intent.current_state == State.AwaitingApproval
        assert intent.event_count == 3


def _advance_to_integrating(commit: str = "abc1234") -> IntentState:
    """Build an IntentState at Integrating with all gates passed at the given commit."""
    intent = IntentState(intent_id="TEST-001")
    events = [
        _event(
            event_type=EventType.intent_created,
            previous_state=None,
            next_state=State.Clarifying,
            role=Role.requester,
            base_commit=commit,
        ),
        _event(
            event_type=EventType.clarify_completed,
            previous_state=State.Clarifying,
            next_state=State.SpecDrafting,
            role=Role.requester,
            artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            base_commit=commit,
        ),
        _event(
            event_type=EventType.spec_completed,
            previous_state=State.SpecDrafting,
            next_state=State.AwaitingApproval,
            artifact_refs=ArtifactRefs(created=("SpecPlan:TEST-001/v1",)),
            base_commit=commit,
        ),
        _event(
            event_type=EventType.approval_granted,
            previous_state=State.AwaitingApproval,
            next_state=State.Building,
            role=Role.scope_owner,
            base_commit=commit,
        ),
        _event(
            event_type=EventType.build_completed,
            previous_state=State.Building,
            next_state=State.Validating,
            artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
            base_commit=commit,
        ),
        _event(
            event_type=EventType.validation_completed,
            previous_state=State.Validating,
            next_state=State.AwaitingReview,
            artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
            base_commit=commit,
        ),
        _event(
            event_type=EventType.review_submitted,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.reviewer,
            artifact_refs=ArtifactRefs(created=("ReviewDecision:TEST-001/v1",)),
            base_commit=commit,
        ),
    ]
    for event in events:
        result = apply(intent, event)
        assert result.valid, f"Setup failed: {result.violations}"
    return intent


class TestStaleness:
    def test_validated_at_commit_set_on_validation_completed(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Validating)
        event = _event(
            event_type=EventType.validation_completed,
            previous_state=State.Validating,
            next_state=State.AwaitingReview,
            artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
            base_commit="aaa1111",
        )
        apply(intent, event)
        assert intent.validated_at_commit == "aaa1111"

    def test_reviewed_at_commit_set_on_review_submitted(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.AwaitingReview,
            validated_at_commit="aaa1111",
        )
        event = _event(
            event_type=EventType.review_submitted,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.reviewer,
            artifact_refs=ArtifactRefs(created=("ReviewDecision:TEST-001/v1",)),
            base_commit="aaa1111",
        )
        apply(intent, event)
        assert intent.reviewed_at_commit == "aaa1111"

    def test_fresh_commits_allow_integration(self):
        intent = _advance_to_integrating("abc1234")
        event = _event(
            event_type=EventType.integration_completed,
            previous_state=State.Integrating,
            next_state=State.Integrated,
            role=Role.merge_actor,
            artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
            base_commit="abc1234",
        )
        result = reduce(intent, event)
        assert result.valid

    def test_staleness_blocks_integration_validation_mismatch(self):
        intent = _advance_to_integrating("abc1234")
        event = _event(
            event_type=EventType.integration_completed,
            previous_state=State.Integrating,
            next_state=State.Integrated,
            role=Role.merge_actor,
            artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
            base_commit="def5678",
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("Validation was against" in v for v in result.violations)

    def test_staleness_blocks_integration_review_mismatch(self):
        intent = _advance_to_integrating("abc1234")
        event = _event(
            event_type=EventType.integration_completed,
            previous_state=State.Integrating,
            next_state=State.Integrated,
            role=Role.merge_actor,
            artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
            base_commit="def5678",
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("Review was against" in v for v in result.violations)

    def test_commits_cleared_on_rebase(self):
        intent = _advance_to_integrating("abc1234")
        assert intent.validated_at_commit == "abc1234"
        assert intent.reviewed_at_commit == "abc1234"

        event = _event(
            event_type=EventType.rebase_required,
            previous_state=State.Integrating,
            next_state=State.NeedsRebase,
            role=Role.merge_actor,
            base_commit="abc1234",
        )
        apply(intent, event)
        assert intent.validated_at_commit is None
        assert intent.reviewed_at_commit is None

    def test_reviewed_cleared_on_needs_revalidate(self):
        intent = _advance_to_integrating("abc1234")
        apply(
            intent,
            _event(
                event_type=EventType.rebase_required,
                previous_state=State.Integrating,
                next_state=State.NeedsRebase,
                role=Role.merge_actor,
                base_commit="abc1234",
            ),
        )
        apply(
            intent,
            _event(
                event_type=EventType.rebase_completed,
                previous_state=State.NeedsRebase,
                next_state=State.NeedsRevalidate,
                base_commit="def5678",
            ),
        )
        assert intent.current_state == State.NeedsRevalidate
        assert intent.reviewed_at_commit is None

    def test_rebase_revalidate_rereview_integrate(self):
        """Full staleness recovery: rebase -> revalidate -> carry-forward -> integrate."""
        commit_v1 = "aaa1111"
        commit_v2 = "bbb2222"
        intent = _advance_to_integrating(commit_v1)

        recovery_events = [
            _event(
                event_type=EventType.rebase_required,
                previous_state=State.Integrating,
                next_state=State.NeedsRebase,
                role=Role.merge_actor,
                base_commit=commit_v1,
            ),
            _event(
                event_type=EventType.rebase_completed,
                previous_state=State.NeedsRebase,
                next_state=State.NeedsRevalidate,
                base_commit=commit_v2,
            ),
            _event(
                event_type=EventType.revalidation_completed,
                previous_state=State.NeedsRevalidate,
                next_state=State.NeedsRereview,
                artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v2",)),
                base_commit=commit_v2,
            ),
            _event(
                event_type=EventType.carry_forward_granted,
                previous_state=State.NeedsRereview,
                next_state=State.Integrating,
                role=Role.reviewer,
                artifact_refs=ArtifactRefs(created=("ApprovalCarryForward:TEST-001/v1",)),
                base_commit=commit_v2,
            ),
            _event(
                event_type=EventType.integration_completed,
                previous_state=State.Integrating,
                next_state=State.Integrated,
                role=Role.merge_actor,
                artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
                base_commit=commit_v2,
            ),
        ]

        for event in recovery_events:
            result = apply(intent, event)
            assert result.valid, f"Failed at {event.event_type}: {result.violations}"

        assert intent.current_state == State.Integrated
        assert intent.validated_at_commit == commit_v2
        assert intent.reviewed_at_commit == commit_v2

    def test_staleness_blocks_integration_started(self):
        """integration_started (self-transition) also checked for staleness."""
        intent = _advance_to_integrating("abc1234")
        event = _event(
            event_type=EventType.integration_started,
            previous_state=State.Integrating,
            next_state=State.Integrating,
            role=Role.merge_actor,
            base_commit="def5678",
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("rebase required" in v for v in result.violations)

    def test_none_commit_blocks_integration(self):
        """Integration rejected when gate commits were never recorded."""
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Integrating,
            validated_at_commit=None,
            reviewed_at_commit=None,
        )
        event = _event(
            event_type=EventType.integration_completed,
            previous_state=State.Integrating,
            next_state=State.Integrated,
            role=Role.merge_actor,
            artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("No validation base commit" in v for v in result.violations)
        assert any("No review base commit" in v for v in result.violations)

    def test_override_path_sets_validated_at_commit(self):
        """Validating -> OverridePending sets validated_at_commit."""
        intent = IntentState(intent_id="TEST-001", current_state=State.Validating)
        event = _event(
            event_type=EventType.override_requested,
            previous_state=State.Validating,
            next_state=State.OverridePending,
            artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
            base_commit="aaa1111",
        )
        apply(intent, event)
        assert intent.validated_at_commit == "aaa1111"


class TestFastPath:
    def test_fast_path_eligible_valid(self):
        """Clarifying -> Building via fast_path_eligible with FastPathEligibility artifact."""
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.fast_path_eligible,
            previous_state=State.Clarifying,
            next_state=State.Building,
            role=Role.requester,
            artifact_refs=ArtifactRefs(created=("FastPathEligibility:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert result.valid
        assert result.gate_artifact == ArtifactType.FastPathEligibility
        assert result.gate_satisfied

    def test_fast_path_eligible_missing_artifact(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.fast_path_eligible,
            previous_state=State.Clarifying,
            next_state=State.Building,
            role=Role.requester,
        )
        result = reduce(intent, event)
        assert not result.valid
        assert result.gate_artifact == ArtifactType.FastPathEligibility
        assert not result.gate_satisfied

    def test_fast_path_eligible_wrong_state(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Building)
        event = _event(
            event_type=EventType.fast_path_eligible,
            previous_state=State.Building,
            next_state=State.Building,
            role=Role.requester,
            artifact_refs=ArtifactRefs(created=("FastPathEligibility:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert not result.valid

    def test_apply_sets_fast_path_fields(self):
        intent = IntentState(intent_id="TEST-001", current_state=State.Clarifying)
        event = _event(
            event_type=EventType.fast_path_eligible,
            previous_state=State.Clarifying,
            next_state=State.Building,
            role=Role.requester,
            artifact_refs=ArtifactRefs(created=("FastPathEligibility:TEST-001/v1",)),
        )
        apply(intent, event)
        assert intent.started_as_fast_path is True
        assert intent.eligible_at_commit == "abc1234"

    def test_fast_path_authorized_succeeds_when_eligible(self):
        """fast_path_authorized valid when eligible_at_commit matches."""
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.AwaitingReview,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
            validated_at_commit="abc1234",
            reviewed_at_commit=None,
        )
        event = _event(
            event_type=EventType.fast_path_authorized,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.tech_lead,
            artifact_refs=ArtifactRefs(created=("FastPathAuthorization:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert result.valid

    def test_fast_path_authorized_rejected_never_classified(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.AwaitingReview,
            started_as_fast_path=False,
            validated_at_commit="abc1234",
            reviewed_at_commit=None,
        )
        event = _event(
            event_type=EventType.fast_path_authorized,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.tech_lead,
            artifact_refs=ArtifactRefs(created=("FastPathAuthorization:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("never classified" in v for v in result.violations)

    def test_fast_path_authorized_rejected_eligibility_revoked(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.AwaitingReview,
            started_as_fast_path=True,
            eligible_at_commit=None,
            validated_at_commit="abc1234",
            reviewed_at_commit=None,
        )
        event = _event(
            event_type=EventType.fast_path_authorized,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.tech_lead,
            artifact_refs=ArtifactRefs(created=("FastPathAuthorization:TEST-001/v1",)),
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("revoked" in v for v in result.violations)

    def test_fast_path_authorized_rejected_commit_mismatch(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.AwaitingReview,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
            validated_at_commit="def5678",
            reviewed_at_commit=None,
        )
        event = _event(
            event_type=EventType.fast_path_authorized,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.tech_lead,
            artifact_refs=ArtifactRefs(created=("FastPathAuthorization:TEST-001/v1",)),
            base_commit="def5678",
        )
        result = reduce(intent, event)
        assert not result.valid
        assert any("reclassification" in v.lower() for v in result.violations)

    def test_build_started_does_not_revoke_eligibility(self):
        """build_started is a progress self-transition, not rework -- must not revoke."""
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Building,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
        )
        event = _event(
            event_type=EventType.build_started,
            previous_state=State.Building,
            next_state=State.Building,
        )
        apply(intent, event)
        assert intent.eligible_at_commit == "abc1234"

    def test_build_failed_revokes_eligibility(self):
        """build_failed IS rework -- must revoke."""
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Building,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
        )
        event = _event(
            event_type=EventType.build_failed,
            previous_state=State.Building,
            next_state=State.Building,
        )
        apply(intent, event)
        assert intent.eligible_at_commit is None

    def test_eligibility_cleared_on_validation_failed(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Validating,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
        )
        event = _event(
            event_type=EventType.validation_failed,
            previous_state=State.Validating,
            next_state=State.Building,
        )
        apply(intent, event)
        assert intent.started_as_fast_path is True
        assert intent.eligible_at_commit is None

    def test_eligibility_cleared_on_needs_rebase(self):
        intent = IntentState(
            intent_id="TEST-001",
            current_state=State.Integrating,
            started_as_fast_path=True,
            eligible_at_commit="abc1234",
        )
        event = _event(
            event_type=EventType.rebase_required,
            previous_state=State.Integrating,
            next_state=State.NeedsRebase,
            role=Role.merge_actor,
        )
        apply(intent, event)
        assert intent.started_as_fast_path is True
        assert intent.eligible_at_commit is None

    def test_full_fast_path_lifecycle(self):
        """End-to-end fast-path lifecycle through all states."""
        intent = IntentState(intent_id="TEST-001")
        commit = "abc1234"

        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                base_commit=commit,
            ),
            _event(
                event_type=EventType.fast_path_eligible,
                previous_state=State.Clarifying,
                next_state=State.Building,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("FastPathEligibility:TEST-001/v1",)),
                base_commit=commit,
            ),
            _event(
                event_type=EventType.build_completed,
                previous_state=State.Building,
                next_state=State.Validating,
                artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
                base_commit=commit,
            ),
            _event(
                event_type=EventType.validation_completed,
                previous_state=State.Validating,
                next_state=State.AwaitingReview,
                artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
                base_commit=commit,
            ),
            _event(
                event_type=EventType.fast_path_authorized,
                previous_state=State.AwaitingReview,
                next_state=State.Integrating,
                role=Role.reviewer,
                artifact_refs=ArtifactRefs(created=("FastPathAuthorization:TEST-001/v1",)),
                base_commit=commit,
            ),
            _event(
                event_type=EventType.integration_completed,
                previous_state=State.Integrating,
                next_state=State.Integrated,
                role=Role.merge_actor,
                artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
                base_commit=commit,
            ),
        ]

        for event in events:
            result = apply(intent, event)
            assert result.valid, f"Failed at {event.event_type}: {result.violations}"

        assert intent.current_state == State.Integrated
        assert intent.started_as_fast_path is True
        assert intent.event_count == 6
