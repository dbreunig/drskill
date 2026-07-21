from __future__ import annotations

import datetime as dt
import os
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from drskill import deep, interactive, ledger, report, state
from drskill.ledger import Ack
from drskill.pipeline import run_scan

key_source = interactive.read_key  # patched in tests
line_source = input  # patched in tests

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


def _load_effective_config_or_exit(
    root: Path, home: Path, global_mode: bool
) -> ledger.Config:
    try:
        return ledger.load_effective_config(root, home, global_mode)
    except ledger.LedgerError as e:
        console.print(f"[red]error:[/red] {escape(str(e))}")
        raise typer.Exit(1)


@app.callback()
def main() -> None:
    pass


def _resolve_refs(refs: list[str], active: list) -> list:
    """Resolve 4-hex finding ids and bare check ids to active findings.
    Exits 1 on no match or on an ambiguous id. Shared by ack and show."""
    import re

    from drskill.checks import REGISTRY

    targets: list = []
    for ref in refs:
        if ref in REGISTRY:
            matches = [f for f in active if f.check_id == ref]
            if not matches:
                console.print(f"[red]No active finding matches[/red] {escape(ref)}")
                raise typer.Exit(1)
            targets += [f for f in matches if f not in targets]
        elif re.fullmatch(r"[0-9a-f]{4,64}", ref):
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
                f"[red]Not a finding id or check id:[/red] {escape(ref)}"
            )
            raise typer.Exit(1)
    return targets


@app.command()
def scan(
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global", help="analyze machine-level skills only"),
    ci: bool = typer.Option(False, "--ci", help="exit 2 on unacknowledged warnings"),
    as_json: bool = typer.Option(False, "--json", help="emit findings as JSON"),
    detailed: bool = typer.Option(False, "--detailed", help="also print each harness's skill table"),
    show_all: bool = typer.Option(False, "--all", help="with --detailed, include harnesses with no skills"),
    harness: str | None = typer.Option(None, "--harness", help="scope the scan to one harness"),
    deep_mode: bool = typer.Option(False, "--deep", help="judge flagged pairs with the configured model"),
    max_calls: int = typer.Option(25, "--max-calls", help="hard budget of model calls per --deep run"),
) -> None:
    """Analyze every detected harness's skill set and report findings."""
    _validate_harness(harness)
    home = _home()
    config = _load_effective_config_or_exit(root, home, global_mode)
    judge = None
    if deep_mode:
        from drskill import deep_llm

        deep.load_user_env(home)
        try:
            judge = deep_llm.build_judge(config.deep.model)
        except deep_llm.DeepUnavailableError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
    world, findings = run_scan(
        root, home, global_mode, config, harness=harness, judge=judge, max_calls=max_calls
    )
    active, acked = ledger.filter_findings(findings, config)
    if as_json:
        print(report.to_json(active))
    else:
        _warn_if_undetected(harness, root, home, global_mode)
        spath = state.state_path(root, home, global_mode)
        report.render(
            world, active, acked, console, seen=set(state.load_seen(spath))
        )
        # active plus acked, so an acked finding stays seen if later un-acked.
        # A --harness scan sees only a slice of the project's findings, and
        # writing it would prune every other harness's seen entries.
        if harness is None:
            state.mark_seen(
                spath, [f.fingerprint for f in findings], dt.date.today()
            )
        if deep_mode:
            last_error = getattr(judge, "last_error", None)
            if last_error:
                flat = " ".join(str(last_error).split())
                console.print(
                    f"[yellow]deep: model calls are failing; last error: "
                    f"{escape(flat)}[/yellow]"
                )
            cache = deep.load_cache(deep.cache_dir(root, home, global_mode))
            remaining = deep.unjudged_count(world, active, cache)
            if remaining:
                plural = "s" if remaining != 1 else ""
                console.print(
                    f"deep: {remaining} flagged pair{plural} still unjudged; "
                    "raise --max-calls to judge more"
                )
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
    force_local: bool = typer.Option(
        False, "--local", help="record in the project ledger regardless of scope"
    ),
    force_global: bool = typer.Option(
        False, "--global-ack", help="record in the machine ledger (~/.drskill.toml)"
    ),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
) -> None:
    """Acknowledge findings so they stay silent until the content changes."""
    import re

    if force_local and force_global:
        console.print("[red]--local and --global-ack are mutually exclusive[/red]")
        raise typer.Exit(1)
    if global_mode and (force_local or force_global):
        console.print("[red]--global mode already writes the machine ledger[/red]")
        raise typer.Exit(1)
    home = _home()
    config = _load_effective_config_or_exit(root, home, global_mode)
    world, findings = run_scan(root, home, global_mode, config)
    active, _ = ledger.filter_findings(findings, config)
    # Notes need no ack; sweeping them into the ledger would hide them and
    # leave a stale ack behind if the verdict cache is ever pruned.
    active = [f for f in active if f.severity != "note"]
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
        targets = _resolve_refs(refs, active)
    else:
        console.print(
            "[red]Nothing to ack:[/red] pass finding ids from the report, "
            "a check id with skill names, or --all"
        )
        raise typer.Exit(1)

    global_ledger = ledger.ledger_path(root, home, True)
    dest_counts: dict[Path, int] = {}
    for f in targets:
        dest = ledger.ack_destination(
            world, f, root, home, global_mode,
            force_local=force_local, force_global=force_global,
        )
        ledger.append_ack(
            dest,
            Ack(check=f.check_id, skills=sorted(f.contributor_names),
                fingerprint=f.fingerprint, note=note, date=dt.date.today()),
        )
        dest_counts[dest] = dest_counts.get(dest, 0) + 1
        label = f"{f.check_id} " + ", ".join(f.contributor_names) if f.contributor_names else f.check_id
        suffix = ""
        if dest == global_ledger and not global_mode:
            suffix = " → ~/.drskill.toml (machine-level skills)"
        console.print(f"Acknowledged [bold]{escape(label)}[/bold]{escape(suffix)}")
    for dest, n in dest_counts.items():
        console.print(f"{n} finding{'s' if n != 1 else ''} → {escape(str(dest))}")


