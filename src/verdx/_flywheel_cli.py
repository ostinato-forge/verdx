"""CLI subcommands for flywheel inspection, governance, and maintenance."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import click

from verdx.engine.flywheel import Flywheel, FlywheelStore
from verdx.engine.flywheel.store import sanitize_target_key


def _store(target_key: str | None) -> FlywheelStore:
    # The CLI accepts a path or a bare key; sanitize so a path-like argument
    # cannot escape the data root and maps to a stable store key.
    return FlywheelStore(sanitize_target_key(target_key) if target_key else "_default")


@click.group()
def flywheel() -> None:
    """Inspect, govern, and maintain the flywheel."""


@flywheel.command()
@click.argument("target_key", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
def inspect(target_key: str | None, as_json: bool) -> None:
    """Show flywheel state for a target."""
    store = _store(target_key)
    fw = Flywheel(store)
    summary = fw.review()

    if as_json:
        click.echo(json.dumps(summary, indent=2))
        return

    patterns = summary["patterns_summary"]
    corrections = summary["corrections_summary"]
    click.echo(f"Flywheel state{f' for {target_key}' if target_key else ''}:")
    click.echo(f"  Patterns: {patterns['total']} ({patterns.get('blocked', 0)} blocked)")
    click.echo(f"  Corrections: {corrections['total']}")

    if patterns["by_frequency"]:
        click.echo("\nPatterns:")
        for p in patterns["by_frequency"][:10]:
            status = "BLOCKED" if p.get("blocked") else f"seen {p['seen_count']}x"
            source = f"  source={p['source']}" if p.get("source") else ""
            click.echo(f'  {p["id"]}  {status}  "{p["diagnosis"]}"{source}')

    if summary.get("issues"):
        click.echo(f"\nIssues ({len(summary['issues'])}):")
        for issue in summary["issues"]:
            tag = issue["type"].upper()
            click.echo(f"  [{tag}] {issue.get('pattern_id', '')} {issue['detail']}")


@flywheel.command()
@click.argument("keywords")
@click.argument("target_key", required=False, default=None)
@click.option("--top", default=5, help="Max results.")
@click.option("--domain", default=None, help="Filter by domain.")
def search(keywords: str, target_key: str | None, top: int, domain: str | None) -> None:
    """Search patterns by keyword similarity."""
    fw = Flywheel(_store(target_key))
    results = fw.search(keywords, domain=domain, top=top)
    if not results:
        click.echo("No matching patterns.")
        return
    for r in results:
        click.echo(
            f'{r["pattern_id"]}  score={r["score"]}  seen={r["seen_count"]}x  "{r["diagnosis"]}"'
        )


@flywheel.command()
@click.argument("target_key", required=False, default=None)
def review(target_key: str | None) -> None:
    """Show flywheel health issues for a target."""
    fw = Flywheel(_store(target_key))
    summary = fw.review()
    issues = summary.get("issues", [])
    if not issues:
        click.echo("No issues detected.")
        return
    click.echo(f"Flywheel review ({len(issues)} issue(s)):\n")
    for issue in issues:
        tag = issue["type"].upper()
        pid = issue.get("pattern_id", issue.get("pattern_ids", ""))
        click.echo(f"  [{tag}] {pid} {issue['detail']}")


@flywheel.command()
@click.argument("pattern_id")
@click.option("--reason", required=True, help="Why this pattern is being blocked.")
def block(pattern_id: str, reason: str) -> None:
    """Suppress a pattern without deleting it."""
    fw = Flywheel(FlywheelStore("_org"))
    result = fw.block(pattern_id, reason=reason)
    if result["action"] == "already_blocked":
        click.echo(f"{pattern_id} is already blocked.")
    else:
        click.echo(f"Blocked {pattern_id}.")


@flywheel.command()
@click.argument("pattern_id")
def unblock(pattern_id: str) -> None:
    """Re-enable a previously blocked pattern."""
    fw = Flywheel(FlywheelStore("_org"))
    result = fw.unblock(pattern_id)
    if result["action"] == "not_found":
        click.echo(f"{pattern_id} is not in the blocklist.")
    else:
        click.echo(f"Unblocked {pattern_id}.")


@flywheel.command()
@click.option("--min-repos", default=3, help="Min distinct repos for promotion.")
def promote(min_repos: int) -> None:
    """Promote cross-repo patterns to org layer."""
    fw = Flywheel(FlywheelStore("_org"))
    result = fw.promote(min_repos=min_repos)
    if result["promoted"]:
        click.echo(f"Promoted {result['promoted']} pattern(s) to org layer.")
        click.echo(f"  Repos scanned: {result['repos_scanned']}")
        click.echo(f"  Total org patterns: {result['total_org_patterns']}")
    else:
        click.echo("Nothing to promote.")


@flywheel.command(name="list")
def list_cmd() -> None:
    """List all repos with flywheel data."""
    store = FlywheelStore("_any")
    keys = store.list_repo_keys()
    if not keys:
        click.echo("No flywheel data yet.")
        return
    for key in sorted(keys):
        s = FlywheelStore(key)
        corrections = s.load_corrections()
        patterns = s.load_repo_patterns()
        click.echo(f"  {key}: {len(corrections)} corrections, {len(patterns)} patterns")


@flywheel.command(name="import")
@click.argument("target_key")
@click.argument("source_path", type=click.Path(exists=True))
@click.option("--org", "org_level", is_flag=True, help="Import org-level data.")
def import_cmd(target_key: str, source_path: str, org_level: bool) -> None:
    """Import flywheel data from a target repo's old .verdx/ directory."""
    from verdx.engine.flywheel.types import Correction, Pattern

    source = Path(source_path).resolve()
    store = FlywheelStore(sanitize_target_key(target_key))
    imported: dict[str, int] = {"corrections": 0, "patterns": 0, "overrides": 0}

    if org_level:
        org_patterns_file = source / "patterns.json"
        if org_patterns_file.is_file():
            data = json.loads(org_patterns_file.read_text(encoding="utf-8"))
            for pd in data.get("patterns", []):
                pat = Pattern(
                    id=pd["id"],
                    symptoms=pd.get("symptoms", []),
                    diagnosis=pd.get("diagnosis", ""),
                    resolution=pd.get("resolution", ""),
                    category=pd.get("category", ""),
                    domain=pd.get("domain", "unknown"),
                    seen_count=pd.get("seen_count", 1),
                    observation_signatures=pd.get("observation_signatures", []),
                    targets=pd.get("targets", []),
                    source=pd.get("source", ""),
                    created=pd.get("created", ""),
                    updated=pd.get("updated", ""),
                )
                store.save_org_pattern(pat)
                imported["patterns"] += 1

        org_blocked_file = source / "blocked.json"
        if org_blocked_file.is_file():
            blocked_data = json.loads(org_blocked_file.read_text(encoding="utf-8"))
            store.save_blocked(blocked_data.get("blocked", []))
            click.echo("  Imported blocked.json")
    else:
        fw_dir = source / ".verdx" / "flywheel"
        corrections_file = fw_dir / "corrections.jsonl"
        if corrections_file.is_file():
            for line in corrections_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                c = Correction(
                    observation_signature=d["observation_signature"],
                    target_key=d.get("target_key", target_key),
                    initial_severity=d["initial_severity"],
                    final_decision=d["final_decision"],
                    description=d.get("description", ""),
                    domain=d.get("domain", "unknown"),
                    timestamp=d.get("timestamp", ""),
                )
                store.append_correction(c)
                imported["corrections"] += 1

        patterns_file = fw_dir / "patterns.json"
        if patterns_file.is_file():
            data = json.loads(patterns_file.read_text(encoding="utf-8"))
            patterns = []
            for pd in data.get("patterns", []):
                patterns.append(
                    Pattern(
                        id=pd["id"],
                        symptoms=pd.get("symptoms", []),
                        diagnosis=pd.get("diagnosis", ""),
                        resolution=pd.get("resolution", ""),
                        category=pd.get("category", ""),
                        domain=pd.get("domain", "unknown"),
                        seen_count=pd.get("seen_count", 1),
                        observation_signatures=pd.get("observation_signatures", []),
                        targets=pd.get("targets", []),
                        source=pd.get("source", ""),
                        created=pd.get("created", ""),
                        updated=pd.get("updated", ""),
                    )
                )
            if patterns:
                store.save_patterns(patterns)
                imported["patterns"] = len(patterns)

        overrides_file = source / ".verdx" / "overrides.json"
        if overrides_file.is_file():
            raw = json.loads(overrides_file.read_text(encoding="utf-8"))
            entries = raw if isinstance(raw, list) else raw.get("overrides", [])
            if entries:
                from verdx.types import Override

                overrides = []
                for e in entries:
                    overrides.append(
                        Override(
                            signature=e["signature"],
                            decision=e["decision"],
                            justification=e.get("justification", ""),
                            by=e.get("by", "imported"),
                            date=e.get("date", ""),
                            expires=e.get("expires"),
                        )
                    )
                store.save_overrides(overrides)
                imported["overrides"] = len(overrides)

    parts = [f"{v} {k}" for k, v in imported.items() if v > 0]
    if parts:
        click.echo(f"Imported {', '.join(parts)} for {target_key}.")
    else:
        click.echo("No data found to import.")


