"""Lifecycle commands: init, status, log, replay, record, artifact, dispatch,
classify, emit, stale, metrics."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.events import ArtifactRefs, ChildUnitType, EventType, StateEvent
from verdx.contracts.roles import ReasonTag, Role
from verdx.contracts.states import State
from verdx.engine.lifecycle.fast_path import ChangeDescription
from verdx.engine.lifecycle.fast_path import classify as run_classify
from verdx.engine.lifecycle.reducer import IntentState, reduce
from verdx.engine.lifecycle.replay import replay
from verdx.engine.runtime.cli import _helpers, main
from verdx.engine.runtime.cli._helpers import (
    _backend,
    _print_cycle_time,
    _print_intent_state,
    _resolve_next_state,
)


@main.command()
def init() -> None:
    """Initialize .agentic/ in the current repository."""
    backend = _backend()
    if backend.initialized:
        click.echo(f"Already initialized: {backend.root / '.agentic'}")
        return
    backend.init()
    click.echo(f"Initialized: {backend.root / '.agentic'}")


@main.command()
@click.option("--intent-id", "-i", default=None, help="Filter by intent ID.")
def status(intent_id: str | None) -> None:
    """Show current state of intent(s)."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    if intent_id:
        state = backend.query_state(intent_id)
        if state is None:
            raise click.ClickException(f"No events found for intent '{intent_id}'")
        _print_intent_state(state)
    else:
        intents = backend.list_intents()
        if not intents:
            click.echo("No intents recorded.")
            return
        for iid in intents:
            state = backend.query_state(iid)
            if state:
                _print_intent_state(state)
                click.echo()


@main.command()
@click.option("--intent-id", "-i", default=None, help="Filter by intent ID.")
def log(intent_id: str | None) -> None:
    """Show event history."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    events = backend.load_events(intent_id)
    if not events:
        click.echo("No events recorded.")
        return

    for i, event in enumerate(events):
        prev = event.previous_state or "(none)"
        click.echo(
            f"{i:>3}  {event.timestamp.isoformat()[:19]}  "
            f"{prev:>18} -> {event.next_state:<18}  "
            f"{event.event_type:<26}  "
            f"{event.actor}/{event.authority_role}"
        )


@main.command(name="replay")
@click.option("--intent-id", "-i", default=None, help="Filter by intent ID.")
def replay_cmd(intent_id: str | None) -> None:
    """Replay events and validate gates."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    events = backend.load_events(intent_id)
    if not events:
        click.echo("No events to replay.")
        return

    result = replay(events)

    for iid, intent_result in result.intents.items():
        click.echo(f"Intent: {iid}")
        click.echo(f"  State: {intent_result.final_state}")
        click.echo(f"  Events: {intent_result.event_count}")
        click.echo(f"  Gates: {len(intent_result.gates)}")

        for gate in intent_result.gates:
            status_mark = "+" if gate.satisfied else "x"
            click.echo(
                f"    {status_mark} {gate.artifact_type} ({gate.from_state} -> {gate.to_state})"
            )

        if intent_result.violations:
            click.echo(f"  Violations ({len(intent_result.violations)}):")
            for v in intent_result.violations:
                click.echo(f"    - {v}")

        can = "yes" if intent_result.can_integrate else "NO"
        click.echo(f"  Can integrate: {can}")
        click.echo()

    if result.clean:
        click.echo("Replay clean.")
    else:
        click.echo(f"Replay found {result.total_violations} violation(s).")
        raise SystemExit(1)


@main.command()
@click.option("--intent-id", "-i", required=True, help="Intent ID.")
@click.option(
    "--artifact-type",
    "-t",
    required=True,
    type=click.Choice([a.value for a in ArtifactType], case_sensitive=True),
    help="Artifact type.",
)
@click.option("--file", "-f", "file_path", default=None, help="JSON file (default: stdin).")
def record(intent_id: str, artifact_type: str, file_path: str | None) -> None:
    """Store a gate artifact."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    raw = Path(file_path).read_text() if file_path else sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON: {exc}") from exc

    ref = backend.record_artifact(intent_id, artifact_type, data)
    click.echo(f"Recorded: {ref}")


@main.command()
@click.option("--intent-id", "-i", required=True, help="Intent ID.")
@click.option(
    "--artifact-type",
    "-t",
    required=True,
    type=click.Choice([a.value for a in ArtifactType], case_sensitive=True),
    help="Artifact type.",
)
def artifact(intent_id: str, artifact_type: str) -> None:
    """Display a stored gate artifact."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    data = backend.load_artifact(intent_id, artifact_type)
    if data is None:
        raise click.ClickException(f"No {artifact_type} artifact found for intent '{intent_id}'.")
    click.echo(json.dumps(data, indent=2))


