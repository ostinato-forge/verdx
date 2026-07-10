"""Pipeline configuration: stages, gates, and routing.

A pipeline defines a sequence of quality gates that content passes through.
Each stage runs a subset of validators, evaluates a gate condition
(score threshold, human approval, or both), and routes to the next stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import yaml

from verdx.contracts.config import ConfigError
from verdx.contracts.subjects import (
    SubjectCoverageState,
    SubjectSelector,
    matches_coverage_selector,
)

if TYPE_CHECKING:
    from pathlib import Path

    from verdx.contracts.assessment import RunAssessment

PIPELINE_SCHEMA_VERSION = 1

VALID_GATE_TYPES = frozenset({"score", "human", "score+human"})


@dataclass(frozen=True, slots=True)
class GateConfig:
    """Gate condition for a pipeline stage."""

    type: str
    threshold: int | None = None
    required_approvals: int = 1
    reviewers_from: tuple[str, ...] = ()
    require_independence: bool = False
    require_subject_coverage: bool = False
    allowed_subject_debt: tuple[SubjectCoverageState, ...] = ()
    subject_selector: SubjectSelector | None = None

    def __post_init__(self) -> None:
        if self.type not in VALID_GATE_TYPES:
            raise ValueError(f"gate type must be one of {sorted(VALID_GATE_TYPES)}")
        if self.type in ("score", "score+human") and self.threshold is None:
            raise ValueError(f"gate type '{self.type}' requires a threshold")
        if self.threshold is not None and not 0 <= self.threshold <= 100:
            raise ValueError("threshold must be between 0 and 100")
        if self.required_approvals < 0:
            raise ValueError("required_approvals must be non-negative")


@dataclass(frozen=True, slots=True)
class StageAction:
    """Actions the caller should take when a gate passes or fails."""

    labels: tuple[str, ...] = ()
    advance: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """A single stage in a pipeline."""

    name: str
    description: str
    gate: GateConfig
    validator_ids: tuple[str, ...] = ()
    on_pass: StageAction = field(default_factory=StageAction)
    on_fail: StageAction | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("stage name must be non-empty")


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Complete pipeline definition."""

    version: int
    name: str
    stages: tuple[PipelineStage, ...]

    def __post_init__(self) -> None:
        if self.version != PIPELINE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported pipeline version {self.version}, expected {PIPELINE_SCHEMA_VERSION}"
            )
        if not self.name:
            raise ValueError("pipeline name must be non-empty")
        if not self.stages:
            raise ValueError("pipeline must have at least one stage")
        names = [s.name for s in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")

    def get_stage(self, name: str) -> PipelineStage:
        """Look up a stage by name. Raises KeyError if not found."""
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"no stage named '{name}' in pipeline '{self.name}'")


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Result of evaluating a gate condition."""

    passed: bool
    reason: str
    score: int | None = None
    threshold: int | None = None


@dataclass(frozen=True, slots=True)
class StageResult:
    """Result of running a pipeline stage."""

    stage: str
    gate_decision: GateDecision
    assessment: RunAssessment
    next_stage: str | None
    actions: StageAction

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "gate_decision": {
                "passed": self.gate_decision.passed,
                "reason": self.gate_decision.reason,
                "score": self.gate_decision.score,
                "threshold": self.gate_decision.threshold,
            },
            "next_stage": self.next_stage,
            "actions": {
                "labels": list(self.actions.labels),
                "advance": self.actions.advance,
                "comment": self.actions.comment,
            },
            "assessment": self.assessment.to_dict(),
        }


def evaluate_gate(gate: GateConfig, assessment: RunAssessment) -> GateDecision:
    """Evaluate a gate condition against an assessment.

    For 'human' gates, always returns passed=False with a reason indicating
    human approval is required — the caller enforces this.
    """
    actual_score = assessment.score.overall
    critical_subject_debt = _subject_debt_for_gate(gate, assessment)
    if gate.require_subject_coverage and not assessment.subject_coverage:
        return GateDecision(
            passed=False,
            reason="subject coverage required but no subject coverage was produced",
            score=actual_score,
            threshold=gate.threshold,
        )
    if critical_subject_debt and gate.type in {"score", "score+human"}:
        debt_summary = _format_subject_debt(critical_subject_debt)
        return GateDecision(
            passed=False,
            reason=f"subject coverage debt blocks gate: {debt_summary}",
            score=actual_score,
            threshold=gate.threshold,
        )

    if gate.require_independence:
        unmet_roles = sorted(
            {
                obs.independence.validator_role
                for obs in assessment.observations
                if obs.independence is not None and obs.independence.result == "unmet"
            }
        )
        if unmet_roles:
            return GateDecision(
                passed=False,
                reason=(f"independence requirement unmet for: {', '.join(unmet_roles)}"),
                score=actual_score,
                threshold=gate.threshold,
            )

    if gate.type == "score":
        if gate.threshold is None:
            msg = "score gate requires a threshold"
            raise ValueError(msg)
        passed = actual_score >= gate.threshold
        reason = (
            f"score {actual_score} >= threshold {gate.threshold}"
            if passed
            else f"score {actual_score} < threshold {gate.threshold}"
        )
        return GateDecision(
            passed=passed,
            reason=reason,
            score=actual_score,
            threshold=gate.threshold,
        )

    if gate.type == "human":
        reason = "human approval required"
        subject_debt = _subject_debt_for_gate(gate, assessment, critical_only=False)
        if subject_debt:
            reason = f"{reason}; subject debt: {_format_subject_debt(subject_debt)}"
        return GateDecision(
            passed=False,
            reason=reason,
            score=actual_score,
        )

    # score+human: score must pass, but human approval still needed
    if gate.threshold is None:
        msg = "score+human gate requires a threshold"
        raise ValueError(msg)
    score_ok = actual_score >= gate.threshold
    if not score_ok:
        return GateDecision(
            passed=False,
            reason=f"score {actual_score} < threshold {gate.threshold}",
            score=actual_score,
            threshold=gate.threshold,
        )
    return GateDecision(
        passed=False,
        reason=f"score {actual_score} >= threshold {gate.threshold}; human approval required",
        score=actual_score,
        threshold=gate.threshold,
    )


def _subject_debt_for_gate(
    gate: GateConfig,
    assessment: RunAssessment,
    *,
    critical_only: bool = True,
) -> tuple[str, ...]:
    if not assessment.subject_coverage:
        return ()

    observations_by_fingerprint = {
        observation.issue_fingerprint: observation for observation in assessment.observations
    }
    subjects_by_key = {
        record.subject.subject_key: record.subject for record in assessment.subject_coverage
    }
    debt_states = {
        SubjectCoverageState.PARTIAL,
        SubjectCoverageState.FAILED,
        SubjectCoverageState.SKIPPED,
        SubjectCoverageState.MISSING,
        SubjectCoverageState.UNSUPPORTED,
        SubjectCoverageState.MODEL_LIMITED,
    } - set(gate.allowed_subject_debt)
    debt: list[str] = []
    for record in assessment.subject_coverage:
        if gate.subject_selector is not None and not matches_coverage_selector(
            gate.subject_selector,
            record,
            observations_by_fingerprint,
            subjects_by_key,
        ):
            continue
        if critical_only and record.criticality != "critical":
            continue
        if record.state in debt_states:
            debt.append(f"{record.subject.subject_key}={record.state.value}")
    return tuple(sorted(debt))


def _format_subject_debt(debt: tuple[str, ...]) -> str:
    if len(debt) <= 3:
        return ", ".join(debt)
    return f"{', '.join(debt[:3])}, +{len(debt) - 3} more"


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_pipeline(path: Path) -> PipelineConfig:
    """Load and validate a pipeline configuration file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to read pipeline config: {exc}") from exc

    return _validate(raw, source=str(path))


