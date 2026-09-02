from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from drskill import deep, interactive, ledger, mcp_connect as mcp_connect_mod, report, service, state
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

app = typer.Typer(add_completion=False, no_args_is_help=True, help="brew doctor for your agent's skill loadout")
loadout_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Manage loadouts on the drskill service")
app.add_typer(loadout_app, name="loadout")
console = Console()


def _home() -> Path:
    env = os.environ.get("DRSKILL_HOME")
    return Path(env) if env else Path.home()


def _service_credentials() -> tuple[dict, str]:
    creds = service.load_credentials()
    if not creds:
        typer.echo("Not signed in. Run: drskill login")
        raise typer.Exit(1)
    return creds, creds.get("service_url") or service.service_url()


def _parse_ref(ref: str) -> tuple[str, str]:
    owner, _, slug = ref.partition("/")
    if not owner or not slug or "/" in slug:
        typer.echo(f"Expected owner/slug, got {ref!r}")
        raise typer.Exit(1)
    return owner, slug


def _echo_service_error(err: "service.ServiceError") -> None:
    typer.echo(err.message)
    for field, messages in (err.details or {}).items():
        for message in messages if isinstance(messages, list) else [messages]:
            typer.echo(f"  {field}: {message}")


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


def _scan_with_status(fn):
    """Run a world-building step under the live one-line spinner scan uses.

    Rich disables the animation on non-TTY output, so piped/captured runs
    are untouched; the callback names each step (discovery, MCP configs,
    every check) exactly as `scan` does.
    """
    with console.status("[bold]starting[/bold]", spinner="dots") as status:
        return fn(lambda m: status.update(f"[bold]{escape(m)}[/bold]"))


@app.callback()
def main() -> None:
    pass


