"""Replay validator: reconstruct state from event log, validate all gates.

The replay function folds events through the reducer, accumulating state
per intent. The result includes:
  - Final state per intent
  - Gate validation report (which gates were satisfied)
  - Any violations encountered during replay
  - Whether integration is safe (all gates cleared)
"""

from collections import Counter
from dataclasses import dataclass, field

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.events import EventType, StateEvent
from verdx.contracts.states import State
from verdx.engine.lifecycle.reducer import IntentState, ReducerResult, apply

_REWORK_EVENTS = frozenset(
    {
        EventType.rebase_required,
        EventType.validation_failed,
        EventType.build_failed,
        EventType.integration_failed,
        EventType.override_rejected,
        EventType.approval_rejected,
    }
)


@dataclass(frozen=True)
class GateRecord:
    from_state: State | None
    to_state: State
    artifact_type: ArtifactType
    satisfied: bool
    event_index: int


@dataclass(frozen=True)
class IntentMetrics:
    cycle_time_seconds: float | None
    total_elapsed_seconds: float
    total_attention_minutes: float
    total_wait_minutes: float
    rework_count: int
    stage_entries: dict[str, int]
    reason_tag_counts: dict[str, int]
    started_as_fast_path: bool = False


@dataclass(frozen=True)
class IntentReplayResult:
    intent_id: str
    final_state: State | None
    event_count: int
    gates: tuple[GateRecord, ...]
    violations: tuple[str, ...]
    artifacts_created: frozenset[str]
    metrics: IntentMetrics | None = None

    @property
    def all_gates_satisfied(self) -> bool:
        return all(g.satisfied for g in self.gates)

    @property
    def can_integrate(self) -> bool:
        return self.all_gates_satisfied and len(self.violations) == 0


@dataclass
class ReplayResult:
    intents: dict[str, IntentReplayResult] = field(default_factory=dict)
    total_events: int = 0
    total_violations: int = 0

    @property
    def clean(self) -> bool:
        return self.total_violations == 0 and all(
            r.all_gates_satisfied for r in self.intents.values()
        )


def replay(events: list[StateEvent]) -> ReplayResult:
    intent_states: dict[str, IntentState] = {}
    intent_gates: dict[str, list[GateRecord]] = {}
    intent_violations: dict[str, list[str]] = {}

    intent_elapsed: dict[str, float] = {}
    intent_attention: dict[str, float] = {}
    intent_wait: dict[str, float] = {}
    intent_rework: dict[str, int] = {}
    intent_stages: dict[str, Counter[str]] = {}
    intent_reasons: dict[str, Counter[str]] = {}
    intent_fast_path: dict[str, bool] = {}
    intent_first_ts: dict[str, float] = {}
    intent_last_ts: dict[str, float] = {}

    for i, event in enumerate(events):
        iid = event.intent_id

        if iid not in intent_states:
            intent_states[iid] = IntentState(intent_id=iid)
            intent_gates[iid] = []
            intent_violations[iid] = []
            intent_elapsed[iid] = 0.0
            intent_attention[iid] = 0.0
            intent_wait[iid] = 0.0
            intent_rework[iid] = 0
            intent_stages[iid] = Counter()
            intent_reasons[iid] = Counter()
            intent_fast_path[iid] = False

        state = intent_states[iid]
        result: ReducerResult = apply(state, event)

        if result.gate_artifact is not None:
            intent_gates[iid].append(
                GateRecord(
                    from_state=event.previous_state,
                    to_state=event.next_state,
                    artifact_type=result.gate_artifact,
                    satisfied=result.gate_satisfied,
                    event_index=i,
                )
            )

        if not result.valid:
            for v in result.violations:
                intent_violations[iid].append(f"Event {i}: {v}")

        # Accumulate metrics
        ts = event.timestamp.timestamp()
        if iid not in intent_first_ts:
            intent_first_ts[iid] = ts
        intent_last_ts[iid] = ts

        if event.elapsed_seconds is not None:
            intent_elapsed[iid] += event.elapsed_seconds
        if event.attention_minutes is not None:
            intent_attention[iid] += event.attention_minutes
        if event.wait_minutes is not None:
            intent_wait[iid] += event.wait_minutes
        if event.event_type in _REWORK_EVENTS:
            intent_rework[iid] += 1

        intent_stages[iid][event.next_state.value] += 1

        if event.event_type == EventType.fast_path_eligible:
            intent_fast_path[iid] = True

        for tag in event.reason_tags:
            intent_reasons[iid][tag.value] += 1

    replay_result = ReplayResult(total_events=len(events))

    for iid, state in intent_states.items():
        violations = tuple(intent_violations[iid])
        replay_result.total_violations += len(violations)

        first = intent_first_ts.get(iid)
        last = intent_last_ts.get(iid)
        cycle_time = (
            (last - first) if first is not None and last is not None and last > first else None
        )

        metrics = IntentMetrics(
            cycle_time_seconds=cycle_time,
            total_elapsed_seconds=intent_elapsed[iid],
            total_attention_minutes=intent_attention[iid],
            total_wait_minutes=intent_wait[iid],
            rework_count=intent_rework[iid],
            stage_entries=dict(intent_stages[iid]),
            reason_tag_counts=dict(intent_reasons[iid]),
            started_as_fast_path=intent_fast_path[iid],
        )

        replay_result.intents[iid] = IntentReplayResult(
            intent_id=iid,
            final_state=state.current_state,
            event_count=state.event_count,
            gates=tuple(intent_gates[iid]),
            violations=violations,
            artifacts_created=frozenset(state.artifacts_created),
            metrics=metrics,
        )

    return replay_result
