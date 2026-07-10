"""Tests for the audit query functions."""

from datetime import UTC, datetime, timedelta

from verdx.contracts.audit import (
    ApprovalRecord,
    ArtifactRef,
    ComplianceSummary,
    TimelineEntry,
)
from verdx.contracts.events import ArtifactRefs, EventType, StateEvent
from verdx.contracts.roles import ReasonTag, Role
from verdx.contracts.states import State
from verdx.engine.lifecycle.audit import (
    approval_chain,
    compliance_summary,
    evidence_at_gate,
    format_compliance_report,
    format_trace,
    timeline,
    who_approved,
)

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
    actor: str = "tester",
    detail: str | None = None,
) -> StateEvent:
    return StateEvent(
        intent_id=intent_id,
        event_type=event_type,
        previous_state=previous_state,
        next_state=next_state,
        actor=actor,
        authority_role=role,
        timestamp=timestamp or datetime.now(UTC),
        base_commit=base_commit,
        policy_version="ptl-v1",
        artifact_refs=artifact_refs or ArtifactRefs(),
        elapsed_seconds=elapsed_seconds,
        attention_minutes=attention_minutes,
        wait_minutes=wait_minutes,
        reason_tags=reason_tags,
        detail=detail,
    )


def _happy_path_events() -> list[StateEvent]:
    """Full happy-path event sequence with varied actors and artifact refs."""
    t = _T0
    dt = timedelta(minutes=30)
    return [
        _event(
            event_type=EventType.intent_created,
            previous_state=None,
            next_state=State.Clarifying,
            role=Role.requester,
            actor="alice",
            timestamp=t,
            elapsed_seconds=10.0,
            attention_minutes=1.0,
            wait_minutes=0.0,
        ),
        _event(
            event_type=EventType.clarify_completed,
            previous_state=State.Clarifying,
            next_state=State.SpecDrafting,
            role=Role.requester,
            actor="alice",
            artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            timestamp=t + dt,
            elapsed_seconds=120.0,
            attention_minutes=5.0,
            wait_minutes=2.0,
        ),
        _event(
            event_type=EventType.spec_completed,
            previous_state=State.SpecDrafting,
            next_state=State.AwaitingApproval,
            role=Role.implementer,
            actor="bob",
            artifact_refs=ArtifactRefs(created=("SpecPlan:TEST-001/v1",)),
            timestamp=t + 2 * dt,
            elapsed_seconds=300.0,
            attention_minutes=15.0,
            wait_minutes=5.0,
        ),
        _event(
            event_type=EventType.approval_granted,
            previous_state=State.AwaitingApproval,
            next_state=State.Building,
            role=Role.scope_owner,
            actor="carol",
            timestamp=t + 3 * dt,
            elapsed_seconds=60.0,
            attention_minutes=3.0,
            wait_minutes=10.0,
        ),
        _event(
            event_type=EventType.build_completed,
            previous_state=State.Building,
            next_state=State.Validating,
            role=Role.implementer,
            actor="bob",
            artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
            timestamp=t + 4 * dt,
            elapsed_seconds=600.0,
            attention_minutes=20.0,
            wait_minutes=0.0,
        ),
        _event(
            event_type=EventType.validation_completed,
            previous_state=State.Validating,
            next_state=State.AwaitingReview,
            role=Role.implementer,
            actor="bob",
            artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
            timestamp=t + 5 * dt,
            elapsed_seconds=120.0,
            attention_minutes=2.0,
            wait_minutes=1.0,
        ),
        _event(
            event_type=EventType.review_submitted,
            previous_state=State.AwaitingReview,
            next_state=State.Integrating,
            role=Role.reviewer,
            actor="diana",
            artifact_refs=ArtifactRefs(created=("ReviewDecision:TEST-001/v1",)),
            timestamp=t + 6 * dt,
            elapsed_seconds=180.0,
            attention_minutes=10.0,
            wait_minutes=30.0,
        ),
        _event(
            event_type=EventType.integration_completed,
            previous_state=State.Integrating,
            next_state=State.Integrated,
            role=Role.merge_actor,
            actor="eve",
            artifact_refs=ArtifactRefs(created=("IntegrationReceipt:TEST-001/v1",)),
            timestamp=t + 7 * dt,
            elapsed_seconds=30.0,
            attention_minutes=1.0,
            wait_minutes=0.0,
        ),
    ]