def _save_approved_baseline(world, f, root: Path, home: Path, global_mode: bool) -> None:
    """Acking an approval baseline records what was approved; keep a copy
    so a later rug-pull warning can name and quote what changed."""
    if f.check_id == "mcp-tools-unreviewed":
        from drskill import mcp_connect as mcpc
        from drskill.checks.mcp_tools import unreviewed_fingerprint

        sdir = mcpc.snapshot_dir(root, home, global_mode)
        for snap in world.mcp_snapshots.values():
            if unreviewed_fingerprint(snap) == f.fingerprint:
                mcpc.save_approved(sdir, snap)
    elif f.check_id == "injection-shell-unreviewed":
        from drskill.checks import skill_shell

        skill_shell.save_approved(world, f, root, home, global_mode)


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
    max_calls: str = typer.Option("25", "--max-calls", help="model calls per --deep run: a number, or 'all' for no limit"),
    mcp_connect: bool = typer.Option(False, "--mcp-connect", help="connect to configured MCP servers and enumerate their tools"),
) -> None:
    """Analyze every detected harness's skill set and report findings."""
    _validate_harness(harness)
    home = _home()
    config = _load_effective_config_or_exit(root, home, global_mode)
    judge = None
    rewriter = None
    budget: int | None = None
    if deep_mode:
        if max_calls == "all":
            budget = None
        else:
            try:
                budget = int(max_calls)
                if budget < 0:
                    raise ValueError
            except ValueError:
                console.print(
                    f"[red]--max-calls takes a number or 'all', not[/red] {escape(max_calls)}"
                )
                raise typer.Exit(1)
        from drskill import deep_llm

        deep.load_user_env(home)
        try:
            judge = deep_llm.build_judge(config.deep.model)
            rewriter = deep_llm.build_rewriter(config.deep.model)
        except deep_llm.DeepUnavailableError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
    def _do_scan(progress):
        return run_scan(
            root, home, global_mode, config, harness=harness, judge=judge,
            max_calls=budget, rewriter=rewriter, mcp_connect=mcp_connect,
            progress=progress,
        )

    try:
        # A live one-line spinner naming the current step. It matters most
        # on the slow paths (connecting to servers, a model call per pair)
        # and on large loadouts, and clears before the report. Silent for
        # --json so machine output is never touched.
        if not as_json:
            with console.status("[bold]starting[/bold]", spinner="dots") as status:
                world, findings = _do_scan(
                    lambda m: status.update(f"[bold]{escape(m)}[/bold]")
                )
        else:
            world, findings = _do_scan(None)
    except mcp_connect_mod.ConnectUnavailableError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1)
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
            last_error = getattr(judge, "last_error", None) or getattr(
                rewriter, "last_error", None
            )
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
            pending = deep.pending_rewrites(world, active, cache)
            if pending:
                plural = "s" if pending != 1 else ""
                console.print(
                    f"deep: {pending} rewrite proposal{plural} pending; "
                    "rerun --deep to generate"
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
def lint(
    path: Path = typer.Argument(Path("."), help="plugin directory, skill directory or SKILL.md, marketplace directory or marketplace.json, or MCP config file"),
    target_type: str | None = typer.Option(None, "--type", help="override detection: plugin, skill, mcp, or marketplace"),
    as_json: bool = typer.Option(False, "--json", help="emit findings as JSON"),
    fail_on: str = typer.Option("error", "--fail-on", help="lowest severity that fails the build: error or warn"),
    deep_mode: bool = typer.Option(False, "--deep", help="judge flagged pairs with the configured model"),
    max_calls: str = typer.Option("25", "--max-calls", help="model calls per --deep run: a number, or 'all' for no limit"),
    mcp_connect: bool = typer.Option(False, "--mcp-connect", help="connect to configured MCP servers and enumerate their tools"),
) -> None:
    """Check a plugin, skill, or MCP config against its standard and drskill's checks."""
    from drskill import lint as lint_mod

    if fail_on not in ("error", "warn"):
        console.print(f"[red]--fail-on takes error or warn, not[/red] {escape(fail_on)}")
        raise typer.Exit(2)
    if target_type not in (None, "plugin", "skill", "mcp", "marketplace"):
        console.print(f"[red]--type takes plugin, skill, mcp, or marketplace, not[/red] {escape(target_type)}")
        raise typer.Exit(2)
    try:
        target = lint_mod.classify(path, target_type)
    except lint_mod.LintUsageError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(2)
    home = _home()
    config_root = lint_mod.find_config_root(target.path)
    config = _load_effective_config_or_exit(config_root, home, False)
    judge = None
    rewriter = None
    budget: int | None = None
    if deep_mode:
        if max_calls == "all":
            budget = None
        else:
            try:
                budget = int(max_calls)
                if budget < 0:
                    raise ValueError
            except ValueError:
                console.print(
                    f"[red]--max-calls takes a number or 'all', not[/red] {escape(max_calls)}"
                )
                # lint's contract reserves exit 2 for usage errors (unlike
                # scan, which exits 1 here); this fires before any model
                # setup, so no API key is required to reach it.
                raise typer.Exit(2)
        from drskill import deep_llm

        deep.load_user_env(home)
        try:
            judge = deep_llm.build_judge(config.deep.model)
            rewriter = deep_llm.build_rewriter(config.deep.model)
        except deep_llm.DeepUnavailableError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)

    def _do_lint(progress):
        return lint_mod.run_lint(
            target, config, config_root, home, mcp_connect=mcp_connect,
            judge=judge, rewriter=rewriter, max_calls=budget, progress=progress,
        )

    try:
        if not as_json:
            with console.status("[bold]linting[/bold]", spinner="dots") as status:
                world, findings = _do_lint(
                    lambda m: status.update(f"[bold]{escape(m)}[/bold]")
                )
        else:
            world, findings = _do_lint(None)
    except mcp_connect_mod.ConnectUnavailableError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1)
    active, acked = ledger.filter_findings(findings, config)
    if as_json:
        print(report.to_json(active))
    else:
        report.render_lint(world, target, active, acked, console)
    rank = {"note": 0, "warning": 1, "error": 2}
    threshold = 1 if fail_on == "warn" else 2
    if any(rank[f.severity] >= threshold for f in active):
        raise typer.Exit(1)


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
    lint_target: Path | None = typer.Option(
        None, "--lint",
        help="resolve refs against `drskill lint` findings for this target "
             "instead of a scan; the ack lands in the target's drskill.toml",
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
    if lint_target is not None and (force_local or force_global or global_mode):
        # A lint ack must land where `lint` reads its ledger back from —
        # the target's config root — so the destination is not a choice.
        console.print(
            "[red]--lint routes the ack to the linted target's drskill.toml; "
            "it cannot combine with --local, --global-ack, or --global[/red]"
        )
        raise typer.Exit(1)
    home = _home()
    lint_config_root: Path | None = None
    if lint_target is not None:
        from drskill import lint as lint_mod

        try:
            target = lint_mod.classify(lint_target)
        except lint_mod.LintUsageError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
        lint_config_root = lint_mod.find_config_root(target.path)
        config = _load_effective_config_or_exit(lint_config_root, home, False)
        world, findings = _scan_with_status(
            lambda p: lint_mod.run_lint(
                target, config, lint_config_root, home, progress=p
            )
        )
    else:
        config = _load_effective_config_or_exit(root, home, global_mode)
        world, findings = _scan_with_status(
            lambda p: run_scan(root, home, global_mode, config, progress=p)
        )
    active, _ = ledger.filter_findings(findings, config)
    # Most notes must not be acked: a deep "judged distinct" note shares a
    # fingerprint with the warning it would revert to if the verdict cache
    # is pruned, so acking it would silently pre-silence that warning. An
    # MCP tool baseline or a skill's shell-command baseline is the
    # exception: acking it is the whole point, and a later change produces
    # a new fingerprint the ack cannot cover.
    _ACKABLE_NOTE_CHECKS = {"mcp-tools-unreviewed", "injection-shell-unreviewed"}
    active = [
        f for f in active
        if f.severity != "note" or f.check_id in _ACKABLE_NOTE_CHECKS
    ]
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
            # If multiple exact matches exist (e.g., multiple categories for same skill),
            # ack them all. Only error if we need superset matching and get ambiguous results.
            if exact:
                matches = exact
            elif len(superset) > 1:
                console.print(f"[red]Ambiguous:[/red] {len(superset)} findings match; name all involved skills")
                raise typer.Exit(1)
            else:
                matches = superset
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
        if lint_config_root is not None:
            # The one ledger run_lint reads back is the target's config
            # root, so a lint ack always lands there.
            dest = ledger.ledger_path(lint_config_root, home, False)
        else:
            dest = ledger.ack_destination(
                world, f, root, home, global_mode,
                force_local=force_local, force_global=force_global,
            )
        ledger.append_ack(
            dest,
            Ack(check=f.check_id, skills=sorted(f.contributor_names),
                fingerprint=f.fingerprint, note=note, date=dt.date.today()),
        )
        _save_approved_baseline(
            world, f, lint_config_root or root, home,
            False if lint_config_root is not None else global_mode,
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
    world, findings = _scan_with_status(
        lambda p: run_scan(root, home, global_mode, config, harness=harness, progress=p)
    )
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
    world, findings = _scan_with_status(
        lambda p: run_scan(root, home, global_mode, config, harness=harness, progress=p)
    )
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
                _save_approved_baseline(world, f, root, home, global_mode)
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
    mcp: bool = typer.Option(False, "--mcp", help="list MCP servers instead of skills"),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(False, "--global"),
) -> None:
    """Show each harness's effective skill set."""
    _validate_harness(harness)
    home = _home()
    config = _load_effective_config_or_exit(root, home, global_mode)
    world, _findings = _scan_with_status(
        lambda p: run_scan(root, home, global_mode, config, harness=harness, progress=p)
    )
    _warn_if_undetected(harness, root, home, global_mode)
    from drskill import suites

    # Suite lookup is only shown here, so it only runs here (not on scan/show).
    suites.assign_suites(world, home)
    if mcp:
        from rich.table import Table

        table = Table(title="MCP servers")
        for col in ("harness", "server", "transport", "scope", "source"):
            table.add_column(col)
        for s in sorted(world.mcp_servers, key=lambda s: (s.harness, s.name, s.scope)):
            table.add_row(
                escape(s.harness), escape(s.name), escape(s.transport),
                escape(s.scope), escape(s.source),
            )
        if world.mcp_servers:
            console.print(table)
        else:
            console.print("No MCP servers configured for the detected harnesses.")
        return
    report.render_harness_tables(
        world, console, tokens=tokens, harness=harness, show_all=show_all
    )


@app.command()
def audit(
    name: str | None = typer.Argument(
        None, help="skill or MCP tool to drill into (server:tool to disambiguate)"
    ),
    root: Path = typer.Option(Path("."), "--root", hidden=True),
    global_mode: bool = typer.Option(
        False, "--global", help="all traces on this machine, not just this project"
    ),
    harness: str | None = typer.Option(None, "--harness", help="one harness only"),
    since: str | None = typer.Option(
        None, "--since", help="window: 7d, 30d, or YYYY-MM-DD"
    ),
    file: Path | None = typer.Option(
        None, "--file", help="audit one trace file (see also --harness)"
    ),
    last: bool = typer.Option(
        False, "--last", help="only the most recent session in scope"
    ),
    json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
) -> None:
    """Report how skills and MCP tools actually get used, from local agent traces."""
    import json as json_mod

    from drskill.traces import pipeline as tpipeline
    from drskill.traces import report as treport
    from drskill.traces.common import parse_since

    home = _home()
    if harness is not None and harness not in tpipeline.ADAPTERS:
        valid = ", ".join(sorted(tpipeline.ADAPTERS))
        console.print(
            f"[red]error:[/red] unknown harness {escape(harness)}; "
            f"valid ids: {valid}"
        )
        raise typer.Exit(1)
    cutoff = None
    if since is not None:
        try:
            cutoff = parse_since(since, dt.datetime.now(dt.timezone.utc))
        except ValueError:
            console.print("[red]error:[/red] invalid --since (use 7d, 30d, or YYYY-MM-DD)")
            raise typer.Exit(1)
    if file is not None and last:
        console.print("[red]error:[/red] --file and --last cannot be combined")
        raise typer.Exit(1)
    if file is not None:
        if not file.is_file():
            console.print(
                f"[red]error:[/red] no such trace file: {escape(str(file))}"
            )
            raise typer.Exit(1)
        try:
            data = tpipeline.run_audit_file(home, file, harness, cutoff)
        except tpipeline.UnknownTraceLocation:
            valid = ", ".join(sorted(tpipeline.ADAPTERS))
            console.print(
                f"[red]error:[/red] {escape(str(file))} is outside every "
                f"known trace location; pass --harness to pick the parser "
                f"(valid ids: {valid})"
            )
            raise typer.Exit(1)
        except Exception as exc:
            console.print(
                f"[red]error:[/red] could not read {escape(str(file))}: "
                f"{escape(str(exc))}"
            )
            raise typer.Exit(1)
    else:
        data = tpipeline.run_audit(
            home, root, global_mode, harness, cutoff, last=last
        )
    if name is not None and not json_out:
        treport.render_drilldown(console, name, data)
        return
    if json_out:
        records = data.invocations
        if name is not None:
            records = [i for i in records if treport.matches(i, name)]
        payload = {
            "invocations": [i.model_dump(mode="json") for i in records],
            "coverage": {
                h: c.model_dump(mode="json")
                for h, c in treport.coverage(records).items()
            },
            "unreadable": data.unreadable,
            "drifted": data.drifted,
        }
        print(json_mod.dumps(payload, indent=2))
        return
    treport.render_audit(console, data)


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
        if entries:
            for name, count in sorted(Counter(v.verdict for v in entries.values()).items()):
                console.print(f"  {escape(name)}: {count}")
            for name, count in sorted(Counter(v.model for v in entries.values()).items()):
                console.print(f"  {escape(name)}: {count}")
            dates = sorted(v.date for v in entries.values())
            console.print(f"  oldest {escape(dates[0])}, newest {escape(dates[-1])}")
        sdir = mcp_connect_mod.snapshot_dir(root, home, global_mode)
        snaps = mcp_connect_mod.load_snapshots(sdir)
        approved = mcp_connect_mod.load_snapshots(mcp_connect_mod.approved_dir(sdir))
        if snaps or approved:
            console.print(
                f"{len(snaps)} tool snapshot{'s' if len(snaps) != 1 else ''}, "
                f"{len(approved)} approved baseline"
                f"{'s' if len(approved) != 1 else ''} in {escape(str(sdir))}"
            )
        from drskill.checks import skill_shell

        bdir = skill_shell.shell_dir(root, home, global_mode)
        baselines = skill_shell.load_baselines(bdir)
        if baselines:
            console.print(
                f"{len(baselines)} shell-command baseline"
                f"{'s' if len(baselines) != 1 else ''} in {escape(str(bdir))}"
            )
        from drskill.traces import cache as tcache

        adir = tcache.audit_cache_dir(home)
        audit_entries = list(adir.glob("*.json")) if adir.is_dir() else []
        if audit_entries:
            console.print(
                f"{len(audit_entries)} audit extraction"
                f"{'s' if len(audit_entries) != 1 else ''} in {escape(str(adir))}"
            )
    elif action == "prune":
        config = _load_effective_config_or_exit(root, home, global_mode)
        world, findings = _scan_with_status(
            lambda p: run_scan(root, home, global_mode, config, progress=p)
        )
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
        from drskill import mcp_connect as mcpc

        sdir = mcpc.snapshot_dir(root, home, global_mode)
        live_cfgs = {s.config_hash for s in world.mcp_servers}
        snap_removed = snap_kept = 0
        for p in sorted(sdir.glob("*.json")) if sdir.is_dir() else []:
            if p.stem in live_cfgs:
                snap_kept += 1
            else:
                p.unlink()
                snap_removed += 1
        adir = mcpc.approved_dir(sdir)
        for p in sorted(adir.glob("*.json")) if adir.is_dir() else []:
            if p.stem in live_cfgs:
                snap_kept += 1
            else:
                p.unlink()
                snap_removed += 1
        if snap_removed or snap_kept:
            console.print(
                f"removed {snap_removed} stale tool snapshots, kept {snap_kept}"
            )
        from drskill.checks import skill_shell

        bdir = skill_shell.shell_dir(root, home, global_mode)
        valid_keys = {
            skill_shell.baseline_key(c, root, home)
            for c in world.contributors.values()
            if c.kind == "skill"
        }
        loaded = skill_shell.load_baselines(bdir)
        b_removed = b_kept = 0
        # Walk the files, not the parsed entries, so corrupt files go too.
        for p in sorted(bdir.glob("*.json")) if bdir.is_dir() else []:
            if p.stem in valid_keys and p.stem in loaded:
                b_kept += 1
            else:
                p.unlink()
                b_removed += 1
        if b_removed or b_kept:
            console.print(
                f"removed {b_removed} stale shell-command baseline"
                f"{'s' if b_removed != 1 else ''}, kept {b_kept}"
            )
        from drskill.traces import cache as tcache

        adir = tcache.audit_cache_dir(home)
        a_removed = a_kept = 0
        for p in sorted(adir.glob("*.json")) if adir.is_dir() else []:
            try:
                entry = tcache.TraceCacheEntry.model_validate_json(p.read_text())
                alive = Path(entry.trace_path).exists()
            except (OSError, ValueError):
                alive = False  # corrupt entries go, same rule as verdicts
            if alive:
                a_kept += 1
            else:
                p.unlink()
                a_removed += 1
        if a_removed or a_kept:
            console.print(
                f"removed {a_removed} stale audit extraction"
                f"{'s' if a_removed != 1 else ''}, kept {a_kept}"
            )
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


@app.command()
def login() -> None:
    """Sign in to the drskill service via your browser."""
    base = service.service_url()

    def paste_flow() -> tuple[str, str]:
        typer.echo(f"Create a token at {base}/settings/api_tokens, then paste it below.")
        token = typer.prompt("API token", hide_input=True).strip()
        try:
            identity = service.api_request("GET", "/api/v1/identity", token=token)
        except service.ServiceError as verify_err:
            typer.echo(f"Token rejected: {verify_err.message}")
            raise typer.Exit(1)
        return token, identity["user"]["handle"]

    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        # The loopback flow cannot work over SSH: the approve redirect goes to
        # 127.0.0.1 on the machine running the browser, not this one.
        typer.echo("SSH session detected; the browser flow needs a local browser.")
        token, handle = paste_flow()
    else:
        typer.echo(f"Opening your browser to sign in at {base}...")
        try:
            token, handle = service.browser_login()
        except KeyboardInterrupt:
            typer.echo("")
            token, handle = paste_flow()
        except service.ServiceError as err:
            typer.echo(f"Browser sign-in unavailable ({err.message}).")
            token, handle = paste_flow()
    service.save_credentials(base, token)
    typer.echo(f"✓ Signed in as {handle}")


@app.command()
def whoami() -> None:
    """Show the signed-in drskill service account."""
    creds, base = _service_credentials()
    try:
        identity = service.api_request("GET", "/api/v1/identity", token=creds["token"], base_url=base)
    except service.ServiceError as err:
        typer.echo(f"Not signed in ({err.message}). Run: drskill login")
        raise typer.Exit(1)
    user = identity["user"]
    token_name = identity.get("token", {}).get("name", "")
    typer.echo(f"{user['handle']} (token: {token_name})")


@app.command()
def logout() -> None:
    """Sign out: revoke the service token and delete local credentials."""
    creds = service.load_credentials()
    if not creds:
        typer.echo("Not signed in.")
        return
    base = creds.get("service_url") or service.service_url()
    try:
        service.api_request("DELETE", "/api/v1/token", token=creds["token"], base_url=base)
        typer.echo("Token revoked on the server.")
    except service.ServiceError as err:
        typer.echo(f"Could not revoke on the server ({err.message}); removing local credentials anyway.")
    service.delete_credentials()
    typer.echo("Signed out.")


@app.command()
def sync() -> None:
    """Sync machine-ledger acknowledgments with the drskill service."""
    import platform as platform_module
    from importlib import metadata

    from drskill import sync as sync_module

    creds, base = _service_credentials()
    try:
        version = metadata.version("drskill-core")
    except metadata.PackageNotFoundError:
        version = "unknown"
    device_info = {
        "name": platform_module.node() or "unknown device",
        "platform": sys.platform,
        "cli_version": version,
    }
    try:
        summary = sync_module.run_sync(creds, base, device_info)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)

    for warning in summary.get("warnings", []):
        typer.echo(warning)

    parts = []
    pushed = []
    if summary["pushed_acks"]:
        pushed.append(f"{summary['pushed_acks']} ack" + ("s" if summary["pushed_acks"] != 1 else ""))
    if summary["pushed_reopens"]:
        pushed.append(f"{summary['pushed_reopens']} reopen" + ("s" if summary["pushed_reopens"] != 1 else ""))
    if pushed:
        parts.append("Pushed " + ", ".join(pushed))
    pulled = []
    if summary["pulled_acks"]:
        pulled.append(f"{summary['pulled_acks']} ack" + ("s" if summary["pulled_acks"] != 1 else ""))
    if summary["pulled_reopens"]:
        pulled.append(f"{summary['pulled_reopens']} reopen" + ("s" if summary["pulled_reopens"] != 1 else ""))
    if pulled:
        parts.append("Pulled " + ", ".join(pulled))
    typer.echo(" · ".join(parts) if parts else "Already up to date.")


