# Detailed Scan, Harness Scoping, and Linked Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scan --detailed`/`--all`/`--harness`, hide empty harnesses by default, and classify store-symlinked skills as `linked` provenance.

**Architecture:** Four small changes to the existing pipeline: a new `Provenance.kind` literal assigned during resolution; the `list` table renderer extracted into `report.render_harness_tables` and shared with `scan --detailed`; `pipeline.run_scan` gains a `harness` scoping parameter; `report.render`'s header splits populated vs empty harness counts.

**Tech Stack:** Existing v0.1 stack (typer, rich, pydantic, pytest via uv). No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-20-detailed-scan-and-linked-provenance-design.md` — exact behaviors there govern.
- Empty = `world.effective(hid)` is empty. Detection does not change; hiding is display-only. Empty harnesses still appear in `--json` and `World.harnesses`.
- `--json` wins over `--detailed`; `--all` without `--detailed` on scan is accepted and does nothing.
- Unknown `--harness` id → exit 1 naming valid ids (both commands). Valid-but-undetected id on scan → runs anyway, prints a not-detected note (suppressed under `--json`), exit per normal rules.
- `linked` requires: not already `skills-lock`/`gh-skill`, and realpath under a directory `.agents/skills`. A lockfile entry still wins as `skills-lock`.
- All dynamic text rendered through rich must go through `rich.markup.escape` (existing convention).
- Stage only named files when committing; never `git add -A` (untracked `initial_design_doc.md` must stay untracked).

---

### Task 1: `linked` provenance

**Files:**
- Modify: `src/drskill/models.py` (Provenance.kind literal)
- Modify: `src/drskill/resolution.py` (classification in `build_world`)
- Modify: `src/drskill/pipeline.py` (lockfile upgrade also applies to `linked`)
- Test: `tests/test_resolution.py` (append)

**Interfaces:**
- Consumes: existing `build_world`, `Provenance`.
- Produces: `Provenance.kind` may be `"linked"`; helper `resolution._in_agents_store(path: Path) -> bool`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_resolution.py`:

```python
def test_linked_provenance_for_store_symlink(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    canonical = write_skill(proj / ".agents" / "skills", "store-skill")
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(canonical, d / "store-skill")
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "linked"


def test_linked_provenance_for_direct_store_residence(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".agents" / "skills", "store-skill")
    (proj / ".pi").mkdir()
    world = world_for("pi", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "linked"


def test_plain_directory_stays_unmanaged(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".claude" / "skills", "hand-dropped")
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "unmanaged"


def test_gh_provenance_beats_linked(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".agents" / "skills", "managed",
                extra_fm="source: octo/repo\nref: main\ntree_sha: abc\n")
    (proj / ".pi").mkdir()
    world = world_for("pi", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "gh-skill"
```

Also add a lockfile-wins test to `tests/test_checks_lockfile.py`:

```python
def test_lockfile_entry_beats_linked(tmp_path):
    import json as _json
    proj, home = tmp_path / "p", tmp_path / "h"
    home.mkdir()
    d = proj / ".agents" / "skills" / "pinned"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: pinned\ndescription: d\n---\nbody\n")
    (proj / ".pi").mkdir()
    (proj / "skills-lock.json").write_text(
        _json.dumps({"skills": {"pinned": {"hash": compute_tree_hash(d)}}})
    )
    world, _ = run_scan(proj, home)
    c = next(c for c in world.contributors.values() if c.name == "pinned")
    assert c.source.kind == "skills-lock"
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_resolution.py tests/test_checks_lockfile.py -v`
Expected: the four new resolution tests fail (`unmanaged != linked` etc.); lockfile test may fail on kind.

- [ ] **Step 3: Implement**

`src/drskill/models.py`:

```python
class Provenance(BaseModel):
    kind: Literal["skills-lock", "gh-skill", "linked", "unmanaged"] = "unmanaged"
```

`src/drskill/resolution.py` — add helper and use it in `build_world` where provenance is assigned:

```python
def _in_agents_store(path: Path) -> bool:
    """Layout heuristic: the realpath lives under a `.agents/skills` canonical
    store, which is how installers like `npx skills` materialize skills. It is
    evidence of installer management, not a claim about which installer."""
    return any(
        p.name == "skills" and p.parent.name == ".agents" for p in [path, *path.parents]
    )
```

In `build_world`, replace the provenance block:

```python
            provenance = Provenance()
            if fm and GH_PROVENANCE_KEYS & fm.keys():
                provenance = Provenance(kind="gh-skill", source=fm.get("source"))
            elif _in_agents_store(real):
                provenance = Provenance(kind="linked")
```

`src/drskill/pipeline.py` — the lockfile upgrade condition becomes:

