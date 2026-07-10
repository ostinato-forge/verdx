"""History, report, handoff, and export commands."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import DEFAULT_STATE_DIR
from verdx.engine.runtime.store.history import HistoryStore
from verdx.engine.runtime.store.runs import RunStore


@main.command(name="history")
@click.argument("target")
@click.option("--target-key", default=None, help="Override target key.")
@click.option("--state-dir", default=None, help="State directory path.")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
def history_cmd(
    target: str,
    target_key: str | None,
    state_dir: str | None,
    use_json: bool,
) -> None:
    """Show target validation history."""
    resolved_target_key = target_key or target
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    history = HistoryStore(resolved_state_dir)
    runs = history.get_runs(resolved_target_key)

    if not runs:
        click.echo(f"No history for target: {resolved_target_key}", err=True)
        raise SystemExit(1)

    if use_json:
        click.echo(json.dumps([r.to_dict() for r in runs], indent=2))
        return

    click.echo(f"History for: {resolved_target_key}")
    click.echo(f"Total runs: {len(runs)}")
    click.echo()
    for run in runs[-10:]:
        click.echo(
            f"  {run.run_id}  score={run.score}  obs={run.observation_count}  {run.timestamp}"
        )


@main.command(name="report")
@click.argument("run_id")
@click.option("--state-dir", default=None, help="State directory path.")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON assessment.")
def report_cmd(
    run_id: str,
    state_dir: str | None,
    use_json: bool,
) -> None:
    """Show a run report."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    store = RunStore(resolved_state_dir)

    if use_json:
        content = store.get_assessment(run_id)
        if content is None:
            click.echo(f"No assessment found for run {run_id}", err=True)
            raise SystemExit(1)
        click.echo(content)
    else:
        content = store.get_report(run_id)
        if content is None:
            click.echo(f"No report found for run {run_id}", err=True)
            raise SystemExit(1)
        click.echo(content, nl=False)


@main.command(name="handoff")
@click.argument("run_id")
@click.option("--state-dir", default=None, help="State directory path.")
def handoff_cmd(
    run_id: str,
    state_dir: str | None,
) -> None:
    """Generate a handoff brief for human gate reviewers."""
    from verdx.engine.validation.handoff import compose_handoff, render_handoff_markdown

    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    store = RunStore(resolved_state_dir)

    assessment_json = store.get_assessment(run_id)
    if assessment_json is None:
        click.echo(f"No assessment found for run {run_id}", err=True)
        raise SystemExit(1)

    from verdx.contracts.assessment import RunAssessment

    assessment = RunAssessment.from_dict(json.loads(assessment_json))
    brief = compose_handoff(assessment)
    click.echo(render_handoff_markdown(brief), nl=False)


@main.command(name="export")
@click.argument("target")
@click.option("--target-key", default=None, help="Override target key.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "csv"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--since",
    default=None,
    help="Filter by ISO 8601 date (include runs on or after this date).",
)
@click.option("--state-dir", default=None, help="State directory path.")
def export_cmd(
    target: str,
    target_key: str | None,
    fmt: str,
    since: str | None,
    state_dir: str | None,
) -> None:
    """Export target run history."""
    resolved_target_key = target_key or target
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    history = HistoryStore(resolved_state_dir)
    runs = history.get_runs(resolved_target_key)

    if not runs:
        click.echo(f"No history found for target: {resolved_target_key}", err=True)
        raise SystemExit(1)

    if since:
        try:
            since_date = datetime.fromisoformat(since.replace("Z", "+00:00"))
            filtered_runs = []
            for run in runs:
                try:
                    run_date = datetime.fromisoformat(run.timestamp.replace("Z", "+00:00"))
                    if run_date >= since_date:
                        filtered_runs.append(run)
                except (ValueError, AttributeError):
                    continue
            runs = filtered_runs
        except ValueError:
            raise click.ClickException(
                f"invalid --since date (expected ISO 8601): {since}"
            ) from None

    if fmt == "json":
        click.echo(json.dumps([r.to_dict() for r in runs], indent=2))
    else:
        writer = csv.writer(sys.stdout)
        writer.writerow(["run_id", "timestamp", "score", "observation_count"])
        for run in runs:
            writer.writerow([run.run_id, run.timestamp, run.score, run.observation_count])