def load_domain_pipeline(domain_name: str) -> PipelineConfig:
    """Load pipeline config from a domain's ``load_pipeline_config``.

    Discovers the domain through the registry and calls its
    ``load_pipeline_config`` callable.  Raises ``ConfigError`` if
    the domain does not provide pipeline configuration.
    """
    from verdx.engine.runtime.domains import UnknownDomainError, get_domain

    try:
        domain = get_domain(domain_name)
    except UnknownDomainError as exc:
        raise ConfigError(
            f"domain {domain_name!r} is not installed or did not register "
            "its 'verdx.domains' entry point"
        ) from exc

    if domain.load_pipeline_config is None:
        raise ConfigError(
            f"domain {domain_name!r} does not provide pipeline config; use --pipeline"
        )

    raw = domain.load_pipeline_config()
    return _validate(raw, source=f"{domain_name}-pipeline")


def load_default_pipeline() -> PipelineConfig:
    """Assemble the default pipeline from all registered domains.

    The engine no longer knows any domain by name. Each registered
    domain that provides a pipeline config contributes its own stages;
    the default pipeline is their composition. Domains without pipeline
    config are skipped. Stage names are kept unique across domains by
    prefixing collisions with the contributing domain's registered name.

    When two domains contribute stages with the same name, the second one's
    stage is renamed (prefixed with its domain name) AND every transition target
    inside that domain (``on_pass.advance``, ``on_fail.advance``) is rewritten
    with the same old-name -> new-name map, so a stage's transitions keep
    pointing at the right stage after renaming instead of dangling or pointing
    into another domain.

    Raises ``ConfigError`` if no registered domain contributes any stage
    (a pipeline must have at least one stage to be runnable).

    A domain that legitimately provides no pipeline (``load_pipeline_config`` is
    ``None``) is skipped. A domain that provides a genuinely malformed pipeline
    propagates its ``ConfigError`` rather than being silently dropped.
    """
    from verdx.engine.runtime.domains import get_domain, list_domains

    stages: list[PipelineStage] = []
    seen: set[str] = set()
    for domain_name in list_domains():
        domain = get_domain(domain_name)
        # Only skip domains that legitimately contribute no pipeline. A domain
        # that DOES provide config but it is malformed must not be swallowed.
        if domain.load_pipeline_config is None:
            continue

        cfg = _validate(domain.load_pipeline_config(), source=f"{domain_name}-pipeline")

        # Build this domain's old-name -> new-name rename map first, so we can
        # rewrite transitions consistently even for forward references.
        rename: dict[str, str] = {}
        for stage in cfg.stages:
            if stage.name in seen:
                rename[stage.name] = f"{domain_name}:{stage.name}"

        for stage in cfg.stages:
            new_name = rename.get(stage.name, stage.name)
            # on_pass is always present; on_fail may be None.
            new_on_pass = _rewrite_action(stage.on_pass, rename) or stage.on_pass
            stage = replace(
                stage,
                name=new_name,
                on_pass=new_on_pass,
                on_fail=_rewrite_action(stage.on_fail, rename),
            )
            seen.add(new_name)
            stages.append(stage)

    if not stages:
        raise ConfigError(
            "no default pipeline available: no registered domain provides "
            "pipeline config; install a domain or pass --pipeline"
        )

    return PipelineConfig(
        version=PIPELINE_SCHEMA_VERSION,
        name="default",
        stages=tuple(stages),
    )