# ===================================================================
# TestTimeline
# ===================================================================


class TestTimeline:
    def test_empty(self):
        assert timeline([], "TEST-001") == []

    def test_single_intent(self):
        events = _happy_path_events()
        entries = timeline(events, "TEST-001")
        assert len(entries) == 8
        assert entries[0].event_type == "intent_created"
        assert entries[-1].event_type == "integration_completed"

    def test_filters_by_intent_id(self):
        events = _happy_path_events()
        events.append(
            _event(
                intent_id="OTHER-002",
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                actor="zara",
            )
        )
        entries = timeline(events, "TEST-001")
        assert len(entries) == 8
        assert all(e.intent_id == "TEST-001" for e in entries)

    def test_preserves_order(self):
        events = _happy_path_events()
        entries = timeline(events, "TEST-001")
        indices = [e.index for e in entries]
        assert indices == list(range(8))

    def test_includes_artifact_refs(self):
        events = _happy_path_events()
        entries = timeline(events, "TEST-001")
        # clarify_completed should have IntentSummary
        entry = entries[1]
        assert "IntentSummary:TEST-001/v1" in entry.artifact_refs_created


# ===================================================================
# TestWhoApproved
# ===================================================================


class TestWhoApproved:
    def test_approval_granted_at_awaiting_approval(self):
        events = _happy_path_events()
        records = who_approved(events, "TEST-001", State.AwaitingApproval)
        assert len(records) == 1
        assert records[0].event_type == "approval_granted"
        assert records[0].actor == "carol"
        assert records[0].authority_role == "scope_owner"

    def test_review_submitted_at_awaiting_review(self):
        events = _happy_path_events()
        records = who_approved(events, "TEST-001", State.AwaitingReview)
        assert len(records) == 1
        assert records[0].event_type == "review_submitted"
        assert records[0].actor == "diana"

    def test_override_at_override_pending(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                actor="alice",
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                actor="alice",
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.spec_completed,
                previous_state=State.SpecDrafting,
                next_state=State.AwaitingApproval,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("SpecPlan:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.approval_granted,
                previous_state=State.AwaitingApproval,
                next_state=State.Building,
                role=Role.scope_owner,
                actor="carol",
            ),
            _event(
                event_type=EventType.build_completed,
                previous_state=State.Building,
                next_state=State.Validating,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.override_requested,
                previous_state=State.Validating,
                next_state=State.OverridePending,
                role=Role.implementer,
                actor="bob",
            ),
            _event(
                event_type=EventType.override_granted,
                previous_state=State.OverridePending,
                next_state=State.AwaitingReview,
                role=Role.tech_lead,
                actor="frank",
                artifact_refs=ArtifactRefs(created=("OverrideDecision:TEST-001/v1",)),
            ),
        ]
        records = who_approved(events, "TEST-001", State.OverridePending)
        assert len(records) == 1
        assert records[0].event_type == "override_granted"
        assert records[0].actor == "frank"

    def test_carry_forward_at_needs_rereview(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                actor="alice",
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                actor="alice",
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.spec_completed,
                previous_state=State.SpecDrafting,
                next_state=State.AwaitingApproval,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("SpecPlan:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.approval_granted,
                previous_state=State.AwaitingApproval,
                next_state=State.Building,
                role=Role.scope_owner,
                actor="carol",
            ),
            _event(
                event_type=EventType.build_completed,
                previous_state=State.Building,
                next_state=State.Validating,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.validation_completed,
                previous_state=State.Validating,
                next_state=State.AwaitingReview,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.rebase_required,
                previous_state=State.AwaitingReview,
                next_state=State.NeedsRebase,
                role=Role.merge_actor,
                actor="eve",
            ),
            _event(
                event_type=EventType.rebase_completed,
                previous_state=State.NeedsRebase,
                next_state=State.NeedsRevalidate,
                role=Role.implementer,
                actor="bob",
            ),
            _event(
                event_type=EventType.revalidation_completed,
                previous_state=State.NeedsRevalidate,
                next_state=State.NeedsRereview,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("RunAssessment:TEST-001/v2",)),
            ),
            _event(
                event_type=EventType.carry_forward_granted,
                previous_state=State.NeedsRereview,
                next_state=State.Integrating,
                role=Role.reviewer,
                actor="diana",
                artifact_refs=ArtifactRefs(created=("ApprovalCarryForward:TEST-001/v1",)),
            ),
        ]
        records = who_approved(events, "TEST-001", State.NeedsRereview)
        assert len(records) == 1
        assert records[0].event_type == "carry_forward_granted"
        assert records[0].actor == "diana"

    def test_wrong_gate_returns_empty(self):
        events = _happy_path_events()
        records = who_approved(events, "TEST-001", State.Building)
        assert records == []


