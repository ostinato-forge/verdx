"""Validation commands: validate, quick."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from verdx.contracts.validator import Validator

import click

from verdx.contracts.plan import PlanError
from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import (
    DEFAULT_STATE_DIR,
    _assessment_gate_passed,
    _compute_content_hash,
    _quick_profiles,
    _resolve_domain_for_targets,
    _resolve_producer_actor,
    _resolve_validate_output_format,
    _resolve_validator_actors,
)
from verdx.engine.runtime.config import load_config, load_escalation_policy
from verdx.engine.runtime.engine import RunConfig, RunOutcome, run_validation
from verdx.engine.runtime.hooks import HookRegistry
from verdx.engine.runtime.store.actors import ActorDirectory
from verdx.engine.runtime.store.cache import CacheStore
from verdx.engine.runtime.store.dispositions import DispositionStore
from verdx.engine.runtime.store.history import (
    HistoryStore,
    ObservationRecord,
    RunRecord,
    _target_key_hash,
)
from verdx.engine.runtime.store.runs import RunStore
from verdx.engine.runtime.workflow import run_gate_workflow
from verdx.engine.validation.reporting import render_junit, render_sarif


@main.command(name="validate")
@click.argument("targets", nargs=-1, required=True)
@click.option("--domain", default=None, help="Validator domain (auto-detected if omitted).")
@click.option("--target-key", default=None, help="Override target key.")
@click.option(
    "--producer-role",
    default="content-author",
    help="Role that produced the target being validated.",
)
@click.option(
    "--producer-id",
    default=None,
    help="Opaque producer actor ID to resolve through the actor directory.",
)
@click.option("--config", "config_path", default=None, help="Domain config file.")
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    help="Named domain profile to apply (repeatable).",
)
@click.option("--state-dir", default=None, help="State directory path.")
@click.option("--policy", default=None, help="Escalation policy file.")
@click.option(
    "--threshold",
    type=int,
    default=80,
    help="Minimum score required for exit 0 (default: 80).",
)
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "json", "junit", "sarif", "agent"]),
    default=None,
    help="Output format (default: markdown; --json aliases --format json).",
)
@click.option(
    "--no-history",
    is_flag=True,
    default=False,
    help="Skip history append.",
)
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Per-validator timeout (seconds).",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Skip result cache (always re-validate).",
)
@click.option(
    "--isolate",
    is_flag=True,
    default=False,
    help="Run each validator in a separate subprocess.",
)
@click.option(
    "--static-only",
    is_flag=True,
    default=False,
    help="Skip live validators (SSH to workstation). By default, live validators run.",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    hidden=True,
    help="Deprecated no-op (live is now the default).",
)
@click.option(
    "--host",
    "-h",
    "workstation_host",
    default=None,
    help="Workstation hostname for live validation (default: workstation).",
)
@click.option(
    "--user",
    "-u",
    "workstation_user",
    default=None,
    help="SSH user for live validation (default: student).",
)
@click.option(
    "--password",
    default=None,
    help="SSH password (uses sshpass; default: key-based auth).",
)
@click.option(
    "--proxy",
    default=None,
    help="SOCKS5 proxy host for SSH (e.g. localhost:1080).",
)
@click.option(
    "--exercise",
    "-e",
    "exercises",
    multiple=True,
    help="Specific exercise(s) for live validation (repeatable).",
)
def validate_cmd(
    targets: tuple[str, ...],
    domain: str | None,
    target_key: str | None,
    producer_role: str,
    producer_id: str | None,
    config_path: str | None,
    profiles: tuple[str, ...],
    state_dir: str | None,
    policy: str | None,
    threshold: int,
    use_json: bool,
    fmt: str | None,
    no_history: bool,
    timeout: int,
    no_cache: bool,
    isolate: bool,
    static_only: bool,
    live: bool,
    workstation_host: str | None,
    workstation_user: str | None,
    password: str | None,
    proxy: str | None,
    exercises: tuple[str, ...],
) -> None:
    """Run domain validators against one or more targets."""
    if len(targets) > 1 and target_key is not None:
        raise click.ClickException("--target-key cannot be used with multiple targets")

    resolved_domain = _resolve_domain_for_targets(domain, targets)

    if threshold < 0 or threshold > 100:
        raise click.ClickException("--threshold must be between 0 and 100")
    if isolate and profiles:
        raise click.ClickException("--profile cannot be combined with --isolate")

    output_format = _resolve_validate_output_format(fmt=fmt, use_json=use_json)
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR

    domain_cfg = (
        load_config(Path(config_path)) if config_path else resolved_domain.load_default_config()
    )
    resolved_policy = load_escalation_policy(policy) if policy else domain_cfg.policy

    # Resolve validators from domain's load_checks() interface
    validators: list[tuple[Validator, dict[str, Any]]] = []
    live_config: dict[str, Any] = {}
    if isolate:
        from verdx.engine.runtime.cli._helpers import _resolve_validator

        checks = resolved_domain.load_checks()
        if static_only:
            validators = [_resolve_validator(c) for c in checks if "live" not in c.capabilities]
        else:
            static_checks = [c for c in checks if "live" not in c.capabilities]
            live_checks = [c for c in checks if "live" in c.capabilities]
            if live_checks:
                if workstation_host:
                    live_config["workstation_host"] = workstation_host
                if workstation_user:
                    live_config["workstation_user"] = workstation_user
                if password:
                    live_config["workstation_password"] = password
                if proxy:
                    parts = proxy.split(":", 1)
                    live_config["proxy_host"] = parts[0]
                    if len(parts) > 1:
                        live_config["proxy_port"] = int(parts[1])
                if exercises:
                    live_config["exercises"] = list(exercises)
                live_pairs = [_resolve_validator(c) for c in live_checks]
                live_resolved = [(v, {**vc, **live_config}) for v, vc in live_pairs]
                validators = live_resolved + [_resolve_validator(c) for c in static_checks]
            else:
                validators = [_resolve_validator(c) for c in static_checks]
    else:
        if workstation_host:
            live_config["workstation_host"] = workstation_host
        if workstation_user:
            live_config["workstation_user"] = workstation_user
        if password:
            live_config["workstation_password"] = password
        if proxy:
            parts = proxy.split(":", 1)
            live_config["proxy_host"] = parts[0]
            if len(parts) > 1:
                live_config["proxy_port"] = int(parts[1])
        if exercises:
            live_config["exercises"] = list(exercises)

    if isolate:
        from verdx.engine.runtime.isolation import SubprocessValidator

        validators = [
            (
                cast(
                    "Validator",
                    SubprocessValidator(
                        validator_id=getattr(v, "validator_id", "unknown"),
                        validator_version=getattr(v, "validator_version", "0.0.0"),
                        domain=resolved_domain.name,
                        timeout_seconds=timeout,
                    ),
                ),
                vc,
            )
            for v, vc in validators
        ]

    actor_directory = ActorDirectory(resolved_state_dir)
    producer_actor = _resolve_producer_actor(
        actor_directory,
        producer_id=producer_id,
        producer_role=producer_role,
    )
    actor_sources: list[tuple[object, dict[str, Any]]]
    if validators:
        actor_sources = [(validator, config) for validator, config in validators]
    else:
        actor_sources = [(check, {}) for check in resolved_domain.load_checks()]
    validator_actors = _resolve_validator_actors(
        actor_directory,
        actor_sources,
        enabled=producer_actor is not None,
    )
    cache = CacheStore(resolved_state_dir)
    hook_registry = HookRegistry(resolved_state_dir)

    outcomes: list[tuple[str, RunOutcome, bool]] = []

    for target in targets:
        target_path = Path(target)
        if not target_path.exists():
            raise click.ClickException(f"Target not found: {target_path}")

        resolved_target_key = target_key or str(target_path.resolve())
        click.echo(f"verdx: validating {resolved_target_key}", err=True)

        run_timestamp = datetime.now(tz=UTC).isoformat()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        content_hash = _compute_content_hash(target_path)

        history = HistoryStore(resolved_state_dir)
        previous_runs = history.get_runs(resolved_target_key)
        previous_score = previous_runs[-1].score if previous_runs else None
        previous_fingerprints = history.get_previous_fingerprints(resolved_target_key)

        tkh = _target_key_hash(resolved_target_key)
        active_dispositions = DispositionStore(resolved_state_dir).get_active(
            tkh,
            run_timestamp,
        )

        run_store = RunStore(resolved_state_dir)
        run_store.create_run(run_id, run_timestamp)

        try:
            if isolate:
                run_config = RunConfig(
                    run_id=run_id,
                    target_key=resolved_target_key,
                    target_path=str(target_path.resolve()),
                    target_content_hash=content_hash,
                    timestamp=run_timestamp,
                    producer_role=producer_role,
                    producer_id=producer_id,
                    policy=resolved_policy,
                    validators=validators,
                    critical_validator_ids=domain_cfg.critical_validators,
                    active_dispositions=active_dispositions,
                    previous_fingerprints=previous_fingerprints,
                    previous_score=previous_score,
                    producer_actor=producer_actor,
                    validator_actors=validator_actors,
                    timeout_seconds=timeout,
                    dimension_map=domain_cfg.dimension_map,
                    subject_discovery=resolved_domain.discover_subjects,
                    scoring_config=domain_cfg.scoring_config,
                    no_cache=no_cache,
                )
                outcome = run_validation(
                    run_config,
                    cache_store=cache,
                    hook_registry=hook_registry,
                )
            else:
                workflow_overrides: dict[str, Any] = {**live_config}
                outcome = run_gate_workflow(
                    domain=resolved_domain,
                    target=target_path,
                    target_key=resolved_target_key,
                    state_dir=resolved_state_dir,
                    policy=resolved_policy,
                    threshold=threshold,
                    static_only=static_only,
                    no_cache=no_cache,
                    active_dispositions=active_dispositions,
                    producer_role=producer_role,
                    producer_id=producer_id,
                    producer_actor=producer_actor,
                    validator_actors=validator_actors,
                    previous_fingerprints=previous_fingerprints,
                    previous_score=previous_score,
                    scoring_config=domain_cfg.scoring_config,
                    dimension_map=domain_cfg.dimension_map,
                    critical_validator_ids=domain_cfg.critical_validators,
                    cli_overrides=workflow_overrides or None,
                    profiles=profiles,
                    run_id=run_id,
                    timestamp=run_timestamp,
                )
        except PlanError as exc:
            run_store.crash_run(run_id, datetime.now(tz=UTC).isoformat(), str(exc))
            raise click.ClickException(str(exc)) from exc
        except Exception as exc:
            run_store.crash_run(run_id, datetime.now(tz=UTC).isoformat(), str(exc))
            raise

        subject_coverage_json = None
        if outcome.assessment.subject_coverage:
            subject_coverage_json = json.dumps(
                [record.to_dict() for record in outcome.assessment.subject_coverage],
                indent=2,
                sort_keys=True,
            )

        run_store.complete_run(
            run_id,
            datetime.now(tz=UTC).isoformat(),
            assessment_json=outcome.assessment_json,
            report_md=outcome.report_md,
            subject_coverage_json=subject_coverage_json,
        )

        if not no_history:
            history.append_run(
                resolved_target_key,
                RunRecord(
                    run_id=run_id,
                    timestamp=run_timestamp,
                    target_content_hash=content_hash,
                    score=outcome.assessment.score.overall,
                    observation_count=len(outcome.observations),
                ),
            )
            history.append_observations(
                resolved_target_key,
                [
                    ObservationRecord(
                        run_id=run_id,
                        observation_id=observation.observation_id,
                        issue_fingerprint=observation.issue_fingerprint,
                        target_content_hash=content_hash,
                        rule_id=observation.rule_id,
                        category=observation.category,
                        severity=observation.severity.value,
                        escalation=observation.escalation.value,
                    )
                    for observation in outcome.observations
                ],
            )

        passed = _assessment_gate_passed(outcome.assessment, threshold)
        outcomes.append((resolved_target_key, outcome, passed))

    if len(outcomes) > 1:
        click.echo("", err=True)
        click.echo("verdx: batch summary", err=True)
        for tkey, oc, ok in outcomes:
            status_label = "PASS" if ok else "FAIL"
            click.echo(f"  {tkey}  {oc.assessment.score.overall}/100  {status_label}", err=True)
        n_passed = sum(1 for _, _, ok in outcomes if ok)
        click.echo(
            f"  {len(outcomes)} targets, {n_passed} passed, {len(outcomes) - n_passed} failed",
            err=True,
        )

    if output_format == "json":
        if len(outcomes) == 1:
            click.echo(outcomes[0][1].assessment_json)
        else:
            combined = [json.loads(oc.assessment_json) for _, oc, _ in outcomes]
            click.echo(json.dumps(combined, indent=2))
    elif output_format == "junit":
        for _, oc, _ in outcomes:
            click.echo(render_junit(oc.assessment), nl=False)
    elif output_format == "sarif":
        for _, oc, _ in outcomes:
            click.echo(render_sarif(oc.assessment), nl=False)
    elif output_format == "agent":
        from verdx.engine.runtime.projector import project_evidence

        evidences = []
        for _, oc, _ in outcomes:
            evidence = project_evidence(
                oc.assessment,
                threshold=threshold,
                domain=resolved_domain.name,
            )
            evidences.append(evidence)
        if len(evidences) == 1:
            click.echo(json.dumps(evidences[0].to_dict(), indent=2))
        else:
            click.echo(json.dumps([e.to_dict() for e in evidences], indent=2))
        if any(not (e.passed and e.coverage_ok and e.confidence_ok) for e in evidences):
            raise SystemExit(1)
        return
    else:
        for i, (_, oc, _) in enumerate(outcomes):
            if i > 0:
                click.echo("\n---\n")
            click.echo(oc.report_md, nl=False)

    if any(not ok for _, _, ok in outcomes):
        raise SystemExit(1)


@main.command(name="quick")
@click.argument("targets", nargs=-1, required=True)
@click.option("--domain", default=None, help="Validator domain (auto-detected if omitted).")
@click.option("--target-key", default=None, help="Override target key.")
@click.option(
    "--producer-role",
    default="content-author",
    help="Role that produced the target being validated.",
)
@click.option(
    "--producer-id",
    default=None,
    help="Opaque producer actor ID to resolve through the actor directory.",
)
@click.option("--config", "config_path", default=None, help="Domain config file.")
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    help="Additional named domain profile to layer after quick (repeatable).",
)
@click.option("--state-dir", default=None, help="State directory path.")
@click.option("--policy", default=None, help="Escalation policy file.")
@click.option(
    "--threshold",
    type=int,
    default=80,
    help="Minimum score required for exit 0 (default: 80).",
)
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "json", "junit", "sarif", "agent"]),
    default=None,
    help="Output format (default: markdown; --json aliases --format json).",
)
@click.option(
    "--no-history",
    is_flag=True,
    default=False,
    help="Skip history append.",
)
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Per-validator timeout (seconds).",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Skip result cache (always re-validate).",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Include live validators; quick defaults to static-only.",
)
@click.option(
    "--host",
    "-h",
    "workstation_host",
    default=None,
    help="Workstation hostname for live validation (default: workstation).",
)
@click.option(
    "--user",
    "-u",
    "workstation_user",
    default=None,
    help="SSH user for live validation (default: student).",
)
@click.option(
    "--password",
    default=None,
    help="SSH password (uses sshpass; default: key-based auth).",
)
@click.option(
    "--proxy",
    default=None,
    help="SOCKS5 proxy host for SSH (e.g. localhost:1080).",
)
@click.option(
    "--exercise",
    "-e",
    "exercises",
    multiple=True,
    help="Specific exercise(s) for live validation (repeatable).",
)
@click.pass_context
def quick_cmd(
    ctx: click.Context,
    targets: tuple[str, ...],
    domain: str | None,
    target_key: str | None,
    producer_role: str,
    producer_id: str | None,
    config_path: str | None,
    profiles: tuple[str, ...],
    state_dir: str | None,
    policy: str | None,
    threshold: int,
    use_json: bool,
    fmt: str | None,
    no_history: bool,
    timeout: int,
    no_cache: bool,
    live: bool,
    workstation_host: str | None,
    workstation_user: str | None,
    password: str | None,
    proxy: str | None,
    exercises: tuple[str, ...],
) -> None:
    """Run the selected domain's quick checkpoint profile."""
    ctx.invoke(
        validate_cmd,
        targets=targets,
        domain=domain,
        target_key=target_key,
        producer_role=producer_role,
        producer_id=producer_id,
        config_path=config_path,
        profiles=_quick_profiles(profiles),
        state_dir=state_dir,
        policy=policy,
        threshold=threshold,
        use_json=use_json,
        fmt=fmt,
        no_history=no_history,
        timeout=timeout,
        no_cache=no_cache,
        isolate=False,
        static_only=not live,
        live=False,
        workstation_host=workstation_host,
        workstation_user=workstation_user,
        password=password,
        proxy=proxy,
        exercises=exercises,
    )