@flywheel.command()
@click.argument("target_key", required=False, default=None)
@click.option("--org", "org_level", is_flag=True, help="Clear org-level data.")
@click.confirmation_option(prompt="This is destructive. Are you sure?")
def reset(target_key: str | None, org_level: bool) -> None:
    """Clear flywheel data (destructive)."""
    if org_level:
        store = FlywheelStore("_org")
        org_dir = store._org_dir()
        if org_dir.exists():
            shutil.rmtree(org_dir)
            click.echo("Org-level flywheel data cleared.")
        else:
            click.echo("No org-level data to clear.")
    else:
        if not target_key:
            click.echo("Error: target_key required for repo-level reset.", err=True)
            raise SystemExit(1)
        store = FlywheelStore(sanitize_target_key(target_key))
        repo_dir = store._repo_dir()
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
            click.echo(f"Repo-level flywheel data cleared for {target_key}.")
        else:
            click.echo("No repo-level data to clear.")


@flywheel.command()
def sync() -> None:
    """Commit and push flywheel data changes to the verdx-data repo."""
    from verdx._flywheel_sync import sync_flywheel

    if sync_flywheel(push=True):
        click.echo("Flywheel data synced and pushed.")
    else:
        click.echo("Nothing to sync.")


@flywheel.command(name="export")
@click.argument("target_key", required=False, default=None)
@click.option("--output", "output_path", required=True, type=click.Path(), help="Output file path.")
def export_cmd(target_key: str | None, output_path: str) -> None:
    """Export flywheel state for backup."""
    store = _store(target_key)
    fw = Flywheel(store)
    summary = fw.review()
    data = {
        "patterns": [
            {
                "id": p.id,
                "symptoms": p.symptoms,
                "diagnosis": p.diagnosis,
                "resolution": p.resolution,
                "category": p.category,
                "domain": p.domain,
                "seen_count": p.seen_count,
                "source": p.source,
                "targets": p.targets,
            }
            for p in store.load_patterns()
        ],
        "corrections_total": summary["corrections_summary"]["total"],
        "blocked": store.load_blocked(),
    }
    Path(output_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    click.echo(f"Exported to {output_path}.")