def _rewrite_action(action: StageAction | None, rename: dict[str, str]) -> StageAction | None:
    """Rewrite an action's transition target through the rename map."""
    if action is None:
        return None
    if action.advance is None or action.advance not in rename:
        return action
    return replace(action, advance=rename[action.advance])


def _validate(raw: Any, source: str) -> PipelineConfig:
    """Validate raw pipeline dict and return typed PipelineConfig."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: pipeline config must be a YAML mapping")

    version = raw.get("version")
    if version != PIPELINE_SCHEMA_VERSION:
        raise ConfigError(
            f"{source}: unsupported pipeline version {version!r}, "
            f"expected {PIPELINE_SCHEMA_VERSION}"
        )

    pipeline = raw.get("pipeline")
    if not isinstance(pipeline, dict):
        raise ConfigError(f"{source}: 'pipeline' key must be a mapping")

    name = pipeline.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{source}: pipeline.name must be a non-empty string")

    raw_stages = pipeline.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ConfigError(f"{source}: pipeline.stages must be a non-empty list")

    stages = tuple(_validate_stage(s, i, source) for i, s in enumerate(raw_stages))

    return PipelineConfig(version=version, name=name, stages=stages)


def _validate_stage(raw: Any, index: int, source: str) -> PipelineStage:
    """Validate a single stage definition."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: stage[{index}] must be a mapping")

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{source}: stage[{index}].name must be a non-empty string")

    description = raw.get("description", "")

    raw_gate = raw.get("gate")
    if not isinstance(raw_gate, dict):
        raise ConfigError(f"{source}: stage '{name}' must have a gate mapping")

    gate = _validate_gate(raw_gate, name, source)

    validator_ids = tuple(raw.get("validators", []))
    for vid in validator_ids:
        if not isinstance(vid, str) or not vid:
            raise ConfigError(f"{source}: stage '{name}' validator IDs must be non-empty strings")

    on_pass = _validate_action(raw.get("on_pass", {}), f"{name}.on_pass", source)
    on_fail_raw = raw.get("on_fail")
    on_fail = _validate_action(on_fail_raw, f"{name}.on_fail", source) if on_fail_raw else None

    return PipelineStage(
        name=name,
        description=description,
        gate=gate,
        validator_ids=validator_ids,
        on_pass=on_pass,
        on_fail=on_fail,
    )


