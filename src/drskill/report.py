from __future__ import annotations

import json

from rich.console import Console

from drskill.models import Finding
from drskill.resolution import World


def to_json(findings: list[Finding]) -> str:
    rows = [
        dict(sorted(f.model_dump(mode="json").items())) for f in findings
    ]
    return json.dumps(rows, indent=2)


def _print_finding(world: World, f: Finding, console: Console) -> None:
    tags = ""
    if any(
        hid in world.harnesses and not world.harnesses[hid].verified
        for hid in f.harnesses
    ):
        tags = " [dim](best effort)[/dim]"
    console.print(f"  [[bold]{f.check_id}[/bold]] {f.message}{tags}")
    if f.harnesses:
        console.print(f"      harnesses: {', '.join(f.harnesses)}")
    for cmd in f.fix_commands:
        console.print(f"      fix: {cmd}")
    if f.contributor_names:
        names = " ".join(f.contributor_names)
        console.print(f"      or:  drskill ack {f.check_id} {names}")


def render(
    world: World, active: list[Finding], acked: list[Finding], console: Console
) -> None:
    n_harness = len(world.harnesses)
    n_skills = len(world.contributors)
    console.print(f"[bold]drskill scan[/bold] — {n_harness} harnesses, {n_skills} skills")
    errors = [f for f in active if f.severity == "error"]
    warnings = [f for f in active if f.severity == "warning"]
    if not active:
        console.print("\n[green]No findings.[/green]", end="")
    if errors:
        console.print("\n[red bold]ERRORS[/red bold]")
        for f in errors:
            _print_finding(world, f, console)
    if warnings:
        console.print("\n[yellow bold]WARNINGS[/yellow bold]")
        for f in warnings:
            _print_finding(world, f, console)
    summary = (
        f"\n{len(errors)} error{'s' if len(errors) != 1 else ''}, "
        f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
    )
    if acked:
        summary += f" ({len(acked)} acknowledged)"
    summary += " · token counts are approximate"
    console.print(summary)
