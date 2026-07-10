"""Maintenance commands: prune, inspect."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import DEFAULT_STATE_DIR
from verdx.engine.runtime.store.history import HistoryStore
from verdx.engine.runtime.store.runs import RunStore


@main.command(name="prune")
@click.option(
    "--days",
    type=int,
    default=90,
    help="Delete runs older than N days (default: 90).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be deleted without deleting.",
)
@click.option(
    "--keep-last",
    type=int,
    default=None,
    help="Keep N most recent runs regardless of age.",
)
@click.option("--state-dir", default=None, help="State directory path.")
def prune_cmd(
    days: int,
    dry_run: bool,
    keep_last: int | None,
    state_dir: str | None,
) -> None:
    """Remove old run directories."""
    if days < 1:
        raise click.ClickException("--days must be a positive integer")
    if keep_last is not None and keep_last < 0:
        raise click.ClickException("--keep-last must be non-negative")

    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    store = RunStore(resolved_state_dir)
    run_ids = store.list_runs()

    if not run_ids:
        click.echo("Pruned 0 runs (0 kept, 0 errors)")
        return

    cutoff_date = datetime.now(UTC) - timedelta(days=days)
    runs_with_timestamps: list[tuple[str, datetime]] = []
    errors = 0

    for rid in run_ids:
        run_status = store.get_status(rid)
        if run_status is None:
            errors += 1
            continue
        try:
            ts = datetime.fromisoformat(run_status.timestamp.replace("Z", "+00:00"))
            runs_with_timestamps.append((rid, ts))
        except (ValueError, AttributeError):
            errors += 1

    runs_with_timestamps.sort(key=lambda x: x[1])

    to_delete: list[str] = []
    keep_count = 0

    for idx, (rid, ts) in enumerate(runs_with_timestamps):
        if keep_last is not None:
            keep_index = len(runs_with_timestamps) - keep_last
            if idx >= keep_index:
                keep_count += 1
                continue

        if ts < cutoff_date:
            to_delete.append(rid)
        else:
            keep_count += 1

    if dry_run:
        for rid in to_delete:
            click.echo(f"Would delete: {rid}")
        summary = f"Dry run: {len(to_delete)} runs would be pruned"
        click.echo(f"{summary} ({keep_count} kept, {errors} errors)")
        return

    deleted = 0
    for rid in to_delete:
        if store.delete_run(rid):
            deleted += 1
        else:
            errors += 1

    click.echo(f"Pruned {deleted} runs ({keep_count} kept, {errors} errors)")


@main.command(name="inspect")
@click.option("--state-dir", default=None, help="State directory path.")
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    default=False,
    help="Output JSON instead of human-readable text.",
)
def inspect_cmd(
    state_dir: str | None,
    use_json: bool,
) -> None:
    """Show state directory statistics."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    run_store = RunStore(resolved_state_dir)
    history_store = HistoryStore(resolved_state_dir)

    run_ids = run_store.list_runs()
    run_count = len(run_ids)

    total_size = 0
    runs_dir = resolved_state_dir / "runs"
    if runs_dir.exists():
        for item in runs_dir.rglob("*"):
            if item.is_file():
                with contextlib.suppress(OSError):
                    total_size += item.stat().st_size

    target_keys = history_store.get_target_keys()
    target_stats: dict[str, int] = {}
    for tk_hash in target_keys:
        runs_path = resolved_state_dir / "history" / tk_hash / "runs.jsonl"
        if runs_path.exists():
            try:
                lines = runs_path.read_text(encoding="utf-8").strip().split("\n")
                target_stats[tk_hash] = len(lines) if lines and lines[0] else 0
            except (OSError, UnicodeDecodeError):
                target_stats[tk_hash] = 0

    oldest_timestamp: str | None = None
    newest_timestamp: str | None = None

    if run_ids:
        timestamps: list[datetime] = []
        for rid in run_ids:
            run_status = run_store.get_status(rid)
            if run_status:
                try:
                    ts = datetime.fromisoformat(run_status.timestamp.replace("Z", "+00:00"))
                    timestamps.append(ts)
                except (ValueError, AttributeError):
                    pass

        if timestamps:
            timestamps.sort()
            oldest_timestamp = timestamps[0].isoformat()
            newest_timestamp = timestamps[-1].isoformat()

    if use_json:
        output = {
            "total_runs": run_count,
            "total_size_bytes": total_size,
            "targets": target_stats,
            "oldest_run": oldest_timestamp,
            "newest_run": newest_timestamp,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"State directory: {resolved_state_dir}")
        click.echo(f"Total runs: {run_count}")
        click.echo(f"Total size: {total_size:,} bytes")
        click.echo(f"Targets with history: {len(target_stats)}")
        if target_stats:
            click.echo("\nTarget run counts:")
            for tk_hash, count in sorted(target_stats.items()):
                click.echo(f"  {tk_hash}: {count} runs")
        if oldest_timestamp:
            click.echo(f"\nOldest run: {oldest_timestamp}")
        if newest_timestamp:
            click.echo(f"Newest run: {newest_timestamp}")