@loadout_app.command("list")
def loadout_list(
    as_json: bool = typer.Option(False, "--json", help="emit the raw API response"),
) -> None:
    """List your loadouts on the drskill service."""
    creds, base = _service_credentials()
    try:
        data = service.api_request("GET", "/api/v1/loadouts", token=creds["token"], base_url=base)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    loadouts = data.get("loadouts", [])
    if not loadouts:
        typer.echo("No loadouts yet. Create one with: drskill loadout create <slug> --name <name>")
        return
    from rich.table import Table

    table = Table(title="Your loadouts")
    table.add_column("ref")
    table.add_column("name")
    table.add_column("visibility")
    table.add_column("current rev")
    for loadout in loadouts:
        revision = loadout.get("current_revision")
        rev_text = f"#{revision['number']} {revision['runtime_hash'][:17]}…" if revision else "—"
        table.add_row(
            f"{loadout['owner']}/{loadout['slug']}",
            loadout.get("name") or "",
            loadout.get("visibility") or "",
            rev_text,
        )
    console.print(table)


@loadout_app.command()
def create(
    slug: str = typer.Argument(..., help="URL slug for the new loadout"),
    name: str | None = typer.Option(None, "--name", help="display name (defaults from the slug)"),
    description: str | None = typer.Option(None, "--description", help="optional description"),
    empty: bool = typer.Option(False, "--empty",
        help="create an empty loadout without the interactive picker"),
    harness: str | None = typer.Option(None, "--harness",
        help="interactive picker: only list skills active in this harness"),
    manifest_out: Path | None = typer.Option(None, "--manifest-out",
        help="interactive picker: also save the generated manifest to a file"),
) -> None:
    """Create a private loadout, picking its contents interactively in a terminal."""
    from drskill import loadout_wizard

    interactive = loadout_wizard._stdin_is_tty() and not empty
    if not interactive and (harness is not None or manifest_out is not None):
        typer.echo(
            "--harness and --manifest-out need the interactive picker "
            "(run in a terminal, without --empty)."
        )
        raise typer.Exit(1)
    creds, base = _service_credentials()
    if name is None:
        name = slug.replace("-", " ").title()
    if interactive:
        if harness is not None:
            _validate_harness(harness)
        loadout_wizard.run(slug, name, description, harness, manifest_out,
                           creds, base, _home())
        return
    body: dict = {"loadout": {"slug": slug, "name": name}}
    if description is not None:
        body["loadout"]["description"] = description
    try:
        data = service.api_request(
            "POST", "/api/v1/loadouts", token=creds["token"], json_body=body, base_url=base
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    loadout = data["loadout"]
    ref = f"{loadout['owner']}/{loadout['slug']}"
    typer.echo(f"Created {ref} ({loadout['visibility']})")
    typer.echo(f"  name: {loadout.get('name') or ''}")
    typer.echo("  contents: empty, no revisions yet")
    typer.echo("")
    typer.echo("Publish your first revision with:")
    typer.echo(f"  drskill loadout publish {ref} <manifest.json>")
    typer.echo("Each publish adds a numbered revision that never changes after upload.")


@loadout_app.command("show")
def loadout_show(
    ref: str = typer.Argument(..., help="owner/slug"),
    as_json: bool = typer.Option(False, "--json", help="emit the raw API response"),
) -> None:
    """Show a loadout's metadata and current revision."""
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    try:
        data = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}", token=creds["token"], base_url=base
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    loadout = data["loadout"]
    typer.echo(f"{loadout['owner']}/{loadout['slug']} — {loadout.get('name') or ''}")
    typer.echo(f"  visibility: {loadout.get('visibility')}")
    if loadout.get("description"):
        typer.echo(f"  description: {loadout['description']}")
    revision = loadout.get("current_revision")
    if revision:
        typer.echo(f"  current revision: #{revision['number']} {revision['runtime_hash']}")
    else:
        typer.echo("  current revision: none")
    if loadout.get("published_at"):
        typer.echo(f"  published: {loadout['published_at']}")
    forked = loadout.get("forked_from")
    if forked:
        suffix = f" · revision {forked['revision_number']}" if forked.get("revision_number") else ""
        typer.echo(f"  Forked from {forked['owner']}/{forked['slug']}{suffix}")


