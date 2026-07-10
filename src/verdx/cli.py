"""CLI — two commands: validate and fix."""

from __future__ import annotations

import logging
import sys

import click

from verdx import __version__
from verdx._flywheel_cli import flywheel
from verdx._output import (
    auto_detect_format,
    format_fix_json,
    format_fix_text,
    format_json,
    format_text,
)
from verdx.api import fix as verdx_fix
from verdx.api import validate as verdx_validate
from verdx.profiles import PROFILES


@click.group()
@click.version_option(version=__version__, prog_name="verdx")
@click.option("-v", "--verbose", is_flag=True, help="Show progress on stderr.")
def main(verbose: bool) -> None:
    """Deterministic verification engine."""
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(name)s: %(message)s",
            stream=sys.stderr,
        )


@main.command()
@click.argument("targets", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--static-only", is_flag=True, help="Disable live validators; static analysis only.")
@click.option("--live", is_flag=True, hidden=True, help="Deprecated no-op (live is default).")
@click.option("--changed-only", is_flag=True, help="Scope to files changed since merge-base.")
@click.option(
    "--profile",
    type=click.Choice(sorted(PROFILES)),
    default="standard",
    show_default=True,
    help="Score threshold profile.",
)
@click.option("--domain", default=None, help="Validate a specific domain only.")
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["text", "json"]),
    default=None,
    help="Output format (auto-detects: text for TTY, json when piped).",
)
@click.option("--no-flywheel", is_flag=True, help="Disable flywheel enrichment and logging.")
def validate(
    targets: tuple[str, ...],
    static_only: bool,
    live: bool,
    changed_only: bool,
    profile: str,
    domain: str | None,
    output_fmt: str | None,
    no_flywheel: bool,
) -> None:
    """Validate one or more targets against quality gates."""
    fmt = output_fmt or auto_detect_format()
    any_blocked = False

    for target in targets:
        try:
            assessment = verdx_validate(
                target,
                changed_only=changed_only,
                live=not static_only,
                profile=profile,
                domain=domain,
                flywheel=not no_flywheel,
            )
        except Exception as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)

        if fmt == "json":
            click.echo(format_json(assessment))
        else:
            if len(targets) > 1:
                click.echo(f"\n--- {target} ---")
            click.echo(format_text(assessment))

        if assessment.verdict == "block":
            any_blocked = True

    sys.exit(1 if any_blocked else 0)


@main.command()
@click.argument("target", type=click.Path(exists=True))
@click.option("--static-only", is_flag=True, help="Disable live validators; static analysis only.")
@click.option("--dry-run", is_flag=True, help="Show what would be fixed without applying.")
@click.option("--changed-only", is_flag=True, help="Scope to files changed since merge-base.")
@click.option(
    "--profile",
    type=click.Choice(sorted(PROFILES)),
    default="standard",
    show_default=True,
    help="Score threshold profile.",
)
@click.option("--domain", default=None, help="Fix a specific domain only.")
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["text", "json"]),
    default=None,
    help="Output format.",
)
@click.option("--no-flywheel", is_flag=True, help="Disable flywheel enrichment and logging.")
def fix(
    target: str,
    static_only: bool,
    dry_run: bool,
    changed_only: bool,
    profile: str,
    domain: str | None,
    output_fmt: str | None,
    no_flywheel: bool,
) -> None:
    """Apply deterministic fixes to a target."""
    fmt = output_fmt or auto_detect_format()

    try:
        result = verdx_fix(
            target,
            dry_run=dry_run,
            changed_only=changed_only,
            live=not static_only,
            profile=profile,
            domain=domain,
            flywheel=not no_flywheel,
        )
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    if fmt == "json":
        click.echo(format_fix_json(result))
    else:
        click.echo(format_fix_text(result, dry_run=dry_run))

    sys.exit(1 if result.assessment_after.verdict == "block" else 0)


@main.command()
@click.argument("target", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--fix-cmd",
    required=True,
    help="External fix command to run; the fix prompt is provided on stdin.",
)
@click.option(
    "--max-iterations",
    type=click.IntRange(min=1),
    default=5,
    show_default=True,
    help="Max fix attempts.",
)
@click.option("--static-only", is_flag=True, help="Gate on static validators only (no lab).")
@click.option(
    "--profile",
    type=click.Choice(sorted(PROFILES)),
    default="standard",
    show_default=True,
    help="Score threshold profile.",
)
@click.option("--domain", default=None, help="Gate on a specific domain only.")
@click.option("--no-flywheel", is_flag=True, help="Disable flywheel enrichment and logging.")
def drive(
    target: str,
    fix_cmd: str,
    max_iterations: int,
    static_only: bool,
    profile: str,
    domain: str | None,
    no_flywheel: bool,
) -> None:
    """Drive an external fix command to repair TARGET until verdx passes.

    verdx owns the loop and the verdict. It runs its own validation; while the verdict is
    'block', it invokes the fix command to repair the findings, then re-validates. Exits 0
    only when verdx's verdict is 'pass'. The fix command never decides done, so 'test it
    live' becomes a gate verdx enforces, not a step the fix command is trusted to run.
    """
    from verdx.api import drive as verdx_drive

    try:
        result = verdx_drive(
            target,
            fix_cmd,
            max_iterations=max_iterations,
            live=not static_only,
            profile=profile,
            domain=domain,
            flywheel=not no_flywheel,
        )
    except Exception as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    after = result.assessment_after
    click.echo(
        f"verdict: {after.verdict}  score: {after.score}  iterations: {result.iterations}  "
        f"fix-invocations: {result.fix_invocations}  reason: {result.termination_reason}"
    )
    if not result.converged:
        click.echo(format_text(after), err=True)

    sys.exit(0 if result.converged else 1)


main.add_command(flywheel)

if __name__ == "__main__":
    main()
