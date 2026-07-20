from __future__ import annotations

import json
import shlex

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from drskill.models import Finding
from drskill.resolution import World


def to_json(findings: list[Finding]) -> str:
    rows = [
        dict(sorted(f.model_dump(mode="json").items())) for f in findings
    ]
    return json.dumps(rows, indent=2)


def render_harness_tables(
    world: World,
    console: Console,
    *,
    tokens: bool = False,
    harness: str | None = None,
    show_all: bool = False,
) -> None:
    hidden: list[str] = []
    for hid, hdef in sorted(world.harnesses.items()):
        if harness and hid != harness:
            continue
        if not show_all and harness is None and not world.effective(hid):
            hidden.append(hid)
            continue
        title = escape(hdef.display_name) + ("" if hdef.verified else " (best effort)")
        table = Table(title=title)
        table.add_column("skill")
        table.add_column("scope")
        table.add_column("source")
        if tokens:
            table.add_column("catalog", justify="right")
            table.add_column("body", justify="right")
        table.add_column("notes")
        cat_total = body_total = 0
        for c, d in world.harness_loads(hid):
            notes = []
            if d.shadowed_by:
                notes.append("shadowed")
            if d.via_symlink:
                notes.append("symlink")
            row = [escape(c.name), escape(d.scope), escape(c.source.kind)]
            if tokens:
                row += [str(c.token_cost.catalog_tokens), str(c.token_cost.body_tokens)]
                if d.shadowed_by is None:
                    cat_total += c.token_cost.catalog_tokens
                    body_total += c.token_cost.body_tokens
            row.append(escape(", ".join(notes)))
            table.add_row(*row)
        if tokens:
            table.add_row("total (effective)", "", "", str(cat_total), str(body_total), "",
                          style="bold")
        console.print(table)
    if hidden:
        plural = "es" if len(hidden) != 1 else ""
        console.print(
            f"[dim]{len(hidden)} more harness{plural} detected with no skills "
            f"({escape(', '.join(sorted(hidden)))}); show with --all[/dim]"
        )
    if tokens:
        console.print("[dim]token counts are approximate[/dim]")


def _print_finding(world: World, f: Finding, console: Console) -> None:
    tags = ""
    if any(
        hid in world.harnesses and not world.harnesses[hid].verified
        for hid in f.harnesses
    ):
        tags = " [dim](best effort)[/dim]"
    console.print(f"  [[bold]{escape(f.check_id)}[/bold]] {escape(f.message)}{tags}")
    if f.harnesses:
        console.print(f"      harnesses: {escape(', '.join(f.harnesses))}")
    for cmd in f.fix_commands:
        console.print(f"      fix: {escape(cmd)}")
    if f.contributor_names:
        names = " ".join(shlex.quote(n) for n in f.contributor_names)
        console.print(f"      or:  drskill ack {escape(f.check_id)} {escape(names)}")
    else:
        console.print(f"      or:  drskill ack {escape(f.check_id)}")


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