@app.command()
def show(
    refs: list[str] = typer.Argument(..., help="finding ids or check ids"),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
    harness: str | None = typer.Option(None, "--harness"),
) -> None:
    """Print the full evidence for specific findings."""
    _validate_harness(harness)
    home = _home()
    config = _load_effective_config_or_exit(root, home, global_mode)
    world, findings = run_scan(root, home, global_mode, config, harness=harness)
    active, _ = ledger.filter_findings(findings, config)
    targets = _resolve_refs(refs, active)
    ordered = report.sort_findings(world, targets, set())
    report.print_findings(
        world, ordered, console, seen={f.fingerprint for f in targets}
    )  # seen = everything: show never tags new


@app.command()
def review(
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
    harness: str | None = typer.Option(None, "--harness"),
) -> None:
    """Walk the findings one at a time and decide each with one keypress."""
    refusal = interactive.can_interact()
    if refusal:
        console.print(escape(refusal))
        raise typer.Exit(1)
    _validate_harness(harness)
    home = _home()
    config = _load_effective_config_or_exit(root, home, global_mode)
    world, findings = run_scan(root, home, global_mode, config, harness=harness)
    active, _ = ledger.filter_findings(findings, config)
    active = [f for f in active if f.severity != "note"]
    if not active:
        console.print("[green]No findings to review.[/green]")
        return
    spath = state.state_path(root, home, global_mode)
    seen = set(state.load_seen(spath))
    ordered = report.sort_findings(world, active, seen)
    acked: list[tuple] = []  # (finding, destination path)
    fixes: list[str] = []
    displayed: set[str] = set()
    undecided = 0
    quit_early = False
    for idx, f in enumerate(ordered, start=1):
        console.print(f"[dim]{idx} of {len(ordered)}[/dim]")
        report.print_findings(world, [f], console, seen=seen)
        displayed.add(f.fingerprint)
        console.print(
            "[bold]a[/bold] ack · [bold]n[/bold] ack+note · [bold]f[/bold] queue fix"
            " · [bold]s[/bold] skip · [bold]q[/bold] quit"
        )
        while True:
            key = key_source()
            if key in ("a", "n"):
                ack_note = None
                if key == "n":
                    try:
                        ack_note = line_source("note: ").strip() or None
                    except KeyboardInterrupt:
                        quit_early = True
                        break
                dest = ledger.ack_destination(world, f, root, home, global_mode)
                ledger.append_ack(dest, Ack(
                    check=f.check_id, skills=sorted(f.contributor_names),
                    fingerprint=f.fingerprint, note=ack_note,
                    date=dt.date.today(),
                ))
                acked.append((f, dest))
                break
            if key == "f":
                if f.fix_commands:
                    fixes.extend(f.fix_commands)
                else:
                    undecided += 1  # nothing to queue; the finding stays open
                break
            if key == "s":
                undecided += 1
                break
            if key in ("q", "\x03"):  # q or ctrl-c
                quit_early = True
                break
            console.print("[dim]a/n/f/s/q[/dim]")
        if quit_early:
            undecided += len(ordered) - idx + 1
            break
    _review_summary(acked, fixes, undecided, home)
    if harness is None:
        # only what was displayed becomes seen; keep already-seen entries
        # that still correspond to current findings alive through the prune
        current = {f.fingerprint for f in findings}
        state.mark_seen(spath, displayed | (seen & current), dt.date.today())