@loadout_app.command()
def revisions(
    ref: str = typer.Argument(..., help="owner/slug"),
    as_json: bool = typer.Option(False, "--json", help="emit the raw API response"),
) -> None:
    """List a loadout's revision history."""
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    try:
        data = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}/revisions", token=creds["token"], base_url=base
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    from rich.table import Table

    table = Table(title=f"Revisions of {owner}/{slug}")
    table.add_column("rev")
    table.add_column("runtime hash")
    table.add_column("published")
    table.add_column("reproducible")
    for revision in data.get("revisions", []):
        table.add_row(
            str(revision["number"]),
            revision["runtime_hash"],
            str(revision.get("published_at") or ""),
            "yes" if revision.get("reproducible") else "no",
        )
    console.print(table)


@loadout_app.command()
def publish(
    ref: str = typer.Argument(..., help="owner/slug"),
    manifest_path: Path = typer.Argument(..., help="path to a resolved manifest JSON file"),
    no_verify: bool = typer.Option(False, "--no-verify", help="do not send a client-computed runtime hash"),
) -> None:
    """Publish a manifest file as a new immutable revision."""
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        typer.echo(f"Could not read manifest: {err}")
        raise typer.Exit(1)
    if not isinstance(manifest, dict):
        typer.echo("Manifest must be a JSON object.")
        raise typer.Exit(1)
    body: dict = {"manifest": manifest}
    client_hash = None
    if not no_verify:
        _, client_hash = service.canonical_manifest(manifest)
        body["runtime_hash"] = client_hash
    try:
        data = service.api_request(
            "POST", f"/api/v1/loadouts/{owner}/{slug}/revisions",
            token=creds["token"], json_body=body, base_url=base,
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        if client_hash and err.code == "revision_invalid":
            typer.echo(f"  client runtime_hash: {client_hash}")
        raise typer.Exit(1)
    revision = data["revision"]
    typer.echo(f"Published revision {revision['number']} ({revision['runtime_hash']})")



@loadout_app.command()
def install(
    ref: str = typer.Argument(..., help="owner/slug"),
    revision: str | None = typer.Argument(None, help="revision number or sha256:<hash> (default: current)"),
    harness: str | None = typer.Option(None, "--harness",
        help="install into this harness's own skills directory instead of the shared .agents/skills store"),
    project: bool = typer.Option(False, "--project", help="install into the project store"),
    user: bool = typer.Option(False, "--user", help="install into the user store"),
    yes: bool = typer.Option(False, "--yes", help="skip the confirmation"),
    force: bool = typer.Option(False, "--force", help="replace installed skills whose content differs"),
) -> None:
    """Install a loadout's hosted skills into a skills directory."""
    from drskill import content
    from drskill.harnesses import detect_harnesses

    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    if project and user:
        typer.echo("Pass at most one of --project and --user.")
        raise typer.Exit(1)

    try:
        if revision is None:
            data = service.api_request(
                "GET", f"/api/v1/loadouts/{owner}/{slug}", token=creds["token"], base_url=base)
            current = data["loadout"].get("current_revision")
            if not current:
                typer.echo(f"{owner}/{slug} has no published revision.")
                raise typer.Exit(1)
            revision = str(current["number"])
        document = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}/revisions/{revision}",
            token=creds["token"], base_url=base, raw=True)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)

    from drskill import gh_source

    entries = json.loads(document).get("entries", [])
    hosted = [e for e in entries if e.get("source_type") == "drskill"]
    github = [e for e in entries if e.get("source_type") == "github"]
    other = len(entries) - len(hosted) - len(github)
    installable = len(hosted) + len(github)
    if not installable:
        typer.echo(f"Revision {revision} of {owner}/{slug} has no installable entries.")
        raise typer.Exit(0)

    root = Path.cwd()
    home = _home()
    target, scope = _install_target(harness, project, user, root, home)

    typer.echo(f"Install {installable} skill{'s' if installable != 1 else ''} "
               f"into {target} ({scope} store):")
    for entry in hosted:
        typer.echo(f"  {entry['name']}  ({entry['content_hash'][:19]}…)")
    for entry in github:
        coords = gh_source.coordinates(entry)
        if coords:
            typer.echo(f"  {entry['name']}  ({coords[0]} @ {coords[1]})")
        else:
            typer.echo(f"  {entry['name']}  (source {entry.get('source_reference')!r} is not fetchable)")
    if other:
        typer.echo(f"{other} entr{'ies' if other != 1 else 'y'} with other source types "
                   "will not be installed.")
    if harness is None:
        blind = [h.display_name for h in detect_harnesses(root, home)
                 if ".agents/skills" not in h.project_paths
                 and "~/.agents/skills" not in h.global_paths]
        if blind:
            typer.echo(f"Note: {', '.join(blind)} does not read the shared store; "
                       "pass --harness to target it directly.")
    if not yes and not typer.confirm("Proceed?", default=False):
        raise typer.Exit(0)

    counts = {"installed": 0, "unchanged": 0, "held": 0, "failed": 0}
    for entry in hosted:
        dest = target / entry["name"]
        status = _existing_dir_status(entry["content_hash"], dest, entry["name"], force)
        if status is None:
            try:
                files = content.download(entry["content_hash"], creds["token"], base)
            except service.ServiceError as err:
                _echo_service_error(err)
                raise typer.Exit(1)
            replaced = dest.exists()
            content.write_skill(files, dest)
            typer.echo(f"  {entry['name']}: {'replaced' if replaced else 'installed'}")
            status = "installed"
        counts[status] += 1
    ctx = {"owner": owner, "slug": slug, "manifest": json.loads(document),
           "creds": creds, "base": base, "home": home}
    for entry in github:
        status = _install_one_github(entry, target, force=force, yes=yes, ctx=ctx)
        counts[status] += 1
    parts = [f"{counts['installed']} installed"]
    if counts["unchanged"]:
        parts.append(f"{counts['unchanged']} already installed")
    if counts["held"]:
        parts.append(f"{counts['held']} held (--force to replace)")
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed")
    typer.echo(" · ".join(parts))
    if counts["failed"] and not counts["installed"] and not counts["unchanged"]:
        raise typer.Exit(1)


