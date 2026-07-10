"""Tests for the replay validator."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.events import ArtifactRefs, EventType, StateEvent
from verdx.contracts.roles import ReasonTag, Role
from verdx.contracts.states import State
from verdx.engine.lifecycle.replay import replay

_T0 = datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC)


def _event(
    *,
    intent_id: str = "TEST-001",
    event_type: EventType,
    previous_state: State | None,
    next_state: State,
    role: Role = Role.implementer,
    artifact_refs: ArtifactRefs | None = None,
    base_commit: str = "abc1234",
    timestamp: datetime | None = None,
    elapsed_seconds: float | None = None,
    attention_minutes: float | None = None,
    wait_minutes: float | None = None,
    reason_tags: tuple[ReasonTag, ...] = (),
) -> StateEvent:
    return StateEvent(
        intent_id=intent_id,
        event_type=event_type,
        previous_state=previous_state,
        next_state=next_state,
        actor="tester",
        authority_role=role,
        timestamp=timestamp or datetime.now(UTC),
        base_commit=base_commit,
        policy_version="ptl-v1",
        artifact_refs=artifact_refs or ArtifactRefs(),
        elapsed_seconds=elapsed_seconds,
        attention_minutes=attention_minutes,
        wait_minutes=wait_minutes,
        reason_tags=reason_tags,
    )


class TestReplayBasic:
    def test_empty_events(self):
        result = replay([])
        assert result.total_events == 0
        assert result.intents == {}
        assert result.clean

    def test_single_event(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
        ]
        result = replay(events)
        assert result.total_events == 1
        assert "TEST-001" in result.intents
        assert result.intents["TEST-001"].final_state == State.Clarifying
        assert result.intents["TEST-001"].event_count == 1


class TestReplayHappyPath:
    def test_full_happy_path_with_all_gates(self):
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
            _event(
                event_type=EventType.approval_granted,
                previous_state=State.AwaitingApproval,
                next_state=State.Building,
                role=Role.scope_owner,
            ),
            _event(
                event_type=EventType.build_completed,
                previous_state=State.Building,
                next_state=State.Validating,
                artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.validation_completed,
                previous_state=State.Validating,
                next_state=State.AwaitingReview,
                artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.review_submitted,
                previous_state=State.AwaitingReview,
                next_state=State.Integrating,
                role=Role.reviewer,
                artifact_refs=ArtifactRefs(created=("ReviewDecision:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.integration_completed,
                previous_state=State.Integrating,
                next_state=State.Integrated,
                role=Role.merge_actor,
                artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
            ),
        ]
        result = replay(events)

        intent = result.intents["TEST-001"]
        assert intent.final_state == State.Integrated
        assert intent.event_count == 8
        assert intent.all_gates_satisfied
        assert intent.can_integrate
        assert result.clean

    def test_gates_are_recorded(self):
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
        ]
        result = replay(events)
        intent = result.intents["TEST-001"]
        assert len(intent.gates) == 1
        gate = intent.gates[0]
        assert gate.artifact_type == ArtifactType.IntentSummary
        assert gate.satisfied
        assert gate.event_index == 1


class TestReplayViolations:
    def test_missing_gate_artifact_is_violation(self):
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
            ),
        ]
        result = replay(events)
        intent = result.intents["TEST-001"]
        assert not intent.all_gates_satisfied
        assert not intent.can_integrate
        assert not result.clean

    def test_violations_include_event_index(self):
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
            ),
        ]
        result = replay(events)
        intent = result.intents["TEST-001"]
        assert any("Event 1:" in v for v in intent.violations)


class TestReplayMultipleIntents:
    def test_independent_intents(self):
        events = [
            _event(
                intent_id="A-001",
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
            _event(
                intent_id="B-001",
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
        ]
        result = replay(events)
        assert len(result.intents) == 2
        assert result.intents["A-001"].final_state == State.Clarifying
        assert result.intents["B-001"].final_state == State.Clarifying


class TestGoldenSample:
    @pytest.fixture
    def sample_events(self) -> list[StateEvent]:
        fixture = Path(__file__).parent / "golden" / "sample_events.jsonl"
        events = []
        for line in fixture.read_text().strip().splitlines():
            events.append(StateEvent.model_validate_json(line))
        return events

    def test_parses_all_13_events(self, sample_events):
        assert len(sample_events) == 13

    def test_replay_reaches_integrated(self, sample_events):
        result = replay(sample_events)
        intent = result.intents["intent-001"]
        assert intent.final_state == State.Integrated

    def test_replay_all_gates_satisfied(self, sample_events):
        result = replay(sample_events)
        intent = result.intents["intent-001"]
        assert intent.all_gates_satisfied

    def test_replay_can_integrate(self, sample_events):
        result = replay(sample_events)
        intent = result.intents["intent-001"]
        assert intent.can_integrate

    def test_replay_clean(self, sample_events):
        result = replay(sample_events)
        assert result.clean
        assert result.total_violations == 0

    def test_replay_event_count(self, sample_events):
        result = replay(sample_events)
        intent = result.intents["intent-001"]
        assert intent.event_count == 13

    def test_replay_gate_count(self, sample_events):
        result = replay(sample_events)
        intent = result.intents["intent-001"]
        gate_types = [g.artifact_type for g in intent.gates]
        assert ArtifactType.IntentSummary in gate_types
        assert ArtifactType.SpecPlan in gate_types
        assert ArtifactType.BuildReport in gate_types
        assert ArtifactType.RunAssessment in gate_types
        assert ArtifactType.OverrideDecision in gate_types
        assert ArtifactType.ReviewDecision in gate_types
        assert ArtifactType.ApprovalCarryForward in gate_types
        assert ArtifactType.IntegrationReceipt in gate_types

    def test_replay_artifacts_created(self, sample_events):
        result = replay(sample_events)
        intent = result.intents["intent-001"]
        expected = {
            "IntentSummary:intent-001/v1",
            "SpecPlan:intent-001/v1",
            "BuildReport:intent-001/v1",
            "RunAssessment:intent-001/v1",
            "OverrideDecision:intent-001/v1",
            "ReviewDecision:intent-001/v1",
            "RunAssessment:intent-001/v2",
            "ApprovalCarryForward:intent-001/v1",
            "IntegrationReceipt:intent-001/v1",
        }
        assert intent.artifacts_created == frozenset(expected)


class TestMetrics:
    def test_cycle_time(self):
        t0 = _T0
        t1 = t0 + timedelta(hours=2, minutes=34)
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                timestamp=t0,
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
                timestamp=t1,
            ),
        ]
        result = replay(events)
        m = result.intents["TEST-001"].metrics
        assert m is not None
        assert m.cycle_time_seconds == pytest.approx(2 * 3600 + 34 * 60)

    def test_attention_wait_totals(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                elapsed_seconds=120.0,
                attention_minutes=10.0,
                wait_minutes=5.0,
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
                elapsed_seconds=300.0,
                attention_minutes=25.0,
                wait_minutes=15.0,
            ),
        ]
        result = replay(events)
        m = result.intents["TEST-001"].metrics
        assert m is not None
        assert m.total_elapsed_seconds == pytest.approx(420.0)
        assert m.total_attention_minutes == pytest.approx(35.0)
        assert m.total_wait_minutes == pytest.approx(20.0)

    def test_rework_count(self):
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
            _event(
                event_type=EventType.approval_rejected,
                previous_state=State.AwaitingApproval,
                next_state=State.SpecDrafting,
                role=Role.scope_owner,
                artifact_refs=ArtifactRefs(created=("ReviewDecision:TEST-001/rej",)),
            ),
        ]
        result = replay(events)
        m = result.intents["TEST-001"].metrics
        assert m is not None
        assert m.rework_count == 1

    def test_no_timing_data(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
        ]
        result = replay(events)
        m = result.intents["TEST-001"].metrics
        assert m is not None
        assert m.total_elapsed_seconds == 0.0
        assert m.total_attention_minutes == 0.0
        assert m.total_wait_minutes == 0.0

    def test_reason_tag_counts(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                reason_tags=(ReasonTag.intent_miss,),
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
                reason_tags=(ReasonTag.intent_miss, ReasonTag.spec_gap),
            ),
        ]
        result = replay(events)
        m = result.intents["TEST-001"].metrics
        assert m is not None
        assert m.reason_tag_counts == {"intent_miss": 2, "spec_gap": 1}

    def test_stage_entries(self):
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
        ]
        result = replay(events)
        m = result.intents["TEST-001"].metrics
        assert m is not None
        assert m.stage_entries["Clarifying"] == 1
        assert m.stage_entries["SpecDrafting"] == 1


class TestFastPathMetrics:
    def test_fast_path_flag_set(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
            _event(
                event_type=EventType.fast_path_eligible,
                previous_state=State.Clarifying,
                next_state=State.Building,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("FastPathEligibility:TEST-001/v1",)),
            ),
        ]
        result = replay(events)
        assert result.intents["TEST-001"].metrics.started_as_fast_path is True

    def test_normal_intent_flag_not_set(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
        ]
        result = replay(events)
        assert result.intents["TEST-001"].metrics.started_as_fast_path is False

    def test_fast_path_and_normal_coexist(self):
        events = [
            _event(
                intent_id="FP-001",
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
            _event(
                intent_id="FP-001",
                event_type=EventType.fast_path_eligible,
                previous_state=State.Clarifying,
                next_state=State.Building,
                role=Role.requester,
                artifact_refs=ArtifactRefs(created=("FastPathEligibility:FP-001/v1",)),
            ),
            _event(
                intent_id="N-001",
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
        ]
        result = replay(events)
        assert result.intents["FP-001"].metrics.started_as_fast_path is True
        assert result.intents["N-001"].metrics.started_as_fast_path is False
