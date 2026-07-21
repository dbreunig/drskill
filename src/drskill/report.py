from __future__ import annotations

import json
import re
import shlex

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from drskill.models import Finding
from drskill.resolution import World

# Rich's escape() neutralizes markup but passes invisible and bidirectional
# characters through, so adversarial skill text could reorder or hide parts
# of the report. Escape them at render time, belt and suspenders with the
# checks' own snippet escaping.
_INVISIBLE = re.compile(r"[\u200b\ufeff\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def _sanitize(text: str) -> str:
    return _INVISIBLE.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


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
        if not hdef.paths_verified:
            suffix = " (paths unverified)"
        elif not hdef.precedence_verified:
            suffix = " (collision rules unverified)"
        else:
            suffix = ""
        title = escape(hdef.display_name) + suffix
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


# Checks whose findings depend on name-collision (precedence) rules; every
# other check depends only on which directories are read (paths).
PRECEDENCE_CHECKS = frozenset({"name-shadow", "double-load"})


def _facet_unverified(world: World, f: Finding, hid: str) -> bool:
    hdef = world.harnesses.get(hid)
    if hdef is None:
        return False
    if f.check_id in PRECEDENCE_CHECKS:
        return not hdef.precedence_verified
    return not hdef.paths_verified


def short_id(f: Finding) -> str:
    """First four hex chars of the fingerprint: the finding's ack handle."""
    return f.fingerprint.split(":", 1)[1][:4]


def _print_finding(world: World, f: Finding, console: Console) -> bool:
    marked = False
    console.print(
        f"  [[bold]{escape(short_id(f))}[/bold]] {escape(f.check_id)}: "
        f"{escape(_sanitize(f.message))}"
    )
    if f.harnesses:
        labels = []
        for hid in f.harnesses:
            if _facet_unverified(world, f, hid):
                labels.append(f"{hid}?")
                marked = True
            else:
                labels.append(hid)
        console.print(f"      harnesses: {escape(', '.join(labels))}")
    for cmd in f.fix_commands:
        console.print(f"      fix: {escape(_sanitize(cmd))}")
    console.print()
    return marked


def render(
    world: World, active: list[Finding], acked: list[Finding], console: Console
) -> None:
    populated = [hid for hid in world.harnesses if world.effective(hid)]
    empty = len(world.harnesses) - len(populated)
    n_skills = len(world.contributors)
    plural = "es" if len(populated) != 1 else ""
    header = f"[bold]drskill scan[/bold] — {len(populated)} harness{plural}"
    if empty:
        header += f" ({empty} more empty)"
    header += f", {n_skills} skills"
    console.print(header)
    errors = [f for f in active if f.severity == "error"]
    warnings = [f for f in active if f.severity == "warning"]
    if not active:
        console.print("\n[green]No findings.[/green]", end="")
    any_marked = False
    if errors:
        console.print("\n[red bold]ERRORS[/red bold]")
        for f in errors:
            any_marked = _print_finding(world, f, console) or any_marked
    if warnings:
        console.print("\n[yellow bold]WARNINGS[/yellow bold]")
        for f in warnings:
            any_marked = _print_finding(world, f, console) or any_marked
    summary = (
        f"\n{len(errors)} error{'s' if len(errors) != 1 else ''}, "
        f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
    )
    if acked:
        summary += f" ({len(acked)} acknowledged)"
    summary += " · token counts are approximate"
    console.print(summary)
    binary = oversize = 0
    affected = 0
    for c in world.contributors.values():
        skipped = [f for f in c.bundled_files if not f.is_text or f.oversize]
        if skipped:
            affected += 1
            binary += sum(1 for f in skipped if not f.is_text)
            oversize += sum(1 for f in skipped if f.is_text and f.oversize)
    if binary or oversize:
        parts = []
        if binary:
            parts.append(f"{binary} binary")
        if oversize:
            parts.append(f"{oversize} over 1 MiB")
        total = binary + oversize
        console.print(
            f"[dim]{total} bundled file{'s' if total != 1 else ''} not content "
            f"scanned ({', '.join(parts)}) across {affected} "
            f"skill{'s' if affected != 1 else ''}[/dim]"
        )
    if any_marked:
        console.print(
            "[dim]? = drskill has not verified this harness's skill-loading rules[/dim]"
        )
    if active:
        example = " ".join(short_id(f) for f in active[:2])
        console.print(f"\nack findings by id, e.g. `drskill ack {escape(example)}`:")
        width = max(len(f.check_id) for f in active)
        for f in active:
            names = ", ".join(f.contributor_names)
            console.print(
                f"  [bold]{escape(short_id(f))}[/bold] "
                f"{escape(f.check_id.ljust(width))}  {escape(_sanitize(names))}"
            )