def _existing_dir_status(expected_hash: str, dest: Path, name: str, force: bool) -> str | None:
    """"unchanged" or "held" for an existing install, None when writing
    should proceed."""
    from drskill import content

    if not dest.exists():
        return None
    if content.manifest_hash(content.read_dir(dest)) == expected_hash:
        typer.echo(f"  {name}: already installed")
        return "unchanged"
    if not force:
        typer.echo(f"  {name}: local copy differs; rerun with --force to replace it")
        return "held"
    return None


def _install_one_github(entry: dict, target: Path, *, force: bool, yes: bool,
                        ctx: dict) -> str:
    from drskill import content, gh_source

    coords = gh_source.coordinates(entry)
    if coords is None:
        typer.echo(f"  {entry['name']}: source {entry.get('source_reference')!r} is not fetchable")
        return "failed"
    repo, ref = coords
    dest = target / entry["name"]
    try:
        tar_bytes = gh_source.fetch_tarball(repo, ref)
        files = gh_source.extract_skill(tar_bytes, entry)
    except gh_source.FetchError as error:
        typer.echo(f"  {entry['name']}: {error}")
        return "failed"
    outcome = gh_source.verify(files, entry)
    if outcome == "mismatch":
        return _remediate(entry, files, dest, ref=ref, force=force, yes=yes, ctx=ctx)
    status = _existing_dir_status(content.manifest_hash(files), dest, entry["name"], force)
    if status is not None:
        return status
    if outcome == "legacy_ok":
        typer.echo(f"  {entry['name']}: bundled files are unverified "
                   "(published before directory hashes)")
    replaced = dest.exists()
    content.write_skill(files, dest)
    typer.echo(f"  {entry['name']}: {'replaced' if replaced else 'installed'}")
    return "installed"


