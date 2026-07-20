from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from drskill import ledger, report
from drskill.pipeline import run_scan

app = typer.Typer(add_completion=False, help="brew doctor for your agent's skill loadout")
console = Console()


def _home() -> Path:
    env = os.environ.get("DRSKILL_HOME")
    return Path(env) if env else Path.home()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """brew doctor for your agent's skill loadout"""
    if ctx.invoked_subcommand is None:
        if not ctx.protected_args:
            raise typer.Exit(code=0)


@app.command()
def scan(
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global", help="analyze machine-level skills only"),
    ci: bool = typer.Option(False, "--ci", help="exit 2 on unacknowledged warnings"),
    as_json: bool = typer.Option(False, "--json", help="emit findings as JSON"),
) -> None:
    """Analyze every detected harness's skill set and report findings."""
    home = _home()
    config = ledger.load_config(ledger.ledger_path(root, home, global_mode))
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
