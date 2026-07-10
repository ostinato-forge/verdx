"""Domain discovery and authoring commands."""

from __future__ import annotations

import json
from pathlib import Path

import click

from verdx.engine.runtime.cli import main
from verdx.engine.runtime.domain_author import author_check, build_domain_catalog
from verdx.engine.runtime.domains import (
    UnknownDomainError,
    domain_load_errors,
    get_domain,
    list_domains,
)


@main.group(name="domains")
def domains_cmd() -> None:
    """Inspect and author verdx domain packages."""


@domains_cmd.command(name="list")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
def domains_list_cmd(use_json: bool) -> None:
    """List registered validation domains."""

    domains = list_domains()
    errors = domain_load_errors()
    if use_json:
        click.echo(json.dumps({"domains": list(domains), "load_errors": errors}, indent=2))
        return

    if domains:
        click.echo("Registered domains:")
        for name in domains:
            click.echo(f"  {name}")
    else:
        click.echo("No domains registered.")

    if errors:
        click.echo()
        click.echo("Failed entry points:")
        for name, error in sorted(errors.items()):
            click.echo(f"  {name}: {error}")


@domains_cmd.command(name="show")
@click.argument("name")
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
def domains_show_cmd(name: str, use_json: bool) -> None:
    """Show the catalog projection for one registered domain."""

    try:
        spec = get_domain(name)
        catalog = build_domain_catalog(spec)
    except UnknownDomainError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(f"failed to load domain {name!r}: {exc}") from exc

    data = catalog.to_dict()
    if use_json:
        click.echo(json.dumps(data, indent=2))
        return

    click.echo(f"Domain: {data['domain']}")
    markers = data["markers"]
    click.echo(f"Markers: {', '.join(markers) if markers else '(none)'}")
    checks = data["checks"]
    click.echo(f"Checks: {len(checks)}")
    for check in checks:
        critical = " critical" if check["critical"] else ""
        caps = ",".join(check["capabilities"]) or "none"
        click.echo(f"  {check['check_id']} ({check['adapter_kind']}, caps={caps}{critical})")


@domains_cmd.command(name="author")
@click.argument("path", default=".", required=False)
@click.option("--json", "use_json", is_flag=True, default=False, help="Output JSON.")
def domains_author_cmd(path: str, use_json: bool) -> None:
    """Check a local domain package for authoring readiness."""

    report = author_check(Path(path))
    data = report.to_dict()

    if use_json:
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo(f"Domain author check: {report.status.upper()}")
        click.echo(f"Path: {report.path}")
        if report.package:
            click.echo(f"Package: {report.package}")
        if report.entry_points:
            click.echo("Entry points:")
            for name, target in sorted(report.entry_points.items()):
                click.echo(f"  {name} -> {target}")
        click.echo()
        for check in report.checks:
            click.echo(f"[{check.status}] {check.name}: {check.message}")

    if report.status == "fail":
        raise click.exceptions.Exit(1)
