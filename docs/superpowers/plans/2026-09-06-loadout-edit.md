# Loadout Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill loadout edit owner/slug` — an interactive picker over an existing loadout's entries plus the local scan, publishing a new revision with additions and removals.

**Architecture:** A pure item-builder pairs the fetched manifest's entries with local wizard rows by selector; a new `run_edit` in `loadout_wizard.py` reuses the create wizard's privates (`_build_rows`, `_offer_registry`, `_mcp_server_entries`, `_print_summary`, `_publish`) and `update`'s fetch/ownership scaffolding. Kept entries pass through byte-identical (refreshing content stays `loadout update`'s job); additions go through the same pipelines as create.

**Tech Stack:** Python 3.11+, typer, questionary, pytest.

**Spec:** The 2026-08-31 loadout-wizard spec listed interactive editing as out of scope; this plan closes it. Decisions:
- Edit changes membership only. A kept entry is republished exactly as fetched — no hash refresh, no re-review (that is `update`).
- Entries with no local counterpart ("phantoms": hosted/github skills not installed here, MCP servers not configured here) are listed pre-checked with a `(published; not on this machine)` label; unchecking removes them.
- Local rows that match a published selector are pre-checked; all other rows start unchecked (they are candidate additions).
- Additions whose selector collides with a kept entry are skipped with a note.
- `harness_mappings` carries over from the fetched manifest unchanged.
- No changes selected → "No changes." and no publish. Only the owner can edit (mirror `update`'s refusal).

## Global Constraints

- Work only in the worktree; every git/test command `cd`s into it in the same compound command. Never run git in the main checkout.
- Full suite before each commit: `uv run --extra connect --extra deep pytest`.
- Transcribe docstrings and comments verbatim. Commit per task, conventional prefix, ending with:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

## Code facts

- `loadout_wizard.py`: `_Row` dataclass (contributor/harnesses/scope/selected), `_build_rows(world)` (skips system and command kinds), `_row_label(row, chosen_harness, width)`, `_label_width(rows)`, `_offer_registry(skills, creds, base_url, home)`, `_mcp_server_entries(mcp_tools, world)`, `_print_summary(manifest, notes)`, `_publish(ref, manifest, manifest_out, creds, base_url)`, `_stdin_is_tty()`.
- `manifest_build._KINDS = {"skill": "skill", "mcp_tool": "mcp"}`, `normalize_name`.
- `update` command (cli.py, search "You can only update your own loadouts") shows the identity/ownership check and the revision fetch sequence.
- `cli.py` `create` command shows the TTY guard and `_validate_harness` usage; `_service_credentials()`, `_home()` helpers.
- Tests: `tests/test_loadout_wizard.py` has `wizard_env` (fake `service.api_request` capturing calls), `set_world`, `make_world`, `contributor(...)`, `_FakeQuestion`. The create-flow tests monkeypatch `loadout_wizard._choose_skills` and invoke `runner.invoke(app, ["loadout", "create", ...])`.

---

### Task 1: Edit items

**Files:**
- Modify: `src/drskill/loadout_wizard.py`
- Test: `tests/test_loadout_wizard.py`

**Interfaces:**
- Produces:

```python
@dataclass
class _EditItem:
    label: str
    checked: bool
    row: _Row | None      # local contributor row when one exists
    entry: dict | None    # published entry when one exists (match or phantom)

def _edit_items(rows: list[_Row], entries: list[dict],
                chosen_harness: str | None) -> list[_EditItem]
def _row_selector(row: _Row) -> str
```

Semantics: `_row_selector` is `f"{manifest_build._KINDS[row.contributor.kind]}:{manifest_build.normalize_name(row.contributor.name)}"`. `_edit_items` indexes entries by selector, walks rows in order (labels via `_row_label(row, chosen_harness, _label_width(rows))`), pairing each row with its entry when the selector matches (checked=True) else leaving it a candidate (checked=False); leftover entries append in manifest order as phantoms with label `f"{entry['name']}  (published; not on this machine)"`, checked=True, row=None. Duplicate selectors among rows: the first row claims the entry; later same-selector rows stay unchecked candidates.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loadout_wizard.py`:

```python
def edit_entry(selector, name=None, **overrides):
    e = {"kind": selector.split(":")[0], "selector": selector,
         "name": name or selector.split(":")[1], "source_type": "github",
         "source_reference": "friend/x@v1", "content_hash": "sha256:" + "ab" * 32,
         "local_only": False, "metadata": {}}
    e.update(overrides)
    return e


def test_edit_items_pairs_rows_with_entries():
    rows = loadout_wizard._build_rows(make_world(contributor("alpha"), contributor("beta")))
    entries = [edit_entry("skill:alpha")]
    items = loadout_wizard._edit_items(rows, entries, None)
    by_name = {(i.row.contributor.name if i.row else i.entry["name"]): i for i in items}
    assert by_name["alpha"].checked is True
    assert by_name["alpha"].entry is entries[0]
    assert by_name["beta"].checked is False
    assert by_name["beta"].entry is None


def test_edit_items_appends_phantoms_prechecked():
    rows = loadout_wizard._build_rows(make_world(contributor("alpha")))
    entries = [edit_entry("skill:alpha"), edit_entry("skill:ghost"), edit_entry("mcp:notion")]
    items = loadout_wizard._edit_items(rows, entries, None)
    phantoms = [i for i in items if i.row is None]
    assert [p.entry["name"] for p in phantoms] == ["ghost", "notion"]
    assert all(p.checked for p in phantoms)
    assert all("not on this machine" in p.label for p in phantoms)
```

- [ ] **Step 2: Verify failure** — `uv run pytest tests/test_loadout_wizard.py -v -k edit_items` → AttributeError.

- [ ] **Step 3: Implement**

Add to `src/drskill/loadout_wizard.py` (near `_Row`; add `from dataclasses import dataclass` only if the file lacks it — it defines `_Row` already, check how):

```python
@dataclass
class _EditItem:
    label: str
    checked: bool
    row: _Row | None
    entry: dict | None


def _row_selector(row: _Row) -> str:
    kind = manifest_build._KINDS[row.contributor.kind]
    return f"{kind}:{manifest_build.normalize_name(row.contributor.name)}"


def _edit_items(rows: list[_Row], entries: list[dict],
                chosen_harness: str | None) -> list[_EditItem]:
    """Pair local rows with published entries by selector. Rows with a
    published match start checked; other rows are candidate additions.
    Entries with no local counterpart become pre-checked phantoms so
    unchecking one removes it from the loadout."""
    by_selector: dict[str, dict] = {}
    for e in entries:
        by_selector.setdefault(e.get("selector") or "", e)
    width = _label_width(rows)
    items: list[_EditItem] = []
    claimed: set[int] = set()
    for row in rows:
        entry = by_selector.get(_row_selector(row))
        if entry is not None and id(entry) not in claimed:
            claimed.add(id(entry))
            items.append(_EditItem(_row_label(row, chosen_harness, width), True, row, entry))
        else:
            items.append(_EditItem(_row_label(row, chosen_harness, width), False, row, None))
    for e in entries:
        if id(e) not in claimed:
            items.append(_EditItem(f"{e['name']}  (published; not on this machine)",
                                   True, None, e))
    return items
```

(`manifest_build` is already imported by the module.)

- [ ] **Step 4: Verify pass**, **Step 5: Full suite, commit**

```bash
git add src/drskill/loadout_wizard.py tests/test_loadout_wizard.py
git commit -m "feat: pair loadout entries with local rows for editing"
```

---

### Task 2: run_edit and the CLI command

**Files:**
- Modify: `src/drskill/loadout_wizard.py`, `src/drskill/cli.py`
- Test: `tests/test_loadout_wizard.py`

**Interfaces:**
- Produces: `loadout_wizard.run_edit(ref: str, harness: str | None, creds: dict, base_url: str, home: Path) -> None`; `loadout_wizard._choose_edit(items: list[_EditItem]) -> list[_EditItem]` (questionary checkbox, monkeypatchable); CLI `drskill loadout edit owner/slug [--harness id]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loadout_wizard.py`. First extend the `wizard_env` fixture's `fake_api_request` (read it) to serve the edit flow when asked: add handlers for `GET /api/v1/identity` → `{"user": {"handle": "drew"}}`, `GET /api/v1/loadouts/drew/pack` → a loadout dict with `current_revision: {"number": 3, ...}`, and `GET /api/v1/loadouts/drew/pack/revisions/3` → `json.dumps(state["edit_manifest"])` (raw=True path), where `state["edit_manifest"]` is settable per test with a default of `{"schema_version": 1, "reproducible": False, "entries": [], "harness_mappings": []}`. Keep every existing handler untouched so create-flow tests still pass; if the fixture returns `calls` today, have it keep doing that (tests below re-derive POSTed bodies from `calls`).

```python
def _accept_items_as_precued(items):
    return [i for i in items if i.checked]


def test_edit_removes_an_unchecked_entry(wizard_env, monkeypatch):
    calls = wizard_env
    calls_state = ...  # however the fixture exposes state; adapt
    # manifest has alpha + ghost; world has alpha only
    ...
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(
        loadout_wizard, "_choose_edit",
        lambda items: [i for i in items if (i.entry or {}).get("name") != "ghost"])
    result = runner.invoke(app, ["loadout", "edit", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    published = next(c for c in calls if c["path"].endswith("/revisions") and c["method"] == "POST")
    names = [e["name"] for e in published["json_body"]["manifest"]["entries"]]
    assert names == ["alpha"]
    assert "Published revision" in result.output


def test_edit_adds_a_new_local_skill(wizard_env, monkeypatch):
    calls = wizard_env
    # manifest has alpha; world has alpha + beta; user checks beta too
    ...
    set_world(monkeypatch, make_world(contributor("alpha"), contributor("beta")))
    monkeypatch.setattr(loadout_wizard, "_choose_edit", lambda items: items_all_checked(items))
    result = runner.invoke(app, ["loadout", "edit", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    published = next(c for c in calls if c["path"].endswith("/revisions") and c["method"] == "POST")
    names = [e["name"] for e in published["json_body"]["manifest"]["entries"]]
    assert names == ["alpha", "beta"]


def test_edit_kept_entries_pass_through_unchanged(wizard_env, monkeypatch):
    # the kept alpha entry in the published manifest is byte-identical to the fetched one
    ...


def test_edit_no_changes_publishes_nothing(wizard_env, monkeypatch):
    ...
    result = runner.invoke(app, ["loadout", "edit", "drew/pack"])
    assert "No changes." in result.output
    assert not any(c["method"] == "POST" and c["path"].endswith("/revisions") for c in calls)


def test_edit_refuses_non_owner(wizard_env, monkeypatch):
    # identity handle != owner in the ref
    result = runner.invoke(app, ["loadout", "edit", "other/pack"])
    assert result.exit_code == 1
    assert "your own" in result.output
```

Each `...` must become real fixture code while writing the file: set `state["edit_manifest"]["entries"]` with `edit_entry(...)` values (from Task 1's helper), define the small `items_all_checked` helper (`lambda items: list(items)` works — name it or inline it), and adapt to how `wizard_env` actually exposes `calls`/state. No placeholder bodies may remain.

- [ ] **Step 2: Verify failure** — `No such command 'edit'`.

- [ ] **Step 3: Implement**

`loadout_wizard.py` — `_choose_edit` mirrors `_choose_skills`' questionary usage:

```python
def _choose_edit(items: list[_EditItem]) -> list[_EditItem]:
    choices = [questionary.Choice(title=i.label, checked=i.checked, value=i)
               for i in items]
    answer = questionary.checkbox(
        "Edit entries",
        choices=choices,
        instruction="(space to toggle, enter to accept, ctrl-c to abort)",
    ).ask()
    if answer is None:  # Ctrl-C
        typer.echo("Aborted.")
        raise typer.Exit(0)
    return answer
```

`run_edit` (transcribe; mirror `run`'s scan/status block and `update`'s fetch/ownership lines exactly as they exist in the code):

```python
def run_edit(ref: str, harness: str | None, creds: dict, base_url: str, home: Path) -> None:
    """Interactive membership edit: keep, drop, or add entries, then
    publish a new revision. Kept entries republish exactly as fetched;
    refreshing changed content stays `loadout update`'s job."""
    from drskill import service
    from drskill.cli import _echo_service_error

    if not _stdin_is_tty():
        typer.echo("loadout edit needs a terminal.")
        raise typer.Exit(1)
    owner, slug = ref.split("/", 1)
    try:
        identity = service.api_request("GET", "/api/v1/identity",
                                       token=creds["token"], base_url=base_url)
        if identity["user"]["handle"] != owner:
            typer.echo("You can only edit your own loadouts; fork it first.")
            raise typer.Exit(1)
        data = service.api_request("GET", f"/api/v1/loadouts/{owner}/{slug}",
                                   token=creds["token"], base_url=base_url)
        current = data["loadout"].get("current_revision")
        if not current:
            typer.echo(f"{ref} has no published revision; use loadout create/publish.")
            raise typer.Exit(1)
        document = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}/revisions/{current['number']}",
            token=creds["token"], base_url=base_url, raw=True)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    old = json.loads(document)
    entries = old.get("entries", [])

    with console.status("[bold]starting[/bold]", spinner="dots") as status:
        world, _findings = pipeline.run_scan(
            Path.cwd(), home, progress=lambda m: status.update(f"[bold]{escape(m)}[/bold]")
        )
    rows = _build_rows(world)
    if harness is not None:
        rows = [row for row in rows if harness in row.harnesses]

    items = _edit_items(rows, entries, harness)
    if not items:
        typer.echo("Nothing to edit.")
        raise typer.Exit(1)
    selected = _choose_edit(items)

    kept_ids = {id(i.entry) for i in selected if i.entry is not None}
    kept = [e for e in entries if id(e) in kept_ids]
    add_rows = [i.row for i in selected if i.entry is None and i.row is not None]
    skills = [r.contributor for r in add_rows if r.contributor.kind != "mcp_tool"]
    mcp_tools = [r.contributor for r in add_rows if r.contributor.kind == "mcp_tool"]

    hosted = _offer_registry(skills, creds, base_url, home)
    add_manifest, notes = manifest_build.contributors_to_manifest(skills, hosted=hosted)
    server_entries, server_notes = _mcp_server_entries(mcp_tools, world)
    notes += server_notes
    used = {e.get("selector") for e in kept}
    additions = []
    for e in add_manifest["entries"] + server_entries:
        if e["selector"] in used:
            notes.append(f"skipped {e['name']!r}: the loadout already has {e['selector']}")
            continue
        used.add(e["selector"])
        additions.append(e)

    removed = len(entries) - len(kept)
    if not additions and removed == 0:
        typer.echo("No changes.")
        return

    manifest = {
        "schema_version": 1,
        "reproducible": False,
        "entries": kept + additions,
        "harness_mappings": old.get("harness_mappings", []),
    }
    _print_summary(manifest, notes)
    typer.echo(f"+{len(additions)} added, -{removed} removed, {len(kept)} kept")
    if not typer.confirm(f"Publish these {len(manifest['entries'])} entries "
                         f"as a new revision of {ref}?", default=False):
        raise typer.Exit(0)
    _publish(ref, manifest, None, creds, base_url)
```

Check the module's existing imports: `json`, `pipeline`, `Path`, `console`, `escape`, `questionary`, `typer` — the create flow uses most; add what is missing. The `from drskill.cli import _echo_service_error` import must be inside the function (cli imports loadout_wizard lazily, but a module-level back-import would be circular). Mirror `run`'s exact scan/status idiom rather than this sketch if they differ.

`cli.py`, after the `create` command:

```python
@loadout_app.command()
def edit(
    ref: str = typer.Argument(..., help="owner/slug"),
    harness: str | None = typer.Option(None, "--harness",
        help="only list skills active in this harness"),
) -> None:
    """Edit a loadout's entries interactively and publish a new revision."""
    from drskill import loadout_wizard

    if not loadout_wizard._stdin_is_tty():
        typer.echo("loadout edit needs a terminal (run without piping stdin).")
        raise typer.Exit(1)
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    if harness is not None:
        _validate_harness(harness)
    loadout_wizard.run_edit(f"{owner}/{slug}", harness, creds, base, _home())
```

Note the tests run through CliRunner with piped stdin — `_stdin_is_tty` is monkeypatched truthy by the fixture (`wizard_env` already does this for create); confirm and rely on it.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_loadout_wizard.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/loadout_wizard.py src/drskill/cli.py tests/test_loadout_wizard.py
git commit -m "feat: edit loadout membership interactively"
```

---

### Task 3: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the docs**

Find where the README documents the loadout commands (create/install/status/update). Add two or three sentences on `drskill loadout edit`: interactive membership editing, phantoms shown as "not on this machine", kept entries republished untouched, and the split of duties with `update` (edit changes what's in the loadout; update refreshes changed content). Match the surrounding voice.

- [ ] **Step 2: Full suite, commit**

```bash
git add README.md
git commit -m "docs: describe loadout edit"
```
