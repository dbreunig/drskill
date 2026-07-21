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
    if f.check_id.startswith("mcp-"):
        return not hdef.mcp_verified
    if f.check_id in PRECEDENCE_CHECKS:
        return not hdef.precedence_verified
    return not hdef.paths_verified


def _all_system(world: World, f: Finding) -> bool:
    cs = [world.contributors.get(cid) for cid in f.contributors]
    cs = [c for c in cs if c is not None]
    return bool(cs) and all(c.system for c in cs)


def _context_bill(world: World):
    best = None
    for hid in world.harnesses:
        skill_tok = tool_tok = 0
        for c in world.effective(hid):
            if c.kind == "mcp_tool":
                tool_tok += c.token_cost.catalog_tokens
            else:
                skill_tok += c.token_cost.catalog_tokens
        if tool_tok == 0:
            continue
        if best is None or skill_tok + tool_tok > best[1] + best[2]:
            best = (hid, skill_tok, tool_tok)
    return best


def sort_findings(
    world: World, findings: list[Finding], seen: set[str]
) -> list[Finding]:
    """New before seen, your skills before harness-vendored ones, then a
    stable check/message tiebreak."""
    return sorted(
        findings,
        key=lambda f: (
            0 if f.fingerprint not in seen else 1,
            1 if _all_system(world, f) else 0,
            f.check_id,
            f.message,
        ),
    )


def short_id(f: Finding) -> str:
    """First four hex chars of the fingerprint: the finding's ack handle."""
    return f.fingerprint.split(":", 1)[1][:4]


def _print_finding(
    world: World, f: Finding, console: Console, new: bool = False
) -> bool:
    marked = False
    tag = "[bold cyan]new[/bold cyan] " if new else ""
    console.print(
        f"  [[bold]{escape(short_id(f))}[/bold]] {tag}{escape(f.check_id)}: "
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
        detected = set(world.harnesses)
        if len(detected) >= 2 and set(f.harnesses) == detected:
            qm = [label for label in labels if label.endswith("?")]
            line = f"all {len(detected)} harnesses"
            if qm:
                line += f" ({', '.join(qm)})"
        else:
            line = ", ".join(labels)
        suffix = "  [dim]\\[system skill][/dim]" if _all_system(world, f) else ""
        console.print(f"      harnesses: {escape(line)}{suffix}")
    for cmd in f.fix_commands:
        console.print(f"      fix: {escape(_sanitize(cmd))}")
    console.print()
    return marked


def print_findings(
    world: World,
    findings: list[Finding],
    console: Console,
    seen: set[str] | frozenset[str] = frozenset(),
) -> None:
    for f in findings:
        _print_finding(world, f, console, new=f.fingerprint not in seen)


def render(
    world: World,
    active: list[Finding],
    acked: list[Finding],
    console: Console,
    seen: set[str] | frozenset[str] = frozenset(),
) -> None:
    populated = [hid for hid in world.harnesses if world.effective(hid)]
    empty = len(world.harnesses) - len(populated)
    n_skills = len(world.contributors)
    plural = "es" if len(populated) != 1 else ""
    header = f"[bold]drskill scan[/bold] — {len(populated)} harness{plural}"
    if empty:
        header += f" ({empty} more empty)"
    header += f", {n_skills} skills"
    if world.mcp_servers:
        n_mcp = len(world.mcp_servers)
        header += f", {n_mcp} MCP server{'s' if n_mcp != 1 else ''}"
    console.print(header)
    ordered = sort_findings(world, active, set(seen))
    errors = [f for f in ordered if f.severity == "error"]
    warnings = [f for f in ordered if f.severity == "warning"]
    notes = [f for f in ordered if f.severity == "note"]
    new_count = sum(1 for f in ordered if f.fingerprint not in seen)
    if not active:
        console.print("\n[green]No findings.[/green]", end="")
    any_marked = False
    if errors:
        console.print("\n[red bold]ERRORS[/red bold]")
        for f in errors:
            any_marked = (
                _print_finding(world, f, console, new=f.fingerprint not in seen)
                or any_marked
            )
    if warnings:
        console.print("\n[yellow bold]WARNINGS[/yellow bold]")
        for f in warnings:
            any_marked = (
                _print_finding(world, f, console, new=f.fingerprint not in seen)
                or any_marked
            )
    if notes:
        console.print("\n[dim bold]NOTES[/dim bold]")
        for f in notes:
            any_marked = (
                _print_finding(world, f, console, new=f.fingerprint not in seen)
                or any_marked
            )
    summary = (
        f"\n{len(errors)} error{'s' if len(errors) != 1 else ''}, "
        f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
    )
    if notes:
        summary += f", {len(notes)} note{'s' if len(notes) != 1 else ''}"
    extras = []
    if new_count:
        extras.append(f"{new_count} new")
    if acked:
        extras.append(f"{len(acked)} acknowledged")
    if extras:
        summary += f" ({', '.join(extras)})"
    summary += " · token counts are approximate"
    console.print(summary)
    bill = _context_bill(world)
    if bill:
        hid, skill_tok, tool_tok = bill
        console.print(
            f"largest context bill: {escape(hid)}, about {skill_tok + tool_tok} "
            f"tokens ({skill_tok} skill catalog + {tool_tok} MCP tool "
            f"definitions), approximate"
        )
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
    # Notes need no ack, so the recap only lists error and warning findings.
    ackable = [f for f in ordered if f.severity != "note"]
    if ackable:
        example = " ".join(short_id(f) for f in ackable[:2])
        console.print(f"\nack findings by id, e.g. `drskill ack {escape(example)}`:")
        width = max(len(f.check_id) for f in ackable)
        for f in ackable:
            names = ", ".join(f.contributor_names)
            tag = " [bold cyan]new[/bold cyan]" if f.fingerprint not in seen else "    "
            console.print(
                f"  [bold]{escape(short_id(f))}[/bold]{tag} "
                f"{escape(f.check_id.ljust(width))}  {escape(_sanitize(names))}"
            )