@main.command()
@click.option("--intent-id", "-i", required=True, help="Intent ID.")
@click.option("--target", required=True, help="Path or identifier to validate.")
@click.option("--timeout", default=300, type=int, help="Handler timeout in seconds.")
def dispatch(intent_id: str, target: str, timeout: int) -> None:
    """Run validation handler and store the result."""
    from verdx.engine.runtime.dispatch import (
        DispatchError,
        find_handler,
        load_manifest,
        run_handler,
    )

    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    intent_state = backend.query_state(intent_id)
    if intent_state is None:
        raise click.ClickException(f"No events found for intent '{intent_id}'.")

    if intent_state.current_state != State.Validating:
        raise click.ClickException(
            f"Intent '{intent_id}' is in {intent_state.current_state}, expected Validating."
        )

    manifest = load_manifest(_helpers._find_root())
    if manifest is None:
        raise click.ClickException("No handler manifest found. Create .agentic/handlers.yml.")

    handler = find_handler(manifest, "Validate")
    if handler is None:
        raise click.ClickException("No handler registered for stage 'Validate'.")

    click.echo(f"Running handler '{handler.id}' v{handler.version}...")

    try:
        data = run_handler(handler, target, timeout=timeout)
    except DispatchError as exc:
        raise click.ClickException(str(exc)) from exc

    ref = backend.record_artifact(intent_id, handler.output_artifact, data)
    click.echo(f"Stored: {ref}")

    score = data.get("score", {})
    overall = score.get("overall", "?") if isinstance(score, dict) else "?"
    checks_run = data.get("checks_run", "?")
    checks_passed = data.get("checks_passed", "?")
    coverage = data.get("coverage_status", "?")

    click.echo(f"  Score: {overall}  Checks: {checks_passed}/{checks_run}  Coverage: {coverage}")
    click.echo(
        f"\nNext: verdx emit -i {intent_id} -e validation_completed -r implementer "
        f"--artifact-ref {handler.output_artifact}:{intent_id}"
    )


