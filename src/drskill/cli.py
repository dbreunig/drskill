from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from drskill import ledger, report
from drskill.ledger import Ack
from drskill.pipeline import run_scan

INIT_TEMPLATE = """\
# drskill configuration and decision ledger.
# Commit this file. Acks silence a finding until the skill content changes.

[budget]
catalog_tokens_max = 6000   # per-harness startup catalog budget (approximate tokens)
body_tokens_warn = 20000    # per-skill body ceiling (approximate tokens)

[thresholds]
near_duplicate = 0.85       # Jaccard similarity that counts as a near duplicate
"""

app = typer.Typer(add_completion=False, help="brew doctor for your agent's skill loadout")
console = Console()


def _home() -> Path:
    env = os.environ.get("DRSKILL_HOME")
    return Path(env) if env else Path.home()


def _validate_harness(harness: str | None) -> None:
    if harness is None:
        return
    from drskill.harnesses import load_harnesses

    ids = sorted(h.id for h in load_harnesses())
    if harness not in ids:
        console.print(
            f"[red]error:[/red] unknown harness {escape(harness)}; "
            f"valid ids: {escape(', '.join(ids))}"
        )
        raise typer.Exit(1)


def _load_config_or_exit(path: Path) -> ledger.Config:
    try:
        return ledger.load_config(path)
    except ledger.LedgerError as e:
        console.print(f"[red]error:[/red] {escape(str(e))}")
        raise typer.Exit(1)


@app.callback()
def main() -> None:
    pass


@app.command()
def scan(
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global", help="analyze machine-level skills only"),
    ci: bool = typer.Option(False, "--ci", help="exit 2 on unacknowledged warnings"),
    as_json: bool = typer.Option(False, "--json", help="emit findings as JSON"),
) -> None:
    """Analyze every detected harness's skill set and report findings."""
    home = _home()
    config = _load_config_or_exit(ledger.ledger_path(root, home, global_mode))
    world, findings = run_scan(root, home, global_mode, config)
    active, acked = ledger.filter_findings(findings, config)
    if as_json:
        print(report.to_json(active))
    else:
        report.render(world, active, acked, console)
    if any(f.severity == "error" for f in active):
        raise typer.Exit(1)
    if ci and any(f.severity == "warning" for f in active):
        raise typer.Exit(2)


@app.command()
def ack(
    check_id: str = typer.Argument(...),
    skills: list[str] = typer.Argument(
        None, help="skills to acknowledge for; omit for contributor-less findings"
    ),
    note: str | None = typer.Option(None, "--note"),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
) -> None:
    """Acknowledge a finding so it stays silent until the skills change."""
    home = _home()
    path = ledger.ledger_path(root, home, global_mode)
    config = _load_config_or_exit(path)
    world, findings = run_scan(root, home, global_mode, config)
    active, _ = ledger.filter_findings(findings, config)
    wanted = set(skills or [])
    if wanted:
        exact = [f for f in active if f.check_id == check_id and set(f.contributor_names) == wanted]
        superset = [f for f in active if f.check_id == check_id and wanted <= set(f.contributor_names)]
        matches = exact or superset
    else:
        matches = [f for f in active if f.check_id == check_id and not f.contributor_names]
    if not matches:
        console.print(f"[red]No active finding matches[/red] {escape(check_id)} {escape(' '.join(skills or []))}")
        raise typer.Exit(1)
    if len(matches) > 1:
        if wanted:
            console.print(f"[red]Ambiguous:[/red] {len(matches)} findings match; name all involved skills")
        else:
            candidates = "; ".join(escape(m.message) for m in matches)
            console.print(f"[red]Ambiguous:[/red] {len(matches)} findings match: {candidates}")
        raise typer.Exit(1)
    f = matches[0]
    ledger.append_ack(
        path,
        Ack(check=check_id, skills=sorted(f.contributor_names),
            fingerprint=f.fingerprint, note=note, date=dt.date.today()),
    )
    if f.contributor_names:
        console.print(f"Acknowledged [bold]{escape(check_id)}[/bold] for {escape(', '.join(f.contributor_names))} → {escape(str(path))}")
    else:
        console.print(f"Acknowledged [bold]{escape(check_id)}[/bold] → {escape(str(path))}")


@app.command("list")
def list_cmd(
    tokens: bool = typer.Option(False, "--tokens"),
    harness: str | None = typer.Option(None, "--harness"),
    show_all: bool = typer.Option(False, "--all", help="include harnesses with no skills"),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
) -> None:
    """Show each harness's effective skill set."""
    _validate_harness(harness)
    home = _home()
    config = _load_config_or_exit(ledger.ledger_path(root, home, global_mode))
    world, _findings = run_scan(root, home, global_mode, config)
    report.render_harness_tables(
        world, console, tokens=tokens, harness=harness, show_all=show_all
    )


@app.command()
def init(root: Path = typer.Option(Path("."), "--root", hidden=True)) -> None:
    """Write a starter drskill.toml with default budgets and thresholds."""
    path = root / "drskill.toml"
    if path.exists():
        console.print(f"[red]{path} already exists[/red]; not overwriting")
        raise typer.Exit(1)
    path.write_text(INIT_TEMPLATE)
    console.print(f"Wrote {path}")