def _review_summary(
    acked: list[tuple], fixes: list[str], undecided: int, home: Path
) -> None:
    from drskill.report import short_id

    for f, dest in acked:
        if dest == home / ".drskill.toml":
            where = " → ~/.drskill.toml"
        else:
            where = f" → {dest.name}"
        console.print(
            f"acked [bold]{escape(short_id(f))}[/bold] "
            f"{escape(f.check_id)}{escape(where)}"
        )
    if fixes:
        block = "\n".join(fixes)
        console.print("\nqueued fix commands:\n")
        # display is sanitized; the clipboard gets the raw command text
        console.print(escape(report._sanitize(block)))
        if _to_clipboard(block):
            console.print("[dim](copied to clipboard)[/dim]")
    if undecided:
        console.print(
            f"\n{undecided} finding{'s' if undecided != 1 else ''} left undecided"
        )


def _to_clipboard(text: str) -> bool:
    import shutil
    import subprocess

    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                return True
            except (OSError, subprocess.SubprocessError):
                return False
    return False


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
    config = _load_effective_config_or_exit(root, home, global_mode)
    world, _findings = run_scan(root, home, global_mode, config, harness=harness)
    _warn_if_undetected(harness, root, home, global_mode)
    report.render_harness_tables(
        world, console, tokens=tokens, harness=harness, show_all=show_all
    )


@app.command()
def cache(
    action: str = typer.Argument(..., help="stats or prune"),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global", help="use the machine cache"),
) -> None:
    """Inspect or prune the committed deep verdict cache."""
    home = _home()
    cdir = deep.cache_dir(root, home, global_mode)
    entries = deep.load_cache(cdir)
    if action == "stats":
        console.print(f"{len(entries)} cached verdicts in {escape(str(cdir))}")
        if not entries:
            return
        for name, count in sorted(Counter(v.verdict for v in entries.values()).items()):
            console.print(f"  {escape(name)}: {count}")
        for name, count in sorted(Counter(v.model for v in entries.values()).items()):
            console.print(f"  {escape(name)}: {count}")
        dates = sorted(v.date for v in entries.values())
        console.print(f"  oldest {escape(dates[0])}, newest {escape(dates[-1])}")
    elif action == "prune":
        config = _load_effective_config_or_exit(root, home, global_mode)
        world, findings = run_scan(root, home, global_mode, config)
        valid = {deep.pair_key(a, b) for a, b in deep.flagged_pairs(world, findings)}
        # Walk the files, not the parsed entries, so corrupt files (which
        # load_cache skips) are pruned instead of lingering forever.
        removed = kept = 0
        for p in sorted(cdir.glob("*.json")) if cdir.is_dir() else []:
            if p.stem in valid and p.stem in entries:
                kept += 1
            else:
                p.unlink()
                removed += 1
        console.print(f"removed {removed} stale verdicts, kept {kept}")
    else:
        console.print(
            f"[red]Unknown action:[/red] {escape(action)} (use stats or prune)"
        )
        raise typer.Exit(1)


@app.command()
def init(root: Path = typer.Option(Path("."), "--root", hidden=True)) -> None:
    """Write a starter drskill.toml with default budgets and thresholds."""
    path = root / "drskill.toml"
    if path.exists():
        console.print(f"[red]{path} already exists[/red]; not overwriting")
        raise typer.Exit(1)
    path.write_text(INIT_TEMPLATE)
    console.print(f"Wrote {path}")