# ===================================================================
# TestEvidenceAtGate
# ===================================================================


class TestEvidenceAtGate:
    def test_artifacts_accumulated_before_gate(self):
        events = _happy_path_events()
        refs = evidence_at_gate(events, "TEST-001", State.AwaitingApproval)
        # Before the approval_granted at AwaitingApproval, we should have:
        # IntentSummary:TEST-001/v1 (from clarify_completed)
        # SpecPlan:TEST-001/v1 (from spec_completed)
        ref_strings = [r.ref for r in refs]
        assert "IntentSummary:TEST-001/v1" in ref_strings
        assert "SpecPlan:TEST-001/v1" in ref_strings

    def test_no_gate_returns_empty(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
            ),
        ]
        refs = evidence_at_gate(events, "TEST-001", State.AwaitingApproval)
        assert refs == []


# ===================================================================
# TestApprovalChain
# ===================================================================


class TestApprovalChain:
    def test_happy_path_chain(self):
        events = _happy_path_events()
        chain = approval_chain(events, "TEST-001")
        assert len(chain) == 2
        assert chain[0].event_type == "approval_granted"
        assert chain[1].event_type == "review_submitted"

    def test_includes_rejections(self):
        events = [
            _event(
                event_type=EventType.intent_created,
                previous_state=None,
                next_state=State.Clarifying,
                role=Role.requester,
                actor="alice",
            ),
            _event(
                event_type=EventType.clarify_completed,
                previous_state=State.Clarifying,
                next_state=State.SpecDrafting,
                role=Role.requester,
                actor="alice",
                artifact_refs=ArtifactRefs(created=("IntentSummary:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.spec_completed,
                previous_state=State.SpecDrafting,
                next_state=State.AwaitingApproval,
                role=Role.implementer,
                actor="bob",
                artifact_refs=ArtifactRefs(created=("SpecPlan:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.approval_rejected,
                previous_state=State.AwaitingApproval,
                next_state=State.SpecDrafting,
                role=Role.scope_owner,
                actor="carol",
                artifact_refs=ArtifactRefs(created=("ReviewDecision:TEST-001/rej",)),
            ),
        ]
        chain = approval_chain(events, "TEST-001")
        assert len(chain) == 1
        assert chain[0].event_type == "approval_rejected"

    def test_includes_overrides(self):
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
                role=Role.implementer,
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
                role=Role.implementer,
                artifact_refs=ArtifactRefs(created=("BuildReport:TEST-001/v1",)),
            ),
            _event(
                event_type=EventType.override_requested,
                previous_state=State.Validating,
                next_state=State.OverridePending,
                role=Role.implementer,
            ),
            _event(
                event_type=EventType.override_granted,
                previous_state=State.OverridePending,
                next_state=State.AwaitingReview,
                role=Role.tech_lead,
                actor="frank",
                artifact_refs=ArtifactRefs(created=("OverrideDecision:TEST-001/v1",)),
            ),
        ]
        chain = approval_chain(events, "TEST-001")
        types = [r.event_type for r in chain]
        assert "approval_granted" in types
        assert "override_requested" in types
        assert "override_granted" in types

    def test_empty_events(self):
        assert approval_chain([], "TEST-001") == []


# ===================================================================
# TestComplianceSummary
# ===================================================================


class TestComplianceSummary:
    def test_happy_path_all_gates_satisfied(self):
        events = _happy_path_events()
        s = compliance_summary(events, "TEST-001")
        assert s.final_state == "Integrated"
        assert s.gates_satisfied == s.gates_total
        assert s.total_events == 8

    def test_with_violations(self):
        # clarify_completed without artifact ref triggers gate violation
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
                # Missing IntentSummary artifact ref
            ),
        ]
        s = compliance_summary(events, "TEST-001")
        assert s.gates_satisfied < s.gates_total

    def test_actors_roles_distinct_and_sorted(self):
        events = _happy_path_events()
        s = compliance_summary(events, "TEST-001")
        assert s.actors_involved == tuple(sorted({"alice", "bob", "carol", "diana", "eve"}))
        expected_roles = tuple(
            sorted(
                {
                    "requester",
                    "implementer",
                    "scope_owner",
                    "reviewer",
                    "merge_actor",
                }
            )
        )
        assert s.roles_involved == expected_roles

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
                role=Role.implementer,
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
        s = compliance_summary(events, "TEST-001")
        assert s.rework_count == 1

    def test_fast_path_flag(self):
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
        s = compliance_summary(events, "TEST-001")
        assert s.started_as_fast_path is True


