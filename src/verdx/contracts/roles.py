"""Authority roles and reason tags for the intent-fulfillment cycle."""

from enum import StrEnum


class Role(StrEnum):
    requester = "requester"
    scope_owner = "scope_owner"
    implementer = "implementer"
    reviewer = "reviewer"
    tech_lead = "tech_lead"
    override_approver = "override_approver"
    merge_actor = "merge_actor"


class ReasonTag(StrEnum):
    intent_miss = "intent_miss"
    spec_gap = "spec_gap"
    validation_gap = "validation_gap"
    coverage_gap = "coverage_gap"
    judgment_call = "judgment_call"
    implementation_bug = "implementation_bug"
    external_change = "external_change"
    process_friction = "process_friction"