```python
            if c.source.kind in ("unmanaged", "linked") and c.name in world.lockfile:
```

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_resolution.py tests/test_checks_lockfile.py -v` then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/models.py src/drskill/resolution.py src/drskill/pipeline.py tests/test_resolution.py tests/test_checks_lockfile.py
git commit -m "feat: classify agents-store skills as linked provenance"
```

---

### Task 2: Shared harness-table renderer, empty hiding, list --all and id validation

**Files:**
- Modify: `src/drskill/report.py` (add `render_harness_tables`)
- Modify: `src/drskill/cli.py` (`list_cmd` delegates; add `--all`; validate `--harness`)
- Test: `tests/test_report.py`, `tests/test_cli_commands.py` (append)

**Interfaces:**
- Consumes: `World.harness_loads`, `World.effective`, existing escape conventions.
- Produces: `report.render_harness_tables(world, console, *, tokens: bool = False, harness: str | None = None, show_all: bool = False) -> None` and `cli._validate_harness(harness: str | None) -> None`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_report.py`:

```python
from drskill.report import render_harness_tables


def world_two_harnesses():
    c = make_contributor(id="/a", name="alpha")
    c.deployments.append(
        __import__("drskill.models", fromlist=["Deployment"]).Deployment(
            harness="claude-code", path="/a", scope="project",
            via_symlink=False, order=0,
        )
    )
    return World(
        contributors={"/a": c},
        harnesses={
            "claude-code": HarnessDef(id="claude-code", display_name="Claude Code", verified=True),
            "qwen-code": HarnessDef(id="qwen-code", display_name="Qwen Code", verified=False),
        },
    )


def tables_to_text(world, **kwargs):
    console = Console(record=True, width=120, force_terminal=False)
    render_harness_tables(world, console, **kwargs)
    return console.export_text()


def test_empty_harness_hidden_by_default():
    text = tables_to_text(world_two_harnesses())
    assert "Claude Code" in text and "alpha" in text
    assert "Qwen Code" not in text
    assert "1 more harness detected with no skills (qwen-code); show with --all" in text


def test_show_all_includes_empty():
    text = tables_to_text(world_two_harnesses(), show_all=True)
    assert "Qwen Code" in text
    assert "show with --all" not in text


def test_harness_filter_suppresses_closing_line():
    text = tables_to_text(world_two_harnesses(), harness="claude-code")
    assert "Claude Code" in text and "show with --all" not in text
```

Append to `tests/test_cli_commands.py`:

```python
def test_list_unknown_harness_errors(tmp_path):
    r = invoke(tmp_path, "list", "--harness", "bogus")
    assert r.exit_code == 1
    assert "unknown harness" in r.output and "claude-code" in r.output


def test_list_all_shows_empty_harnesses(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "alpha", "First.", "body")
    (proj / ".pi").mkdir()  # detected, but loads the same .agents-less set → empty
    r = invoke(tmp_path, "list")
    assert "Pi" not in r.output
    r_all = invoke(tmp_path, "list", "--all")
    assert "Pi" in r_all.output
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_report.py tests/test_cli_commands.py -v`
Expected: import error on `render_harness_tables`, then assertion failures.

- [ ] **Step 3: Implement**

Move the table loop from `cli.list_cmd` into `src/drskill/report.py` (imports: `shlex` already there; add `from rich.table import Table`):

```python
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
```

In `src/drskill/cli.py` add the validator and rewrite `list_cmd`:

```python
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
```

Remove the now-unused `Table` import from `cli.py` if nothing else uses it.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_report.py tests/test_cli_commands.py -v` then `uv run pytest -q`
Expected: all pass, including the pre-existing `test_list_tokens` and markup-escape tests (they exercise the moved code).

- [ ] **Step 5: Commit**

```bash
git add src/drskill/report.py src/drskill/cli.py tests/test_report.py tests/test_cli_commands.py
git commit -m "feat: shared harness table renderer with empty-harness hiding and --all"
```

---

### Task 3: scan --detailed, --all, --harness scoping, split header

**Files:**
- Modify: `src/drskill/pipeline.py` (`run_scan` harness param)
- Modify: `src/drskill/report.py` (`render` split header)
- Modify: `src/drskill/cli.py` (`scan` flags)
- Test: `tests/test_cli_scan.py`, `tests/test_report.py` (append)

**Interfaces:**
- Consumes: `render_harness_tables`, `_validate_harness` (Task 2).
- Produces: `run_scan(project_root, home, global_only=False, config=None, harness=None)`; `scan` options `--detailed`, `--all`, `--harness`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_report.py`:

```python
def test_header_splits_empty_harness_count():
    text = render_to_text(world_two_harnesses(), [], [])
    assert "1 harness (1 more empty), 1 skills" in text