def _remediate(entry: dict, files: list[dict], dest: Path, *, ref: str,
               force: bool, yes: bool, ctx: dict) -> str:
    """Interactive recovery for an upstream drift: review the fetched
    version with the standard checks, then republish (owner) or fork and
    republish (non-owner), then install."""
    from drskill import content

    typer.echo("The remote skill has been updated since this loadout was "
               "created and the original version was not pinned.")
    if yes or interactive.can_interact() is not None:
        typer.echo("Rerun interactively to review and republish.")
        return "failed"

    creds, base, home = ctx["creds"], ctx["base"], ctx["home"]
    owner, slug = ctx["owner"], ctx["slug"]
    try:
        identity = service.api_request("GET", "/api/v1/identity",
                                       token=creds["token"], base_url=base)
        handle = identity["user"]["handle"]
    except service.ServiceError as err:
        _echo_service_error(err)
        return "failed"

    if handle != owner:
        if not typer.confirm(f"Fork {owner}/{slug} to your account and review "
                             "the updated skill?", default=False):
            return "failed"
        forked = _fork_loadout(owner, slug, creds, base)
        if forked is None:
            return "failed"
        owner, slug = forked

    if not _review_fetched(files, home, manifest=ctx["manifest"],
                           selector=entry.get("selector"), name=entry["name"]):
        return "failed"

    published = _publish_updated_entry(entry, files, ref, owner, slug, creds, base,
                                       ctx["manifest"])
    if not published:
        return "failed"

    status = _existing_dir_status(content.manifest_hash(files), dest, entry["name"], force)
    if status is not None:
        return status
    replaced = dest.exists()
    content.write_skill(files, dest)
    typer.echo(f"  {entry['name']}: {'replaced' if replaced else 'installed'}")
    return "installed"


