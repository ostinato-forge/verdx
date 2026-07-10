"""Hook management commands."""

from __future__ import annotations

from pathlib import Path

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import DEFAULT_STATE_DIR
from verdx.engine.runtime.hooks import HookRegistry


@main.group(name="hook")
def hook_group() -> None:
    """Manage event hooks."""


@hook_group.command(name="add")
@click.argument("url")
@click.option(
    "--events",
    default=None,
    help="Comma-separated event names (default: all).",
)
@click.option(
    "--header",
    multiple=True,
    help="Header in Key:Value format (repeatable).",
)
@click.option("--state-dir", default=None, help="State directory path.")
def hook_add_cmd(
    url: str,
    events: str | None,
    header: tuple[str, ...],
    state_dir: str | None,
) -> None:
    """Register a webhook endpoint."""
    from verdx.engine.runtime.hooks import VALID_EVENTS, HookConfig

    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR

    if events is not None:
        event_set = frozenset(e.strip() for e in events.split(",") if e.strip())
    else:
        event_set = frozenset(VALID_EVENTS)

    headers: dict[str, str] = {}
    for h in header:
        if ":" not in h:
            raise click.ClickException(f"invalid header (expected Key:Value): {h}")
        key, value = h.split(":", 1)
        headers[key.strip()] = value.strip()

    try:
        hook = HookConfig(url=url, events=event_set, headers=headers)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None

    registry = HookRegistry(resolved_state_dir)
    registry.add(hook)
    click.echo(f"Hook added: {url}")
    click.echo(f"  Events: {', '.join(sorted(hook.events))}")


@hook_group.command(name="list")
@click.option("--state-dir", default=None, help="State directory path.")
def hook_list_cmd(state_dir: str | None) -> None:
    """List registered hooks."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    registry = HookRegistry(resolved_state_dir)

    if not registry.hooks:
        click.echo("No hooks configured.")
        return

    for hook in registry.hooks:
        click.echo(f"  {hook.url}")
        click.echo(f"    Events: {', '.join(sorted(hook.events))}")
        if hook.headers:
            for k, v in hook.headers.items():
                click.echo(f"    Header: {k}: {v}")


@hook_group.command(name="remove")
@click.argument("url")
@click.option("--state-dir", default=None, help="State directory path.")
def hook_remove_cmd(url: str, state_dir: str | None) -> None:
    """Remove a hook by URL."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    registry = HookRegistry(resolved_state_dir)

    if registry.remove(url):
        click.echo(f"Hook removed: {url}")
    else:
        click.echo(f"No hook found for URL: {url}", err=True)
        raise SystemExit(1)