def test_header_plain_when_no_empty():
    text = render_to_text(world_with(), [], [])
    assert "more empty" not in text
```

Append to `tests/test_cli_scan.py`:

```python
def test_scan_detailed_appends_tables(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path, "--detailed")
    assert r.exit_code == 0
    assert "No findings" in r.output and "clean" in r.output and "Claude Code" in r.output


def test_scan_json_wins_over_detailed(tmp_path):
    import json
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path, "--detailed", "--json")
    json.loads(r.output)  # pure JSON, no tables


def test_scan_unknown_harness_errors(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    r = scan(tmp_path, "--harness", "bogus")
    assert r.exit_code == 1 and "unknown harness" in r.output


def test_scan_scoped_harness_drops_cross_harness_findings(tmp_path):
    proj = tmp_path / "proj"
    content = "---\nname: same\ndescription: d\n---\nbody\n"
    for rel in [".claude/skills/same", ".pi/skills/same"]:
        d = proj / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content)
    full = scan(tmp_path)
    assert "exact-duplicate" in full.output
    scoped = scan(tmp_path, "--harness", "pi")
    assert "exact-duplicate" not in scoped.output
    assert scoped.exit_code == 0


def test_scan_valid_but_undetected_harness_notes_and_passes(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path, "--harness", "qwen-code")
    assert r.exit_code == 0
    assert "not detected" in r.output
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_cli_scan.py tests/test_report.py -v`
Expected: header tests fail on old wording; scan tests fail on unknown options.

- [ ] **Step 3: Implement**

`src/drskill/pipeline.py` — scoping (import `load_harnesses` alongside `detect_harnesses`):

```python
def run_scan(
    project_root: Path,
    home: Path,
    global_only: bool = False,
    config: Config | None = None,
    harness: str | None = None,
) -> tuple[World, list[Finding]]:
    if config is None:
        config = load_config(ledger_path(project_root, home, global_only))
    if harness is None:
        harnesses = detect_harnesses(project_root, home, global_only)
    else:
        harnesses = [h for h in load_harnesses() if h.id == harness]
```
(rest unchanged)

`src/drskill/report.py` — header in `render`:

```python
    populated = [hid for hid in world.harnesses if world.effective(hid)]
    empty = len(world.harnesses) - len(populated)
    n_skills = len(world.contributors)
    plural = "es" if len(populated) != 1 else ""
    header = f"[bold]drskill scan[/bold] — {len(populated)} harness{plural}"
    if empty:
        header += f" ({empty} more empty)"
    header += f", {n_skills} skills"
    console.print(header)
```

`src/drskill/cli.py` — `scan` gains options and post-report tables:

```python
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
        if harness is not None:
            from drskill.harnesses import detect_harnesses

            detected = {h.id for h in detect_harnesses(root, home, global_mode)}
            if harness not in detected:
                console.print(
                    f"[dim]note: harness {escape(harness)} is not detected on this "
                    "machine; scanning its search paths anyway[/dim]"
                )
        report.render(world, active, acked, console)
        if detailed:
            console.print()
            report.render_harness_tables(
                world, console, tokens=False, harness=None, show_all=show_all
            )
    if any(f.severity == "error" for f in active):
        raise typer.Exit(1)
    if ci and any(f.severity == "warning" for f in active):
        raise typer.Exit(2)
```

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_cli_scan.py tests/test_report.py -v` then `uv run pytest -q`
Expected: all pass. Note `test_render_sections_and_ack_line` and smoke tests must still pass with the new header (they assert findings text, not the header).

- [ ] **Step 5: Commit**

```bash
git add src/drskill/pipeline.py src/drskill/report.py src/drskill/cli.py tests/test_cli_scan.py tests/test_report.py
git commit -m "feat: scan --detailed/--all/--harness with split harness counts"
```

---

### Task 4: README and final sweep

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README** (plain style, no dashes): in Quick start, add after the `list --tokens` block: a `drskill scan --detailed` example ("Scan and also print each harness's skill table") and a `drskill scan --harness pi` example ("Scope the scan to a single harness, exactly what that harness sees; unknown ids error, and harnesses with no skills are hidden from tables unless you pass --all"). In the ledger/known-limitations area, adjust the sentence about provenance if present; add one line: "Skills that live in or link into a `.agents/skills` store show the source `linked`. The label means an installer arranged the layout; drskill does not guess which one."

- [ ] **Step 2: Full suite + real-machine spot check**

Run: `uv run pytest -q` (expect all green), then `uv run drskill scan --detailed` and `uv run drskill list` from the repo root; confirm empty harnesses collapse to the closing line and your symlinked skills show `linked`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README for --detailed, --harness scoping, and linked provenance"
```
