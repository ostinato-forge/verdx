"""Shared helpers for the verdx CLI."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from verdx.contracts.assessment import AssessmentConfidence
from verdx.contracts.dimensions import CoverageStatus
from verdx.engine.lifecycle.transitions import resolve_transition
from verdx.engine.runtime.domains import (
    UnknownDomainError,
    detect_domain,
)
from verdx.engine.runtime.domains import (
    get_domain as _registry_get_domain,
)
from verdx.engine.runtime.engine import RunConfig
from verdx.engine.runtime.pipeline import (
    PipelineConfig,
    StageResult,
    load_default_pipeline,
    load_pipeline,
)
from verdx.engine.runtime.store.actors import ActorDirectory
from verdx.engine.runtime.store.dispositions import DispositionStore
from verdx.engine.runtime.store.history import _target_key_hash
from verdx.engine.runtime.store.lifecycle_store import LocalJsonlBackend
from verdx.engine.validation.independence import ActorFacts

if TYPE_CHECKING:
    from collections.abc import Sequence

    from verdx.contracts.assessment import RunAssessment
    from verdx.contracts.check import CheckSpec
    from verdx.contracts.domain import DomainSpec
    from verdx.contracts.events import EventType
    from verdx.contracts.validator import Validator
    from verdx.engine.lifecycle.reducer import IntentState
    from verdx.engine.runtime.config import DomainConfig
    from verdx.engine.runtime.store.actors import ActorEntry

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "verdx"
_MAX_HASH_FILE_SIZE = 10_485_760  # 10 MB
_PIPELINE_RUN_SCHEMA_VERSION = "verdx.pipeline-run.v1"
_EVIDENCE_SUMMARY_SCHEMA_VERSION = "verdx.evidence-summary.v1"
_EXIT_REQUIRES_HUMAN = 2


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _find_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".agentic").is_dir():
            return parent
        if (parent / ".git").is_dir():
            return parent
    return cwd


def _backend() -> LocalJsonlBackend:
    return LocalJsonlBackend(_find_root())


def _git_short_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_actor() -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_diff_name_only() -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            files = [f for f in result.stdout.strip().splitlines() if f]
            return files if files else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_diff_lines() -> int | None:
    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            total = 0
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0] != "-":
                    total += int(parts[0]) + int(parts[1])
            return total
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _git_diff_digest() -> str | None:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return "sha256:" + hashlib.sha256(result.stdout.encode()).hexdigest()[:16]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def _resolve_next_state(
    intent_state: IntentState | None,
    event_type: EventType,
) -> Any:
    current = intent_state.current_state if intent_state else None

    if event_type.value == "unblocked":
        if intent_state is None or intent_state.pre_block_state is None:
            raise click.ClickException("No pre-block state recorded for this intent.")
        return intent_state.pre_block_state

    next_state = resolve_transition(current, event_type)
    if next_state is None:
        raise click.ClickException(f"No valid transition from '{current}' via '{event_type}'.")
    return next_state


def _print_intent_state(state: IntentState) -> None:
    click.echo(f"Intent: {state.intent_id}")
    click.echo(f"  State: {state.current_state}")
    click.echo(f"  Base:  {state.base_commit}")
    click.echo(f"  Branch: {state.branch}")
    click.echo(f"  Events: {state.event_count}")
    if state.artifacts_created:
        click.echo(f"  Artifacts: {len(state.artifacts_created)}")


def _print_cycle_time(seconds: float) -> None:
    if seconds < 60:
        click.echo(f"  Cycle time: {seconds:.0f}s")
    elif seconds < 3600:
        click.echo(f"  Cycle time: {seconds / 60:.1f} min")
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        click.echo(f"  Cycle time: {h}h {m}m")


# ---------------------------------------------------------------------------
# Domain & config helpers
# ---------------------------------------------------------------------------


def _resolve_domain(name: str) -> DomainSpec:
    try:
        return get_domain(name)
    except UnknownDomainError as exc:
        raise click.ClickException(str(exc)) from exc


def get_domain(name: str) -> DomainSpec:
    """Resolve a domain while preserving the historical CLI patch point."""
    import verdx.engine.runtime.cli as cli

    cli_get_domain = getattr(cli, "get_domain", _registry_get_domain)
    if cli_get_domain is not _registry_get_domain:
        return cli_get_domain(name)
    return _registry_get_domain(name)


def _resolve_domain_for_targets(
    explicit_domain: str | None,
    targets: tuple[str, ...],
) -> DomainSpec:
    if explicit_domain is not None:
        return _resolve_domain(explicit_domain)

    if targets:
        first_target = Path(targets[0])
        detected = detect_domain(first_target)
        if detected is not None:
            return _resolve_domain(detected)

    raise click.ClickException(
        "Cannot auto-detect validator domain for this target. Pass --domain explicitly."
    )


def _resolve_producer_actor(
    directory: ActorDirectory,
    *,
    producer_id: str | None,
    producer_role: str,
) -> ActorFacts | None:
    if producer_id is None:
        return None
    entry = directory.get(producer_id)
    if entry is None:
        return ActorFacts(
            opaque_id=producer_id,
            role=producer_role,
            kind="agent",
        )
    return _actor_facts_from_entry(entry)


def _resolve_validator_actors(
    directory: ActorDirectory,
    validators: Sequence[tuple[object, dict[str, Any]]],
    *,
    enabled: bool,
) -> dict[str, ActorFacts] | None:
    if not enabled:
        return None

    actor_facts: dict[str, ActorFacts] = {}
    for validator, _config in validators:
        raw_validator_id = getattr(validator, "validator_id", None) or getattr(
            validator, "check_id", None
        )
        validator_id = str(raw_validator_id or "unknown")
        entry = directory.get(validator_id)
        if entry is None:
            actor_facts[validator_id] = ActorFacts(
                opaque_id=validator_id,
                role=validator_id,
                kind="agent",
            )
        else:
            actor_facts[validator_id] = _actor_facts_from_entry(entry)
    return actor_facts


def _actor_facts_from_entry(entry: ActorEntry) -> ActorFacts:
    return ActorFacts(
        opaque_id=entry.opaque_id,
        role=entry.role,
        kind=entry.kind,
        team=entry.team,
    )


def _compute_content_hash(target_path: Path) -> str:
    h = hashlib.sha256()
    if target_path.is_file():
        try:
            file_size = target_path.stat().st_size
        except OSError:
            file_size = 0
        if file_size > _MAX_HASH_FILE_SIZE:
            h.update(str(target_path).encode())
            h.update(file_size.to_bytes(8, "little"))
        else:
            with target_path.open("rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
    elif target_path.is_dir():
        for p in sorted(target_path.rglob("*")):
            if p.is_symlink():
                continue
            if p.is_file() and not p.name.startswith("."):
                h.update(str(p.relative_to(target_path)).encode())
                try:
                    file_size = p.stat().st_size
                except OSError:
                    continue
                if file_size > _MAX_HASH_FILE_SIZE:
                    h.update(file_size.to_bytes(8, "little"))
                else:
                    with p.open("rb") as f:
                        while chunk := f.read(65536):
                            h.update(chunk)
    return f"sha256:{h.hexdigest()[:16]}"


def _resolve_validate_output_format(
    *,
    fmt: str | None,
    use_json: bool,
) -> str:
    output_format = fmt or ("json" if use_json else "markdown")
    if use_json and output_format != "json":
        raise click.ClickException("--json cannot be combined with non-json --format")
    return output_format


# ---------------------------------------------------------------------------
# Pipeline & assessment helpers
# ---------------------------------------------------------------------------


def _build_stage_run_config(
    *,
    state_dir: Path,
    domain_cfg: DomainConfig,
    validators: list[tuple[Validator, dict[str, Any]]],
    run_id: str,
    timestamp: str,
    target_path: Path,
    target_key: str,
    content_hash: str,
    producer_role: str,
    producer_id: str | None,
    timeout_seconds: int,
    subject_discovery: Any = None,
) -> RunConfig:
    tkh = _target_key_hash(target_key)
    active_dispositions = DispositionStore(state_dir).get_active(tkh, timestamp)

    actor_directory = ActorDirectory(state_dir)
    producer_actor = _resolve_producer_actor(
        actor_directory,
        producer_id=producer_id,
        producer_role=producer_role,
    )
    validator_actors = _resolve_validator_actors(
        actor_directory,
        validators,
        enabled=producer_actor is not None,
    )

    return RunConfig(
        run_id=run_id,
        target_key=target_key,
        target_path=str(target_path.resolve()),
        target_content_hash=content_hash,
        timestamp=timestamp,
        producer_role=producer_role,
        producer_id=producer_id,
        policy=domain_cfg.policy,
        validators=validators,
        critical_validator_ids=domain_cfg.critical_validators,
        active_dispositions=active_dispositions,
        previous_fingerprints=None,
        previous_score=None,
        producer_actor=producer_actor,
        validator_actors=validator_actors,
        timeout_seconds=timeout_seconds,
        dimension_map=domain_cfg.dimension_map,
        subject_discovery=subject_discovery,
        scoring_config=domain_cfg.scoring_config,
    )


def _is_human_gate_stop(stage: Any, result: StageResult) -> bool:
    if "human approval required" not in result.gate_decision.reason:
        return False
    if stage.gate.type == "human":
        return True
    if stage.gate.type != "score+human":
        return False

    score = result.gate_decision.score
    threshold = result.gate_decision.threshold
    return score is not None and threshold is not None and score >= threshold


# ---------------------------------------------------------------------------
# Pipeline formatting helpers
# ---------------------------------------------------------------------------


def _pipeline_run_to_dict(
    *,
    domain: str,
    pipeline: PipelineConfig,
    target_key: str,
    target_content_hash: str,
    from_stage: str,
    started_at: str,
    completed_at: str,
    status: str,
    stop_stage: str | None,
    stop_reason: str,
    results: list[StageResult],
) -> dict[str, Any]:
    return {
        "schema_version": _PIPELINE_RUN_SCHEMA_VERSION,
        "domain": domain,
        "pipeline": pipeline.name,
        "target_key": target_key,
        "target_content_hash": target_content_hash,
        "from_stage": from_stage,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "stop_stage": stop_stage,
        "stop_reason": stop_reason,
        "stateless": True,
        "stages": [result.to_dict() for result in results],
    }


def _print_pipeline_run(payload: dict[str, Any], results: list[StageResult]) -> None:
    click.echo(f"Pipeline: {payload['pipeline']}")
    click.echo(f"Domain: {payload['domain']}")
    click.echo(f"Target: {payload['target_key']}")
    click.echo("Mode: stateless v1 (stage results are not persisted)")
    click.echo()
    if results:
        for index, result in enumerate(results):
            if index:
                click.echo()
            _print_stage_result(result)
    else:
        click.echo("No automated stages executed.")
    click.echo()
    click.echo(f"Pipeline status: {payload['status']}")
    if payload["stop_stage"] is not None:
        click.echo(f"Stopped at stage: {payload['stop_stage']}")
    click.echo(f"Stopped: {payload['stop_reason']}")


def _print_stage_result(result: StageResult) -> None:
    gd = result.gate_decision
    status = _stage_result_display_status(result)
    click.echo(f"Stage: {result.stage}")
    click.echo(f"Result: {status}")
    click.echo(f"Reason: {gd.reason}")
    if gd.score is not None:
        click.echo(f"Score: {gd.score}")
    if result.next_stage:
        click.echo(f"Next stage: {result.next_stage}")
    if result.actions.labels:
        click.echo(f"Labels: {', '.join(result.actions.labels)}")
    obs_count = len(result.assessment.observations)
    if obs_count:
        click.echo(f"Observations: {obs_count}")


def _stage_result_display_status(result: StageResult) -> str:
    if result.gate_decision.passed:
        return "PASSED"
    if "human approval required" in result.gate_decision.reason:
        return "REQUIRES HUMAN"
    return "BLOCKED"


def _stage_evidence_gate_passed(result: StageResult) -> bool:
    threshold = result.gate_decision.threshold
    if threshold is None:
        return result.assessment.confidence != AssessmentConfidence.LOW and (
            result.assessment.coverage_status in (CoverageStatus.FULL, CoverageStatus.PARTIAL)
        )
    return _assessment_gate_passed(result.assessment, threshold)


def _stage_summary(result: StageResult) -> dict[str, Any]:
    assessment = result.assessment
    blocking_findings = sum(
        1 for obs in assessment.observations if obs.escalation.value in {"L3", "L4"}
    )
    awareness_items = len(assessment.observations) - blocking_findings
    coverage_ok = assessment.coverage_status in (CoverageStatus.FULL, CoverageStatus.PARTIAL)
    confidence_ok = assessment.confidence != AssessmentConfidence.LOW

    return {
        "stage": result.stage,
        "result": _stage_result_display_status(result).lower().replace(" ", "-"),
        "gate_passed": result.gate_decision.passed,
        "gate_reason": result.gate_decision.reason,
        "score": assessment.score.overall,
        "threshold": result.gate_decision.threshold,
        "evidence_gate_passed": _stage_evidence_gate_passed(result),
        "coverage": assessment.coverage_status.value,
        "coverage_ok": coverage_ok,
        "confidence": assessment.confidence.value,
        "confidence_ok": confidence_ok,
        "checks_run": assessment.checks_run,
        "checks_passed": assessment.checks_passed,
        "validators_run": len(assessment.validator_statuses),
        "validators_clean": assessment.validators_clean,
        "observations": len(assessment.observations),
        "blocking_findings": blocking_findings,
        "awareness_items": awareness_items,
        "recovery_items": len(assessment.recovery_items),
        "coverage_gaps": len(assessment.coverage_gaps),
        "actions": {
            "labels": list(result.actions.labels),
            "advance": result.actions.advance,
            "comment": result.actions.comment,
        },
    }


def _evidence_summary_contradictions(
    payload: dict[str, Any],
    results: list[StageResult],
    stages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    contradictions: list[dict[str, str]] = []
    for result, stage in zip(results, stages, strict=True):
        if stage["gate_passed"] and not stage["evidence_gate_passed"]:
            contradictions.append(
                {
                    "stage": result.stage,
                    "code": "stage-gate-passed-with-failed-evidence",
                    "message": (
                        "Stage gate passed on score, but coverage or confidence prevents "
                        "the deterministic evidence gate from passing."
                    ),
                }
            )

    status = payload["status"]
    if status == "completed" and payload.get("stop_stage") is not None:
        contradictions.append(
            {
                "stage": payload["stop_stage"],
                "code": "completed-with-stop-stage",
                "message": "Pipeline status is completed but stop_stage is set.",
            }
        )
    if status == "blocked" and not any(stage["result"] == "blocked" for stage in stages):
        contradictions.append(
            {
                "stage": payload.get("stop_stage") or "",
                "code": "blocked-without-blocking-stage",
                "message": "Pipeline status is blocked but no stage summary is blocked.",
            }
        )
    if status == "requires-human" and "human approval required" not in payload["stop_reason"]:
        contradictions.append(
            {
                "stage": payload.get("stop_stage") or "",
                "code": "requires-human-without-human-reason",
                "message": (
                    "Pipeline status requires human review but stop_reason does not say why."
                ),
            }
        )
    return contradictions


def _evidence_summary_caveats(stages: list[dict[str, Any]]) -> list[dict[str, str]]:
    caveats: list[dict[str, str]] = []
    for stage in stages:
        stage_name = str(stage["stage"])
        if stage["coverage"] == CoverageStatus.PARTIAL.value:
            caveats.append(
                {
                    "stage": stage_name,
                    "code": "partial-coverage",
                    "message": "Coverage is partial; review degraded validators before release.",
                }
            )
        if stage["coverage_gaps"]:
            caveats.append(
                {
                    "stage": stage_name,
                    "code": "coverage-gaps",
                    "message": f"{stage['coverage_gaps']} coverage gap(s) were reported.",
                }
            )
        if stage["recovery_items"]:
            caveats.append(
                {
                    "stage": stage_name,
                    "code": "recovery-items",
                    "message": f"{stage['recovery_items']} recovery item(s) were reported.",
                }
            )
    return caveats


def _evidence_summary_next_actions(summary: dict[str, Any]) -> list[str]:
    if summary["contradictions"]:
        return ["Resolve deterministic evidence contradictions before release."]
    if summary["blocked"]:
        stop_stage = summary["stop_stage"] or "the blocked stage"
        return [f"Fix blocking findings from {stop_stage} and rerun verdx evidence-summary."]
    if summary["requires_human"]:
        stop_stage = summary["stop_stage"] or "the next gate"
        return [f"Route the evidence summary to a human reviewer for {stop_stage}."]
    if summary["release_ready"]:
        return ["Release gate passed; proceed with the release workflow."]
    return ["Review evidence caveats before release."]


def _pipeline_evidence_summary_to_dict(
    payload: dict[str, Any],
    results: list[StageResult],
) -> dict[str, Any]:
    stages = [_stage_summary(result) for result in results]
    totals = {
        "checks_run": sum(stage["checks_run"] for stage in stages),
        "checks_passed": sum(stage["checks_passed"] for stage in stages),
        "observations": sum(stage["observations"] for stage in stages),
        "blocking_findings": sum(stage["blocking_findings"] for stage in stages),
        "awareness_items": sum(stage["awareness_items"] for stage in stages),
        "recovery_items": sum(stage["recovery_items"] for stage in stages),
        "coverage_gaps": sum(stage["coverage_gaps"] for stage in stages),
    }
    contradictions = _evidence_summary_contradictions(payload, results, stages)
    release_ready = (
        payload["status"] == "completed"
        and not contradictions
        and all(stage["evidence_gate_passed"] for stage in stages)
    )

    summary: dict[str, Any] = {
        "schema_version": _EVIDENCE_SUMMARY_SCHEMA_VERSION,
        "domain": payload["domain"],
        "pipeline": payload["pipeline"],
        "target_key": payload["target_key"],
        "target_content_hash": payload["target_content_hash"],
        "from_stage": payload["from_stage"],
        "started_at": payload["started_at"],
        "completed_at": payload["completed_at"],
        "status": payload["status"],
        "stop_stage": payload["stop_stage"],
        "stop_reason": payload["stop_reason"],
        "release_ready": release_ready,
        "requires_human": payload["status"] == "requires-human",
        "blocked": payload["status"] == "blocked",
        "stage_count": len(stages),
        "stages": stages,
        "totals": totals,
        "contradictions": contradictions,
        "caveats": _evidence_summary_caveats(stages),
    }
    summary["next_actions"] = _evidence_summary_next_actions(summary)
    return summary


def _print_evidence_summary(summary: dict[str, Any]) -> None:
    click.echo(f"Evidence summary: {summary['pipeline']}")
    click.echo(f"Domain: {summary['domain']}")
    click.echo(f"Target: {summary['target_key']}")
    click.echo(f"Status: {summary['status']}")
    click.echo(f"Release ready: {'yes' if summary['release_ready'] else 'no'}")
    if summary["stop_stage"] is not None:
        click.echo(f"Stopped at stage: {summary['stop_stage']}")
    click.echo(f"Stopped: {summary['stop_reason']}")
    click.echo()

    if summary["stages"]:
        click.echo("Stages:")
        for stage in summary["stages"]:
            click.echo(
                f"  {stage['stage']}: {stage['result']} "
                f"score={stage['score']} blockers={stage['blocking_findings']} "
                f"coverage={stage['coverage']} confidence={stage['confidence']}"
            )
    else:
        click.echo("Stages: none")

    if summary["contradictions"]:
        click.echo()
        click.echo("Contradictions:")
        for item in summary["contradictions"]:
            click.echo(f"  {item['code']}: {item['message']}")

    if summary["caveats"]:
        click.echo()
        click.echo("Caveats:")
        for item in summary["caveats"]:
            click.echo(f"  {item['code']}: {item['message']}")

    click.echo()
    click.echo("Next actions:")
    for action in summary["next_actions"]:
        click.echo(f"  - {action}")


def _load_cli_pipeline(pipeline_path: str | None) -> PipelineConfig:
    if pipeline_path:
        return load_pipeline(Path(pipeline_path))
    return load_default_pipeline()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _assessment_gate_passed(assessment: RunAssessment, threshold: int) -> bool:
    from verdx.engine.validation.gate import passes_gate

    return passes_gate(assessment, threshold)


def _quick_profiles(profiles: tuple[str, ...]) -> tuple[str, ...]:
    return ("quick", *(profile for profile in profiles if profile != "quick"))


def _resolve_validator(check: CheckSpec) -> tuple[Any, dict[str, Any]]:
    """Resolve a CheckSpec's PythonAdapter to a (callable, config) tuple."""
    import importlib

    from verdx.contracts.check import PythonAdapter

    adapter = check.adapter
    if not isinstance(adapter, PythonAdapter):
        raise ValueError(f"Cannot resolve non-Python adapter for check {check.check_id}")
    module_path, qualname = adapter.import_path.split(":", 1)
    mod = importlib.import_module(module_path)
    validator = getattr(mod, qualname)
    return validator, dict(check.default_config)
