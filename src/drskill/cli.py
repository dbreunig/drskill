from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from drskill import ledger, report, state
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
description_overlap = 0.6   # cosine similarity that clusters descriptions
generic_min_distinct_tokens = 2  # fewer distinctive words than this is too vague
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


def _warn_if_undetected(
    harness: str | None, root: Path, home: Path, global_mode: bool
) -> None:
    if harness is None:
        return
    from drskill.harnesses import detect_harnesses

    detected = {h.id for h in detect_harnesses(root, home, global_mode)}
    if harness not in detected:
        console.print(
            f"[dim]note: harness {escape(harness)} is not detected on this "
            "machine; scanning its search paths anyway[/dim]"
        )


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
    detailed: bool = typer.Option(False, "--detailed", help="also print each harness's skill table"),
    show_all: bool = typer.Option(False, "--all", help="with --detailed, include harnesses with no skills"),
    harness: str | None = typer.Option(None, "--harness", help="scope the scan to one harness"),
) -> None:
    """Analyze every detected harness's skill set and report findings."""
    _validate_harness(harness)
    home = _home()
    config = _load_config_or_exit(ledger.ledger_path(root, home, global_mode))
    world, findings = run_scan(root, home, global_mode, config, harness=harness)
    active, acked = ledger.filter_findings(findings, config)
    if as_json:
        print(report.to_json(active))
    else:
        _warn_if_undetected(harness, root, home, global_mode)
        spath = state.state_path(root, home, global_mode)
        report.render(
            world, active, acked, console, seen=set(state.load_seen(spath))
        )
        # active plus acked, so an acked finding stays seen if later un-acked
        state.mark_seen(spath, [f.fingerprint for f in findings], dt.date.today())
        if detailed:
            console.print()
            report.render_harness_tables(
                world, console, tokens=False, harness=harness, show_all=show_all
            )
    if any(f.severity == "error" for f in active):
        raise typer.Exit(1)
    if ci and any(f.severity == "warning" for f in active):
        raise typer.Exit(2)


@app.command()
def ack(
    refs: list[str] = typer.Argument(
        None,
        help="finding ids from the report, or a check id followed by skill names",
    ),
    ack_all: bool = typer.Option(
        False, "--all",
        help="ack every active finding, or every finding of the named check",
    ),
    note: str | None = typer.Option(None, "--note"),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
) -> None:
    """Acknowledge findings so they stay silent until the content changes."""
    import re

    home = _home()
    path = ledger.ledger_path(root, home, global_mode)
    config = _load_config_or_exit(path)
    _world, findings = run_scan(root, home, global_mode, config)
    active, _ = ledger.filter_findings(findings, config)
    from drskill.checks import REGISTRY

    refs = refs or []
    targets: list = []
    if ack_all:
        if not refs:
            targets = list(active)
        elif len(refs) == 1 and refs[0] in REGISTRY:
            targets = [f for f in active if f.check_id == refs[0]]
        else:
            console.print("[red]--all takes no arguments, or exactly one check id[/red]")
            raise typer.Exit(1)
        if not targets:
            console.print("[red]No active finding matches[/red]")
            raise typer.Exit(1)
    elif refs and refs[0] in REGISTRY:
        check_id, skills = refs[0], refs[1:]
        wanted = set(skills)
        if wanted:
            exact = [f for f in active if f.check_id == check_id and set(f.contributor_names) == wanted]
            superset = [f for f in active if f.check_id == check_id and wanted <= set(f.contributor_names)]
            matches = exact or superset
            if len(matches) > 1:
                console.print(f"[red]Ambiguous:[/red] {len(matches)} findings match; name all involved skills")
                raise typer.Exit(1)
        else:
            # a bare check id acks the whole class of findings
            matches = [f for f in active if f.check_id == check_id]
        if not matches:
            console.print(f"[red]No active finding matches[/red] {escape(check_id)} {escape(' '.join(skills))}")
            raise typer.Exit(1)
        targets = matches
    elif refs and all(re.fullmatch(r"[0-9a-f]{4,64}", r) for r in refs):
        for ref in refs:
            hits = [f for f in active if f.fingerprint.split(":", 1)[1].startswith(ref)]
            if not hits:
                console.print(f"[red]No active finding matches[/red] id {escape(ref)}")
                raise typer.Exit(1)
            if len(hits) > 1:
                console.print(
                    f"[red]Ambiguous id[/red] {escape(ref)}: matches "
                    f"{len(hits)} findings; use more characters"
                )
                raise typer.Exit(1)
            if hits[0] not in targets:
                targets.append(hits[0])
    else:
        console.print(
            "[red]Nothing to ack:[/red] pass finding ids from the report, "
            "a check id with skill names, or --all"
        )
        raise typer.Exit(1)

    for f in targets:
        ledger.append_ack(
            path,
            Ack(check=f.check_id, skills=sorted(f.contributor_names),
                fingerprint=f.fingerprint, note=note, date=dt.date.today()),
        )
        label = f"{f.check_id} " + ", ".join(f.contributor_names) if f.contributor_names else f.check_id
        console.print(f"Acknowledged [bold]{escape(label)}[/bold]")
    console.print(f"{len(targets)} finding{'s' if len(targets) != 1 else ''} → {escape(str(path))}")


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
    world, _findings = run_scan(root, home, global_mode, config, harness=harness)
    _warn_if_undetected(harness, root, home, global_mode)
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
