"""StateEvent model matching state-event.schema.json from agentic-sdlc."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from verdx.contracts.roles import ReasonTag, Role
from verdx.contracts.states import State


class EventType(StrEnum):
    intent_created = "intent_created"
    clarify_started = "clarify_started"
    clarify_completed = "clarify_completed"
    spec_started = "spec_started"
    spec_completed = "spec_completed"
    approval_granted = "approval_granted"
    approval_rejected = "approval_rejected"
    build_started = "build_started"
    build_completed = "build_completed"
    build_failed = "build_failed"
    build_escalated = "build_escalated"
    validation_started = "validation_started"
    validation_completed = "validation_completed"
    validation_failed = "validation_failed"
    review_submitted = "review_submitted"
    override_requested = "override_requested"
    override_granted = "override_granted"
    override_rejected = "override_rejected"
    rebase_required = "rebase_required"
    rebase_completed = "rebase_completed"
    revalidation_required = "revalidation_required"
    revalidation_completed = "revalidation_completed"
    rereview_required = "rereview_required"
    carry_forward_granted = "carry_forward_granted"
    fast_path_authorized = "fast_path_authorized"
    fast_path_eligible = "fast_path_eligible"
    integration_started = "integration_started"
    integration_completed = "integration_completed"
    integration_failed = "integration_failed"
    blocked = "blocked"
    unblocked = "unblocked"
    abandoned = "abandoned"
    superseded = "superseded"
    reverted = "reverted"


class ChildUnitType(StrEnum):
    section = "section"
    module = "module"
    bug = "bug"
    release = "release"
    other = "other"


class ArtifactRefs(BaseModel):
    model_config = ConfigDict(frozen=True)

    created: tuple[str, ...] = ()
    consumed: tuple[str, ...] = ()


class StateEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "state-event/v1"
    intent_id: str
    parent_intent_id: str | None = None
    child_unit_type: ChildUnitType | None = None

    event_type: EventType
    previous_state: State | None
    next_state: State

    actor: str
    authority_role: Role
    timestamp: datetime
    base_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]

    branch: str | None = None
    pr_ids: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    artifact_refs: ArtifactRefs = ArtifactRefs()

    policy_version: str
    handler_versions: dict[str, str] | None = None

    elapsed_seconds: Annotated[float, Field(ge=0)] | None = None
    attention_minutes: Annotated[float, Field(ge=0)] | None = None
    wait_minutes: Annotated[float, Field(ge=0)] | None = None

    reason_tags: tuple[ReasonTag, ...] = ()
    detail: str | None = None