def _fork_loadout(owner: str, slug: str, creds: dict, base: str) -> tuple[str, str] | None:
    body: dict = {}
    while True:
        try:
            data = service.api_request(
                "POST", f"/api/v1/loadouts/{owner}/{slug}/fork",
                token=creds["token"], json_body=body or None, base_url=base)
        except service.ServiceError as err:
            if err.code == "loadout_invalid" and (err.details or {}).get("slug"):
                new_slug = typer.prompt("That slug is taken; choose a slug for your fork").strip()
                if not new_slug:
                    return None
                body = {"loadout": {"slug": new_slug}}
                continue
            _echo_service_error(err)
            return None
        fork = data["loadout"]
        typer.echo(f"Forked to {fork['owner']}/{fork['slug']}.")
        return fork["owner"], fork["slug"]


def _review_fetched(files: list[dict], home: Path, manifest: dict | None = None,
                    selector: str | None = None, name: str = "skill") -> bool:
    """Write the fetched skill to a temp directory, run the standard checks,
    and offer acks. False when the user quits the review. When a manifest
    with a health_report is given, that entry's findings are refreshed in
    place from this run."""
    import tempfile

    from drskill import content, lint as lint_mod

    with tempfile.TemporaryDirectory(prefix="drskill-review-") as tmp:
        skill_dir = Path(tmp) / name
        content.write_skill(files, skill_dir)
        target = lint_mod.classify(skill_dir, "skill")
        config = _load_effective_config_or_exit(Path(tmp), home, False)
        world, findings = lint_mod.run_lint(target, config, Path(tmp), home)
        active, acked = ledger.filter_findings(findings, config)
        active = [f for f in active if f.severity != "note"]
        if manifest is not None and selector is not None:
            _refresh_health_report(manifest, selector, active)
        if not active:
            typer.echo("Review: no findings.")
            return True
        report.print_findings(world, active, console)
        machine_ledger = home / ".drskill.toml"
        for finding in active:
            console.print("[bold]a[/bold] ack · [bold]s[/bold] skip · [bold]q[/bold] quit review")
            while True:
                key = key_source()
                if key == "a":
                    ledger.append_ack(machine_ledger, ledger.Ack(
                        check=finding.check_id, skills=finding.contributor_names,
                        fingerprint=finding.fingerprint, date=dt.date.today()))
                    typer.echo(f"  acked {finding.check_id}")
                    break
                if key == "s":
                    break
                if key == "q":
                    return False
        return True


