"""Disposition management commands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click

from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION, Disposition
from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import DEFAULT_STATE_DIR
from verdx.engine.runtime.store.dispositions import DispositionStore
from verdx.engine.runtime.store.history import _target_key_hash


@main.group(name="disposition")
def disposition_group() -> None:
    """Manage dispositions."""


@disposition_group.command(name="add")
@click.argument("fingerprint")
@click.option("--target-key", required=True, help="Target key.")
@click.option(
    "--decision",
    required=True,
    type=click.Choice(["accepted-risk", "false-positive", "deferred", "context-dependent"]),
    help="Disposition decision.",
)
@click.option("--reason", required=True, help="Why this decision was made.")
@click.option("--team", required=True, help="Team making this decision.")
@click.option("--expires", default=None, help="Expiry date (ISO 8601).")
@click.option("--state-dir", default=None, help="State directory path.")
def disposition_add(
    fingerprint: str,
    target_key: str,
    decision: str,
    reason: str,
    team: str,
    expires: str | None,
    state_dir: str | None,
) -> None:
    """Add a disposition for an issue fingerprint."""
    if expires is not None:
        try:
            datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except ValueError:
            raise click.ClickException(
                f"invalid --expires date (expected ISO 8601): {expires}"
            ) from None

    resolved_state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    run_timestamp = datetime.now(tz=UTC).isoformat()
    disposition = Disposition(
        schema_version=DISPOSITION_SCHEMA_VERSION,
        issue_fingerprint=fingerprint,
        decision=decision,
        reason=reason,
        team=team,
        timestamp=run_timestamp,
        expires=expires,
    )

    tkh = _target_key_hash(target_key)
    store = DispositionStore(resolved_state_dir)
    store.append(tkh, disposition)

    click.echo(f"Disposition added for {fingerprint}")
    click.echo(f"  Decision: {decision}")
    click.echo(f"  Team: {team}")
    click.echo(f"  Target: {target_key}")
    if expires:
        click.echo(f"  Expires: {expires}")
