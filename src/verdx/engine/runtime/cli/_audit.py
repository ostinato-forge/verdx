"""Audit commands: trace, compliance."""

from __future__ import annotations

import json

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.cli._helpers import _backend


@main.command()
@click.option("--intent-id", "-i", required=True, help="Intent ID.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["ascii", "mermaid"]),
    default="ascii",
    help="Output format.",
)
def trace(intent_id: str, fmt: str) -> None:
    """Show trace timeline for an intent."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")
    events = backend.load_events(intent_id)
    if not events:
        click.echo("No events found.")
        return
    from verdx.engine.lifecycle.audit import format_trace, timeline

    entries = timeline(events, intent_id)
    click.echo(format_trace(entries, fmt=fmt))


@main.command()
@click.option("--intent-id", "-i", required=True, help="Intent ID.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    help="Output format.",
)
def compliance(intent_id: str, fmt: str) -> None:
    """Show compliance report for an intent."""
    backend = _backend()
    if not backend.initialized:
        raise click.ClickException("Not initialized. Run 'verdx init' first.")
    events = backend.load_events(intent_id)
    if not events:
        click.echo("No events found.")
        return
    from verdx.engine.lifecycle.audit import compliance_summary, format_compliance_report, timeline

    entries = timeline(events, intent_id)
    summary = compliance_summary(events, intent_id)
    if fmt == "json":
        click.echo(json.dumps(summary.to_dict(), indent=2))
    else:
        click.echo(format_compliance_report(summary, entries))
