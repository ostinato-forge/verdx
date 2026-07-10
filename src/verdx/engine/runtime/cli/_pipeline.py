"""Pipeline commands: assess, run, evidence-summary."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import (
    _EXIT_REQUIRES_HUMAN,
    DEFAULT_STATE_DIR,
    _build_stage_run_config,
    _compute_content_hash,
    _is_human_gate_stop,
    _load_cli_pipeline,
    _pipeline_evidence_summary_to_dict,
    _pipeline_run_to_dict,
    _print_evidence_summary,
    _print_pipeline_run,
    _print_stage_result,
    _resolve_domain,
    _resolve_validator,
)
from verdx.engine.runtime.config import load_config
from verdx.engine.runtime.domains import list_domains
from verdx.engine.runtime.engine import assess_stage

if TYPE_CHECKING:
    from verdx.contracts.domain import DomainSpec
    from verdx.engine.runtime.config import DomainConfig
    from verdx.engine.runtime.pipeline import PipelineConfig, StageResult


# ---------------------------------------------------------------------------
# Shared pipeline walk
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineWalkResult:
    results: list[StageResult] = field(default_factory=list)
    status: str = "completed"
    stop_stage: str | None = None
    stop_reason: str = "pipeline completed"
    started_at: str = ""
    completed_at: str = ""


def _walk_pipeline(
    *,
    pipeline_cfg: PipelineConfig,
    start_stage: str,
    state_dir: Path,
    domain_cfg: DomainConfig,
    domain: DomainSpec,
    target_path: Path,
    target_key: str,
    content_hash: str,
    producer_role: str,
    producer_id: str | None,
    timeout: int,
) -> PipelineWalkResult:
    """Walk a pipeline from start_stage until a gate stops it or it completes."""
    started_at = datetime.now(tz=UTC).isoformat()
    validators = [_resolve_validator(check) for check in domain.load_checks()]

    results: list[StageResult] = []
    visited: set[str] = set()
    current_stage: str | None = start_stage
    run_status = "completed"
    stop_reason = "pipeline completed"
    stop_stage: str | None = None

    while current_stage is not None:
        if current_stage in visited:
            raise click.ClickException(f"pipeline cycle detected at stage '{current_stage}'")
        visited.add(current_stage)

        pipeline_stage = pipeline_cfg.get_stage(current_stage)
        if pipeline_stage.gate.type == "human":
            run_status = "requires-human"
            stop_stage = pipeline_stage.name
            stop_reason = f"human approval required at stage '{pipeline_stage.name}'"
            break

        run_timestamp = datetime.now(tz=UTC).isoformat()
        run_config = _build_stage_run_config(
            state_dir=state_dir,
            domain_cfg=domain_cfg,
            validators=validators,
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            timestamp=run_timestamp,
            target_path=target_path,
            target_key=target_key,
            content_hash=content_hash,
            producer_role=producer_role,
            producer_id=producer_id,
            timeout_seconds=timeout,
            subject_discovery=domain.discover_subjects,
        )
        result = assess_stage(current_stage, pipeline_cfg, run_config)
        results.append(result)

        if not result.gate_decision.passed:
            run_status = (
                "requires-human" if _is_human_gate_stop(pipeline_stage, result) else "blocked"
            )
            stop_stage = pipeline_stage.name
            stop_reason = result.gate_decision.reason
            break

        if result.next_stage is None:
            break

        current_stage = result.next_stage

    return PipelineWalkResult(
        results=results,
        status=run_status,
        stop_stage=stop_stage,
        stop_reason=stop_reason,
        started_at=started_at,
        completed_at=datetime.now(tz=UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _require_domain(domain: str | None) -> str:
    """Ensure a domain was passed, else raise a click usage error.

    The engine names no domain, so pipeline commands cannot default one.
    When ``--domain`` is omitted, list the registered domains and tell
    the user to pass it explicitly.
    """
    if domain is not None:
        return domain
    available = ", ".join(list_domains()) or "(none registered)"
    raise click.UsageError(f"Pass --domain explicitly. Available domains: {available}.")


@main.command(name="assess")
@click.option("--stage", required=True, help="Pipeline stage to evaluate.")
@click.option("--target", required=True, help="Target to validate.")
@click.option("--domain", default=None, help="Validator domain.")
@click.option("--target-key", default=None, help="Override target key.")
@click.option(
    "--producer-role",
    default="content-developer",
    help="Role that produced the target being assessed.",
)
@click.option(
    "--producer-id",
    default=None,
    help="Opaque producer actor ID to resolve through the actor directory.",
)
@click.option("--pipeline", "pipeline_path", default=None, help="Pipeline config file.")
@click.option("--config", "config_path", default=None, help="Domain config file.")
@click.option("--state-dir", default=None, help="State directory path.")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Per-validator timeout (seconds).",
)
def assess_cmd(
    stage: str,
    target: str,
    domain: str | None,
    target_key: str | None,
    producer_role: str,
    producer_id: str | None,
    pipeline_path: str | None,
    config_path: str | None,
    state_dir: str | None,
    use_json: bool,
    timeout: int,
) -> None:
    """Run pipeline stage assessment."""
    target_path = Path(target)
    if not target_path.exists():
        raise click.ClickException(f"Target not found: {target_path}")

    resolved_domain = _resolve_domain(_require_domain(domain))
    pipeline_cfg = _load_cli_pipeline(pipeline_path)
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    domain_cfg = (
        load_config(Path(config_path)) if config_path else resolved_domain.load_default_config()
    )

    validators = [_resolve_validator(check) for check in resolved_domain.load_checks()]
    resolved_target_key = target_key or str(target_path.resolve())
    run_timestamp = datetime.now(tz=UTC).isoformat()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    content_hash = _compute_content_hash(target_path)

    run_config = _build_stage_run_config(
        state_dir=resolved_state_dir,
        domain_cfg=domain_cfg,
        validators=validators,
        run_id=run_id,
        timestamp=run_timestamp,
        target_path=target_path,
        target_key=resolved_target_key,
        content_hash=content_hash,
        producer_role=producer_role,
        producer_id=producer_id,
        timeout_seconds=timeout,
        subject_discovery=resolved_domain.discover_subjects,
    )

    result = assess_stage(stage, pipeline_cfg, run_config)

    if use_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        _print_stage_result(result)


@main.command(name="run")
@click.option("--target", required=True, help="Target to validate.")
@click.option("--domain", default=None, help="Validator domain.")
@click.option("--target-key", default=None, help="Override target key.")
@click.option(
    "--producer-role",
    default="content-developer",
    help="Role that produced the target being assessed.",
)
@click.option(
    "--producer-id",
    default=None,
    help="Opaque producer actor ID to resolve through the actor directory.",
)
@click.option("--pipeline", "pipeline_path", default=None, help="Pipeline config file.")
@click.option("--config", "config_path", default=None, help="Domain config file.")
@click.option(
    "--from-stage",
    default=None,
    help="Start at a stage (stateless v1; does not resume approval history).",
)
@click.option("--state-dir", default=None, help="State directory path.")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Per-validator timeout (seconds).",
)
def run_cmd(
    target: str,
    domain: str | None,
    target_key: str | None,
    producer_role: str,
    producer_id: str | None,
    pipeline_path: str | None,
    config_path: str | None,
    from_stage: str | None,
    state_dir: str | None,
    use_json: bool,
    timeout: int,
) -> None:
    """Run a pipeline until a gate stops it."""
    target_path = Path(target)
    if not target_path.exists():
        raise click.ClickException(f"Target not found: {target_path}")

    resolved_domain = _resolve_domain(_require_domain(domain))
    pipeline_cfg = _load_cli_pipeline(pipeline_path)
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    domain_cfg = (
        load_config(Path(config_path)) if config_path else resolved_domain.load_default_config()
    )

    resolved_target_key = target_key or str(target_path.resolve())
    content_hash = _compute_content_hash(target_path)
    start_stage = from_stage or pipeline_cfg.stages[0].name

    walk = _walk_pipeline(
        pipeline_cfg=pipeline_cfg,
        start_stage=start_stage,
        state_dir=resolved_state_dir,
        domain_cfg=domain_cfg,
        domain=resolved_domain,
        target_path=target_path,
        target_key=resolved_target_key,
        content_hash=content_hash,
        producer_role=producer_role,
        producer_id=producer_id,
        timeout=timeout,
    )

    payload = _pipeline_run_to_dict(
        domain=resolved_domain.name,
        pipeline=pipeline_cfg,
        target_key=resolved_target_key,
        target_content_hash=content_hash,
        from_stage=start_stage,
        started_at=walk.started_at,
        completed_at=walk.completed_at,
        status=walk.status,
        stop_stage=walk.stop_stage,
        stop_reason=walk.stop_reason,
        results=walk.results,
    )

    if use_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        _print_pipeline_run(payload, walk.results)

    if walk.status == "blocked":
        raise SystemExit(1)
    if walk.status == "requires-human":
        raise SystemExit(_EXIT_REQUIRES_HUMAN)


@main.command(name="evidence-summary")
@click.option("--target", required=True, help="Target to validate.")
@click.option("--domain", default=None, help="Validator domain.")
@click.option("--target-key", default=None, help="Override target key.")
@click.option(
    "--producer-role",
    default="content-developer",
    help="Role that produced the target being assessed.",
)
@click.option(
    "--producer-id",
    default=None,
    help="Opaque producer actor ID to resolve through the actor directory.",
)
@click.option("--pipeline", "pipeline_path", default=None, help="Pipeline config file.")
@click.option("--config", "config_path", default=None, help="Domain config file.")
@click.option(
    "--from-stage",
    default=None,
    help="Start at a stage (stateless v1; does not resume approval history).",
)
@click.option("--state-dir", default=None, help="State directory path.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Per-validator timeout (seconds).",
)
def evidence_summary_cmd(
    target: str,
    domain: str | None,
    target_key: str | None,
    producer_role: str,
    producer_id: str | None,
    pipeline_path: str | None,
    config_path: str | None,
    from_stage: str | None,
    state_dir: str | None,
    fmt: str,
    timeout: int,
) -> None:
    """Run release pipeline and emit compact deterministic evidence."""
    target_path = Path(target)
    if not target_path.exists():
        raise click.ClickException(f"Target not found: {target_path}")

    resolved_domain = _resolve_domain(_require_domain(domain))
    pipeline_cfg = _load_cli_pipeline(pipeline_path)
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    domain_cfg = (
        load_config(Path(config_path)) if config_path else resolved_domain.load_default_config()
    )

    resolved_target_key = target_key or str(target_path.resolve())
    content_hash = _compute_content_hash(target_path)
    start_stage = from_stage or pipeline_cfg.stages[0].name

    walk = _walk_pipeline(
        pipeline_cfg=pipeline_cfg,
        start_stage=start_stage,
        state_dir=resolved_state_dir,
        domain_cfg=domain_cfg,
        domain=resolved_domain,
        target_path=target_path,
        target_key=resolved_target_key,
        content_hash=content_hash,
        producer_role=producer_role,
        producer_id=producer_id,
        timeout=timeout,
    )

    payload = _pipeline_run_to_dict(
        domain=resolved_domain.name,
        pipeline=pipeline_cfg,
        target_key=resolved_target_key,
        target_content_hash=content_hash,
        from_stage=start_stage,
        started_at=walk.started_at,
        completed_at=walk.completed_at,
        status=walk.status,
        stop_stage=walk.stop_stage,
        stop_reason=walk.stop_reason,
        results=walk.results,
    )
    summary = _pipeline_evidence_summary_to_dict(payload, walk.results)

    if fmt == "json":
        click.echo(json.dumps(summary, indent=2))
    else:
        _print_evidence_summary(summary)

    if summary["contradictions"] or summary["blocked"]:
        raise SystemExit(1)
    if summary["requires_human"]:
        raise SystemExit(_EXIT_REQUIRES_HUMAN)