def _refresh_health_report(manifest: dict, selector: str, findings) -> None:
    """Replace the selector's findings in an existing health_report with
    this review run's results and recompute the summary."""
    health_report = manifest.get("health_report")
    if not isinstance(health_report, dict) or not isinstance(health_report.get("findings"), list):
        return
    kept = [f for f in health_report["findings"]
            if not (isinstance(f, dict) and f.get("entry_selector") == selector)]
    kept += [{
        "id": f.fingerprint, "check_id": f.check_id, "severity": f.severity,
        "entry_selector": selector, "title": f.check_id, "summary": f.message,
    } for f in findings]
    health_report["findings"] = kept
    if isinstance(health_report.get("summary"), dict):
        health_report["summary"] = {
            "errors": sum(1 for f in kept if isinstance(f, dict) and f.get("severity") == "error"),
            "warnings": sum(1 for f in kept if isinstance(f, dict) and f.get("severity") == "warning"),
            "notices": sum(1 for f in kept if isinstance(f, dict) and f.get("severity") not in ("error", "warning")),
        }


def _publish_updated_entry(entry: dict, files: list[dict], ref: str,
                           owner: str, slug: str, creds: dict, base: str,
                           current_manifest: dict) -> bool:
    import copy

    from drskill import content

    manifest = copy.deepcopy(current_manifest)
    for candidate in manifest.get("entries", []):
        if candidate.get("selector") == entry.get("selector"):
            metadata = candidate.setdefault("metadata", {})
            metadata["directory_hash"] = content.manifest_hash(files)
            metadata["ref"] = ref
            break
    if not typer.confirm(f"Publish a new revision of {owner}/{slug} with the "
                         "updated skill and install it?", default=False):
        return False
    _, runtime_hash = service.canonical_manifest(manifest)
    try:
        data = service.api_request(
            "POST", f"/api/v1/loadouts/{owner}/{slug}/revisions",
            token=creds["token"], base_url=base,
            json_body={"manifest": manifest, "runtime_hash": runtime_hash})
    except service.ServiceError as err:
        _echo_service_error(err)
        return False
    revision = data["revision"]
    typer.echo(f"Published revision {revision['number']} ({revision['runtime_hash']}).")
    return True


def _install_target(harness_id: str | None, project: bool, user: bool,
                    root: Path, home: Path) -> tuple[Path, str]:
    from drskill.harnesses import load_harnesses

    in_project = project or (not user and ((root / ".git").exists() or (root / ".agents").exists()))
    scope = "project" if in_project else "user"
    if harness_id is None:
        base = (root if in_project else home) / ".agents" / "skills"
        return base, scope
    hd = next((h for h in load_harnesses() if h.id == harness_id), None)
    if hd is None:
        typer.echo(f"Unknown harness {harness_id!r}. Known: "
                   + ", ".join(h.id for h in load_harnesses()))
        raise typer.Exit(1)
    specs = hd.project_paths if in_project else hd.global_paths
    if not specs:
        typer.echo(f"{hd.display_name} has no {scope} skills directory.")
        raise typer.Exit(1)
    spec = specs[0]
    base = root / spec if in_project else home / spec.removeprefix("~/")
    return base, scope

@loadout_app.command()
def fetch(
    target: str = typer.Argument(..., help="owner/slug, or a bare sha256:<hash>"),
    revision: str | None = typer.Argument(None, help="revision number or sha256:<hash> (with owner/slug)"),
    output: Path = typer.Option(None, "-o", "--output", help="write the document to a file"),
) -> None:
    """Fetch a revision's canonical manifest, byte-stable."""
    creds, base = _service_credentials()
    if target.startswith("sha256:"):
        path = f"/api/v1/revision_hashes/{target}"
    else:
        owner, slug = _parse_ref(target)
        if not revision:
            typer.echo("Provide a revision number or sha256:<hash> after owner/slug.", err=True)
            raise typer.Exit(1)
        path = f"/api/v1/loadouts/{owner}/{slug}/revisions/{revision}"
    try:
        document = service.api_request("GET", path, token=creds["token"], base_url=base, raw=True)
    except service.ServiceError as err:
        typer.echo(err.message, err=True)
        for field, messages in (err.details or {}).items():
            for message in messages if isinstance(messages, list) else [messages]:
                typer.echo(f"  {field}: {message}", err=True)
        raise typer.Exit(1)
    if output:
        try:
            output.write_bytes(document.encode())
        except OSError as err:
            typer.echo(f"Could not write {output}: {err}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(document)
