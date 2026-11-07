"""Project acceptance commands for the public CLI."""

from __future__ import annotations

import json
from pathlib import Path

import click

from verdx._output import auto_detect_format
from verdx.acceptance.models import Receipt
from verdx.acceptance.receipt import EXIT_CODES, replay, summary
from verdx.acceptance.runner import check


def _report(receipt: Receipt, path: Path, output: str | None) -> None:
    if (output or auto_detect_format()) == "json":
        click.echo(json.dumps(summary(receipt, path), indent=2))
    else:
        click.echo(
            f"{receipt.decision.outcome.upper()}  Required coverage: {receipt.decision.coverage}"
        )
        for item in receipt.decision.checks:
            requirement = "required" if item.required else "advisory"
            click.echo(
                f"  {item.id}: {item.status} ({requirement}, coverage {item.coverage}). "
                f"{item.reason}"
            )
        for reason in receipt.decision.reasons:
            click.echo(f"  {reason}")
        click.echo(f"Evidence: {path}")
        click.echo(f"Decision hash: {receipt.decision_hash}")


@click.command(name="check")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False),
    help="Acceptance policy; default: TARGET/verdx.toml.",
)
@click.option("--output", type=click.Choice(["text", "json"]), default=None)
def check_command(target: str, config: str | None, output: str | None) -> None:
    """Run a project's required and advisory checks and save the evidence."""
    try:
        receipt, path = check(
            target, config=config, progress=lambda message: click.echo(message, err=True)
        )
    except (ValueError, OSError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    _report(receipt, path, output)
    raise click.exceptions.Exit(EXIT_CODES[receipt.decision.outcome])


@click.command(name="replay")
@click.argument("receipt_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", type=click.Choice(["text", "json"]), default=None)
def replay_command(receipt_path: Path, output: str | None) -> None:
    """Verify saved evidence and recompute its decision without running commands."""
    try:
        receipt = replay(receipt_path)
    except (ValueError, OSError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    _report(receipt, receipt_path, output)
    raise click.exceptions.Exit(EXIT_CODES[receipt.decision.outcome])
