"""Tests for local_jsonl store backend."""

from datetime import UTC, datetime

import pytest

from verdx.contracts.errors import StoreError
from verdx.contracts.events import ArtifactRefs, EventType, StateEvent
from verdx.contracts.roles import Role
from verdx.contracts.states import State
from verdx.engine.runtime.store.lifecycle_store import LocalJsonlBackend


def _event(
    *,
    intent_id: str = "TEST-001",
    event_type: EventType = EventType.intent_created,
    previous_state: State | None = None,
    next_state: State = State.Clarifying,
    role: Role = Role.requester,
) -> StateEvent:
    return StateEvent(
        intent_id=intent_id,
        event_type=event_type,
        previous_state=previous_state,
        next_state=next_state,
        actor="tester",
        authority_role=role,
        timestamp=datetime.now(UTC),
        base_commit="abc1234",
        policy_version="ptl-v1",
    )


class TestInit:
    def test_not_initialized_by_default(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        assert not backend.initialized

    def test_init_creates_directory(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        assert backend.initialized
        assert (tmp_path / ".agentic").is_dir()
        assert (tmp_path / ".agentic" / "artifacts").is_dir()
        assert (tmp_path / ".agentic" / "state.jsonl").exists()

    def test_init_is_idempotent(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.init()
        assert backend.initialized

    def test_root_property(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        assert backend.root == tmp_path


class TestAppendAndLoad:
    def test_append_and_load_single_event(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        event = _event()
        backend.append_event(event)
        events = backend.load_events()
        assert len(events) == 1
        assert events[0].intent_id == "TEST-001"
        assert events[0].event_type == EventType.intent_created

    def test_append_multiple_events(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.append_event(_event(intent_id="A-001"))
        backend.append_event(_event(intent_id="B-001"))
        backend.append_event(
            _event(
                intent_id="A-001",
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
            )
        )
        events = backend.load_events()
        assert len(events) == 3

    def test_load_filtered_by_intent(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.append_event(_event(intent_id="A-001"))
        backend.append_event(_event(intent_id="B-001"))
        events = backend.load_events("A-001")
        assert len(events) == 1
        assert events[0].intent_id == "A-001"

    def test_load_empty_returns_empty(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        assert backend.load_events() == []

    def test_append_without_init_raises(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        with pytest.raises(StoreError, match="Not initialized"):
            backend.append_event(_event())


class TestArtifacts:
    def test_record_and_load_artifact(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        data = {"score": 85, "passed": True}
        ref = backend.record_artifact("TEST-001", "RunAssessment", data)
        assert ref == "TEST-001/RunAssessment.json"

        loaded = backend.load_artifact("TEST-001", "RunAssessment")
        assert loaded == data

    def test_load_missing_artifact(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        assert backend.load_artifact("TEST-001", "RunAssessment") is None

    def test_record_without_init_raises(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        with pytest.raises(StoreError, match="Not initialized"):
            backend.record_artifact("TEST-001", "RunAssessment", {})


class TestQueryState:
    def test_query_nonexistent_intent(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        assert backend.query_state("NOPE") is None

    def test_query_state_replays_events(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.append_event(_event(intent_id="TEST-001"))
        backend.append_event(
            StateEvent(
                intent_id="TEST-001",
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                actor="tester",
                authority_role=Role.requester,
                timestamp=datetime.now(UTC),
                base_commit="abc1234",
                policy_version="ptl-v1",
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            )
        )
        state = backend.query_state("TEST-001")
        assert state is not None
        assert state.current_state == State.SpecDrafting
        assert state.event_count == 2


class TestListIntents:
    def test_list_empty(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        assert backend.list_intents() == []

    def test_list_preserves_order(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.append_event(_event(intent_id="C-001"))
        backend.append_event(_event(intent_id="A-001"))
        backend.append_event(_event(intent_id="B-001"))
        intents = backend.list_intents()
        assert intents == ["C-001", "A-001", "B-001"]

    def test_list_deduplicates(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.append_event(_event(intent_id="A-001"))
        backend.append_event(
            _event(
                intent_id="A-001",
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
            )
        )
        intents = backend.list_intents()
        assert intents == ["A-001"]


class TestCrashRecovery:
    def test_partial_last_line_is_rejected(self, tmp_path):
        backend = LocalJsonlBackend(tmp_path)
        backend.init()
        backend.append_event(_event(intent_id="GOOD"))
        state_file = tmp_path / ".agentic" / "state.jsonl"
        with open(state_file, "a") as f:
            f.write('{"schema_version": "state-event/v1", "truncated\n')
        with pytest.raises(StoreError, match="Invalid event on line 2"):
            backend.load_events()
