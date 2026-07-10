"""Actor directory management commands."""

from __future__ import annotations

import json
from pathlib import Path

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import DEFAULT_STATE_DIR
from verdx.engine.runtime.store.actors import ActorDirectory, ActorEntry


@main.group(name="actor")
def actor_group() -> None:
    """Manage actor directory."""


@actor_group.command(name="create")
@click.argument("role_id")
@click.option(
    "--kind",
    required=True,
    type=click.Choice(["human", "agent", "system"]),
    help="Actor kind.",
)
@click.option("--team", default=None, help="Team name.")
@click.option("--state-dir", default=None, help="State directory path.")
def actor_create(
    role_id: str,
    kind: str,
    team: str | None,
    state_dir: str | None,
) -> None:
    """Create an actor in the directory."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    directory = ActorDirectory(resolved_state_dir)

    if directory.get(role_id) is not None:
        raise click.ClickException(f"Actor with role '{role_id}' already exists")

    entry = ActorEntry(
        opaque_id=role_id,
        role=role_id,
        kind=kind,
        team=team,
    )
    directory.put(entry)

    click.echo(f"Actor created: {role_id}")
    click.echo(f"  Kind: {kind}")
    if team:
        click.echo(f"  Team: {team}")


@actor_group.command(name="delete")
@click.argument("role_id")
@click.option("--state-dir", default=None, help="State directory path.")
def actor_delete(
    role_id: str,
    state_dir: str | None,
) -> None:
    """Delete an actor from the directory."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    directory = ActorDirectory(resolved_state_dir)

    if not directory.delete(role_id):
        raise click.ClickException(f"Actor with role '{role_id}' not found")

    click.echo(f"Actor deleted: {role_id}")


@actor_group.command(name="list")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
@click.option("--state-dir", default=None, help="State directory path.")
def actor_list(
    use_json: bool,
    state_dir: str | None,
) -> None:
    """List all registered actors."""
    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    directory = ActorDirectory(resolved_state_dir)
    actors = directory.list_all()

    if not actors:
        click.echo("No actors registered.", err=True)
        return

    if use_json:
        click.echo(json.dumps([a.to_dict() for a in actors], indent=2))
    else:
        click.echo(f"{'opaque_id':<20} {'role':<20} {'kind':<10} {'team':<20}")
        click.echo("-" * 70)
        for actor in actors:
            actor_team = actor.team or ""
            click.echo(f"{actor.opaque_id:<20} {actor.role:<20} {actor.kind:<10} {actor_team:<20}")