# ===================================================================
# TestFormatTrace
# ===================================================================


class TestFormatTrace:
    def test_ascii_contains_state_names_and_actors(self):
        events = _happy_path_events()
        entries = timeline(events, "TEST-001")
        output = format_trace(entries, fmt="ascii")
        assert "Clarifying" in output
        assert "Integrated" in output
        assert "alice" in output
        assert "carol" in output

    def test_mermaid_contains_state_diagram(self):
        events = _happy_path_events()
        entries = timeline(events, "TEST-001")
        output = format_trace(entries, fmt="mermaid")
        assert "stateDiagram-v2" in output
        assert "-->" in output


# ===================================================================
# TestFormatComplianceReport
# ===================================================================


class TestFormatComplianceReport:
    def test_markdown_contains_expected_headers(self):
        events = _happy_path_events()
        entries = timeline(events, "TEST-001")
        s = compliance_summary(events, "TEST-001")
        report = format_compliance_report(s, entries)
        assert "# Compliance Report: TEST-001" in report
        assert "## Status" in report
        assert "## Gate Summary" in report
        assert "## Approval Chain" in report
        assert "## Timing Metrics" in report
        assert "## Actors" in report
        assert "## Timeline" in report

    def test_violations_section_present_when_violations_exist(self):
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
                # Missing artifact ref -> gate violation
            ),
        ]
        entries = timeline(events, "TEST-001")
        s = compliance_summary(events, "TEST-001")
        report = format_compliance_report(s, entries)
        assert "## Violations" in report


class TestContractRoundTrip:
    def test_approval_record_round_trip(self):
        rec = ApprovalRecord(
            intent_id="T-001",
            event_type="approval_granted",
            gate_state="AwaitingApproval",
            actor="alice",
            authority_role="scope_owner",
            timestamp="2026-05-02T10:00:00",
            artifact_refs=("SpecPlan:v1",),
            detail="LGTM",
            reason_tags=("judgment_call",),
        )
        assert ApprovalRecord.from_dict(rec.to_dict()) == rec

    def test_timeline_entry_round_trip(self):
        entry = TimelineEntry(
            index=0,
            intent_id="T-001",
            event_type="intent_created",
            previous_state=None,
            next_state="Clarifying",
            actor="bob",
            authority_role="requester",
            timestamp="2026-05-02T10:00:00",
            elapsed_seconds=1.5,
            detail="kickoff",
            artifact_refs_created=("IntentSummary:v1",),
            reason_tags=("intent_miss",),
        )
        assert TimelineEntry.from_dict(entry.to_dict()) == entry

    def test_artifact_ref_round_trip(self):
        ref = ArtifactRef(
            ref="RunAssessment:v1",
            created_by_actor="validator",
            created_at="2026-05-02T11:00:00",
            created_at_event_index=5,
        )
        assert ArtifactRef.from_dict(ref.to_dict()) == ref

    def test_compliance_summary_round_trip(self):
        chain = (
            ApprovalRecord(
                intent_id="T-001",
                event_type="approval_granted",
                gate_state="AwaitingApproval",
                actor="alice",
                authority_role="scope_owner",
                timestamp="2026-05-02T10:00:00",
            ),
        )
        summary = ComplianceSummary(
            intent_id="T-001",
            final_state="Integrated",
            total_events=10,
            gates_satisfied=3,
            gates_total=3,
            violations=(),
            rework_count=0,
            actors_involved=("alice", "bob"),
            roles_involved=("requester", "scope_owner"),
            approval_chain=chain,
            cycle_time_seconds=3600.0,
            total_elapsed_seconds=120.0,
            total_attention_minutes=30.0,
            total_wait_minutes=10.0,
            reason_tag_counts={"intent_miss": 1},
            started_as_fast_path=False,
        )
        assert ComplianceSummary.from_dict(summary.to_dict()) == summary