@main.command(name="classify")
@click.option("--intent-id", "-i", required=True, help="Intent ID.")
@click.option(
    "--type", "change_types", multiple=True, required=True, help="Change type (repeatable)."
)
@click.option("--extra-denylist", multiple=True, help="Additional denylist pattern (repeatable).")
def classify_cmd(
    intent_id: str, change_types: tuple[str, ...], extra_denylist: tuple[str, ...]
) -> None:
    """Classify a change for fast-path eligibility."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    files = _helpers._git_diff_name_only()
    if files is None:
        raise click.ClickException("Cannot read git diff — no changes detected or git unavailable.")

    lines = _helpers._git_diff_lines()
    if lines is None:
        lines = 0

    digest = _helpers._git_diff_digest()
    if digest is None:
        digest = "unknown"

    head = _helpers._git_short_head()
    if head is None:
        raise click.ClickException("Cannot detect git HEAD.")

    policy_denylist: tuple[str, ...] = ()
    policy_path = backend.root / ".agentic" / "policy.yml"
    if policy_path.exists():
        import yaml

        try:
            policy = yaml.safe_load(policy_path.read_text()) or {}
            fp_config = policy.get("fast_path", {})
            if isinstance(fp_config, dict):
                dl = fp_config.get("denylist", [])
                if isinstance(dl, list):
                    policy_denylist = tuple(str(p) for p in dl)
        except Exception:
            pass

    change = ChangeDescription(
        files_changed=tuple(files),
        lines_modified=lines,
        change_types=frozenset(change_types),
        diff_digest=digest,
        base_commit=head,
    )

    result = run_classify(change, extra_denylist=extra_denylist + policy_denylist)

    if result.eligible:
        artifact_data: dict[str, Any] = {
            "base_commit": head,
            "diff_digest": digest,
            "files_changed": list(files),
            "lines_modified": lines,
            "change_types": sorted(change_types),
            "classifier_version": "v1",
            "policy_version": "ptl-v1",
            "denylist_hits": list(result.denylist_hits),
            "unchecked_criteria": list(result.unchecked),
            "eligible": True,
            "reasons": list(result.reasons),
        }
        backend.record_artifact(intent_id, "FastPathEligibility", artifact_data)

        click.echo("Eligible for fast-path.")
        for r in result.reasons:
            click.echo(f"  + {r}")
        if result.unchecked:
            click.echo("  Unchecked (requires policy config):")
            for u in result.unchecked:
                click.echo(f"    ? {u}")
        click.echo(
            f"\nNext: verdx emit -i {intent_id} -e fast_path_eligible -r requester "
            f"--artifact-ref FastPathEligibility:{intent_id}/v1"
        )
    else:
        click.echo("Not eligible for fast-path.")
        for r in result.reasons:
            click.echo(f"  x {r}")
        if result.denylist_hits:
            click.echo("  Denylist hits:")
            for h in result.denylist_hits:
                click.echo(f"    - {h}")
        click.echo(
            f"\nNormal path: verdx emit -i {intent_id} -e clarify_completed -r requester "
            f"--artifact-ref IntentSummary:{intent_id}/v1"
        )
        raise SystemExit(1)


@main.command()
@click.option("--intent-id", "-i", required=True, help="Intent ID to emit event for.")
@click.option(
    "--event-type",
    "-e",
    required=True,
    type=click.Choice([e.value for e in EventType], case_sensitive=True),
    help="Event type.",
)
@click.option(
    "--authority-role",
    "-r",
    required=True,
    type=click.Choice([r.value for r in Role], case_sensitive=True),
    help="Actor's authority role.",
)
@click.option("--actor", "-a", default=None, help="Actor name (default: git user.name).")
@click.option("--base-commit", default=None, help="Base commit SHA (default: git HEAD).")
@click.option("--policy-version", default="ptl-v1", help="Policy version.")
@click.option("--timestamp", "ts", default=None, help="ISO 8601 timestamp (default: now UTC).")
@click.option("--branch", default=None, help="Git branch name.")
@click.option("--parent-intent-id", default=None, help="Parent intent ID.")
@click.option(
    "--child-unit-type",
    default=None,
    type=click.Choice([c.value for c in ChildUnitType], case_sensitive=True),
    help="Child unit type.",
)
@click.option("--artifact-ref", multiple=True, help="Created artifact ref (repeatable).")
@click.option("--consumed-ref", multiple=True, help="Consumed artifact ref (repeatable).")
@click.option("--pr-id", multiple=True, help="PR identifier (repeatable).")
@click.option("--source-ref", multiple=True, help="Source reference (repeatable).")
@click.option("--detail", default=None, help="Free-form detail text.")
@click.option(
    "--reason-tag",
    multiple=True,
    type=click.Choice([t.value for t in ReasonTag], case_sensitive=True),
    help="Reason tag (repeatable).",
)
@click.option("--elapsed-seconds", type=float, default=None, help="Stage elapsed seconds.")
@click.option("--attention-minutes", type=float, default=None, help="Human attention minutes.")
@click.option("--wait-minutes", type=float, default=None, help="Wait/blocked minutes.")
def emit(
    intent_id: str,
    event_type: str,
    authority_role: str,
    actor: str | None,
    base_commit: str | None,
    policy_version: str,
    ts: str | None,
    branch: str | None,
    parent_intent_id: str | None,
    child_unit_type: str | None,
    artifact_ref: tuple[str, ...],
    consumed_ref: tuple[str, ...],
    pr_id: tuple[str, ...],
    source_ref: tuple[str, ...],
    detail: str | None,
    reason_tag: tuple[str, ...],
    elapsed_seconds: float | None,
    attention_minutes: float | None,
    wait_minutes: float | None,
) -> None:
    """Emit a state event into the log."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    evt = EventType(event_type)
    role = Role(authority_role)

    if actor is None:
        actor = _helpers._git_actor()
        if actor is None:
            raise click.ClickException("Cannot detect actor. Provide --actor.")

    if base_commit is None:
        base_commit = _helpers._git_short_head()
        if base_commit is None:
            raise click.ClickException("Cannot detect base commit. Provide --base-commit.")

    if ts is not None:
        try:
            timestamp = datetime.fromisoformat(ts)
        except ValueError as exc:
            msg = f"Invalid timestamp: {exc}"
            raise click.BadParameter(msg, param_hint="--timestamp") from None
    else:
        timestamp = datetime.now(UTC)

    intent_state = backend.query_state(intent_id)
    previous_state = intent_state.current_state if intent_state else None
    next_state = _resolve_next_state(intent_state, evt)

    try:
        event = StateEvent(
            intent_id=intent_id,
            event_type=evt,
            previous_state=previous_state,
            next_state=next_state,
            actor=actor,
            authority_role=role,
            timestamp=timestamp,
            base_commit=base_commit,
            policy_version=policy_version,
            branch=branch,
            parent_intent_id=parent_intent_id,
            child_unit_type=ChildUnitType(child_unit_type) if child_unit_type else None,
            artifact_refs=ArtifactRefs(created=artifact_ref, consumed=consumed_ref),
            pr_ids=pr_id,
            source_refs=source_ref,
            detail=detail,
            reason_tags=tuple(ReasonTag(t) for t in reason_tag),
            elapsed_seconds=elapsed_seconds,
            attention_minutes=attention_minutes,
            wait_minutes=wait_minutes,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    reducer_state = intent_state or IntentState(intent_id=intent_id)
    result = reduce(reducer_state, event)

    if not result.valid:
        click.echo("Validation failed:")
        for v in result.violations:
            click.echo(f"  x {v}")
        raise SystemExit(1)

    if evt in (EventType.integration_started, EventType.integration_completed):
        past_events = backend.load_events(intent_id)
        replay_result = replay(past_events)
        intent_replay = replay_result.intents.get(intent_id)
        if intent_replay and not intent_replay.can_integrate:
            click.echo("Integration blocked — replay check failed:")
            for v in intent_replay.violations:
                click.echo(f"  x {v}")
            for g in intent_replay.gates:
                if not g.satisfied:
                    click.echo(f"  x Gate unsatisfied: {g.artifact_type}")
            raise SystemExit(1)

    backend.append_event(event)

    prev_label = previous_state or "(none)"
    click.echo(f"Emitted: {prev_label} -> {next_state}  [{evt}]  {actor}/{role}")


@main.command()
@click.option("--intent-id", "-i", default=None, help="Filter by intent ID.")
def stale(intent_id: str | None) -> None:
    """Check if intent base commits are stale against current HEAD."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    head = _helpers._git_short_head()
    if head is None:
        raise click.ClickException("Cannot detect git HEAD.")

    intents = [intent_id] if intent_id else backend.list_intents()
    if not intents:
        click.echo("No intents recorded.")
        return

    any_stale = False
    for iid in intents:
        state = backend.query_state(iid)
        if state is None:
            continue
        if state.current_state is not None and state.current_state.is_terminal:
            continue

        issues: list[str] = []
        if state.base_commit and state.base_commit != head:
            issues.append(f"base {state.base_commit} != HEAD {head}")
        if state.validated_at_commit and state.validated_at_commit != head:
            issues.append(f"validated at {state.validated_at_commit} != HEAD {head}")
        if state.reviewed_at_commit and state.reviewed_at_commit != head:
            issues.append(f"reviewed at {state.reviewed_at_commit} != HEAD {head}")

        if issues:
            any_stale = True
            click.echo(f"Intent {iid}: STALE")
            for issue in issues:
                click.echo(f"  x {issue}")
            click.echo(f"  Fix: verdx emit -i {iid} -e rebase_required -r merge_actor")
        else:
            click.echo(f"Intent {iid}: fresh (HEAD {head})")

    if any_stale:
        raise SystemExit(1)


@main.command()
@click.option("--intent-id", "-i", default=None, help="Filter by intent ID.")
def metrics(intent_id: str | None) -> None:
    """Show timing and rework metrics for intent(s)."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")

    events = backend.load_events(intent_id)
    if not events:
        click.echo("No events to analyze.")
        return

    result = replay(events)

    for iid, intent_result in result.intents.items():
        m = intent_result.metrics
        if m is None:
            continue

        click.echo(f"Intent: {iid}")

        if m.cycle_time_seconds is not None:
            _print_cycle_time(m.cycle_time_seconds)
        else:
            click.echo("  Cycle time: (single event)")

        click.echo(f"  Attention:  {m.total_attention_minutes:.1f} min")
        click.echo(f"  Wait:       {m.total_wait_minutes:.1f} min")
        click.echo(f"  Elapsed:    {m.total_elapsed_seconds:.1f} s")
        click.echo(f"  Rework:     {m.rework_count}")

        if m.stage_entries:
            entries = " ".join(f"{k}({v})" for k, v in sorted(m.stage_entries.items()))
            click.echo(f"  Stages:     {entries}")

        if m.reason_tag_counts:
            tags = " ".join(f"{k}({v})" for k, v in sorted(m.reason_tag_counts.items()))
            click.echo(f"  Reasons:    {tags}")

        click.echo()