def _validate_gate(raw: dict[str, Any], stage_name: str, source: str) -> GateConfig:
    """Validate a gate configuration."""
    gate_type = raw.get("type")
    if gate_type not in VALID_GATE_TYPES:
        raise ConfigError(
            f"{source}: stage '{stage_name}' gate type must be one of {sorted(VALID_GATE_TYPES)}"
        )

    threshold = raw.get("threshold")
    if gate_type in ("score", "score+human"):
        if not isinstance(threshold, int):
            raise ConfigError(f"{source}: stage '{stage_name}' gate requires integer threshold")
        if not 0 <= threshold <= 100:
            raise ConfigError(f"{source}: stage '{stage_name}' threshold must be 0-100")

    required_approvals = raw.get("required_approvals", 1)
    if not isinstance(required_approvals, int) or required_approvals < 0:
        raise ConfigError(
            f"{source}: stage '{stage_name}' required_approvals must be non-negative int"
        )

    reviewers_from = tuple(raw.get("reviewers_from", []))
    require_independence = raw.get("require_independence", False)
    if not isinstance(require_independence, bool):
        raise ConfigError(f"{source}: stage '{stage_name}' require_independence must be boolean")
    require_subject_coverage = raw.get("require_subject_coverage", False)
    if not isinstance(require_subject_coverage, bool):
        raise ConfigError(
            f"{source}: stage '{stage_name}' require_subject_coverage must be boolean"
        )
    allowed_subject_debt = _validate_allowed_subject_debt(
        raw.get("allowed_subject_debt", []),
        stage_name,
        source,
    )
    subject_selector = _validate_subject_selector(raw.get("subject_selector"), stage_name, source)

    return GateConfig(
        type=gate_type,
        threshold=threshold,
        required_approvals=required_approvals,
        reviewers_from=reviewers_from,
        require_independence=require_independence,
        require_subject_coverage=require_subject_coverage,
        allowed_subject_debt=allowed_subject_debt,
        subject_selector=subject_selector,
    )


def _validate_allowed_subject_debt(
    raw: Any,
    stage_name: str,
    source: str,
) -> tuple[SubjectCoverageState, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: stage '{stage_name}' allowed_subject_debt must be a list")
    states: list[SubjectCoverageState] = []
    for item in raw:
        if not isinstance(item, str):
            raise ConfigError(
                f"{source}: stage '{stage_name}' allowed_subject_debt entries must be strings"
            )
        try:
            states.append(SubjectCoverageState(item))
        except ValueError as exc:
            raise ConfigError(
                f"{source}: stage '{stage_name}' unknown subject debt state {item!r}"
            ) from exc
    return tuple(states)


def _validate_subject_selector(
    raw: Any,
    stage_name: str,
    source: str,
) -> SubjectSelector | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: stage '{stage_name}' subject_selector must be a mapping")
    try:
        return SubjectSelector.from_dict(raw)
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"{source}: stage '{stage_name}' invalid subject_selector: {exc}"
        raise ConfigError(msg) from exc


def _validate_action(raw: Any, context: str, source: str) -> StageAction:
    """Validate an action (on_pass or on_fail)."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context} must be a mapping")

    labels = tuple(raw.get("labels", []))
    advance = raw.get("advance")
    comment = raw.get("comment")

    return StageAction(labels=labels, advance=advance, comment=comment)
