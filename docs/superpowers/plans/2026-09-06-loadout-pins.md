# Loadout Pins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `loadout install` writes pins binding installed directories to loadout entries; `loadout status`/`update` resolve entries through pins before falling back to the name heuristic.

**Architecture:** New pure module `src/drskill/pins.py` (model, path, load, record, prune). `cli.py`'s loadout `install` records pins for hosted/github skill outcomes `installed`/`unchanged`. `loadout_drift.classify_entries` gains `pins`/`ref` parameters; `status` and `update` thread them through.

**Tech Stack:** Python 3.11+, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-loadout-pins-design.md` (committed with this plan).

## Global Constraints

- Work only in the worktree; every git/test command `cd`s into it. Never run git in the main checkout.
- Full suite before each commit: `uv run --extra connect --extra deep pytest`.
- Behavior without pins is unchanged everywhere: every existing test passes unmodified (fixtures produce no pins file unless a test writes one; the install fixture WILL now produce pins — its existing tests assert on outputs/paths, not directory listings, so they stay green; verify).
- Transcribe docstrings and comments verbatim. Commit per task, conventional prefix, ending with:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

## Code facts

- `cli.py` loadout `install`: `target, scope = _install_target(harness, project, user, root, home)` where scope is "project"/"user"; hosted loop uses `_existing_dir_status` → statuses installed/unchanged/held; github loop via `_install_one_github`; both append `(entry["name"], dest_path)` to `bridged` exactly when status is installed/unchanged — that append site identifies the pin-worthy outcomes and the dest path.
- `loadout_drift.classify_entries(entries, contributors, servers=None)`: skill branch builds `by_name` and searches; `EntryStatus(entry, contributor, state, note, server)`; `_compare(entry, contributor)` returns matches/changed/unreadable.
- `status` command: `classify_entries(entries, contributors, servers=world.mcp_servers)`; `update`: `classify_entries(manifest.get("entries", []), list(world.contributors.values()), servers=world.mcp_servers)`. Both have `root = Path.cwd()` equivalents and `home = _home()` in scope (verify variable names at the call sites).
- Contributor ids are resolved realpaths (`str(inst.skill_file.resolve())` in build_world); installed skill dirs hold the primary file at `<dir>/SKILL.md`, so the contributor id for a pinned dir is `str((base / relpath / "SKILL.md").resolve())`.
- `tests/test_cli_install.py` `env` fixture: `home`, `project` tmp dirs, DRSKILL_HOME set, cwd=project; `manifest([...])` helper; `hosted_entry()`/`github_entry()`.
- `tests/test_loadout_drift.py` has `loadout_drift` imported plus contributor-building helpers; `tests/test_cli_status.py`/`test_cli_update.py` fixtures mock `run_scan` returning a `World` built from `state["world"]` contributors (ids like `/tmp/x/SKILL.md`).

---

### Task 1: The pins module

**Files:**
- Create: `src/drskill/pins.py`
- Test: `tests/test_pins.py`

**Interfaces:**
- Produces:

```python
class Pin(BaseModel):
    loadout: str
    revision: int | None = None
    selector: str
    source_type: str
    content_hash: str
    installed_at: str = ""

def pins_path(base: Path) -> Path                       # base/.drskill/pins.json
def load_pins(base: Path) -> dict[str, Pin]             # relpath/abspath key -> Pin; {} on missing/corrupt
def record_pin(base: Path, target: Path, pin: Pin) -> None
def prune_pins(base: Path) -> None
def resolve_pins(project_root: Path, home: Path) -> dict[str, Pin]
```

`record_pin` keys by `str(target.relative_to(base))`, falling back to `str(target)` on `ValueError`; it read-modify-writes the JSON document (sorted keys, indent 2, trailing newline) creating `.drskill/` as needed. `prune_pins` drops entries whose key, joined to `base` (or taken absolute), no longer exists as a directory; missing file is a no-op. `resolve_pins` merges both scopes into one dict keyed by the RESOLVED absolute primary-file path `str((root_for_scope / key / "SKILL.md").resolve())` (absolute keys skip the join), project scope winning on collisions.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pins.py`:

```python
import json
from pathlib import Path

from drskill import pins


def make_pin(**overrides):
    fields = dict(loadout="drew/pack", revision=3, selector="skill:vector",
                  source_type="drskill", content_hash="sha256:" + "ab" * 32,
                  installed_at="2026-09-06")
    fields.update(overrides)
    return pins.Pin(**fields)


def test_record_and_load_roundtrip(tmp_path):
    target = tmp_path / ".agents" / "skills" / "vector"
    target.mkdir(parents=True)
    pins.record_pin(tmp_path, target, make_pin())
    loaded = pins.load_pins(tmp_path)
    assert loaded[".agents/skills/vector"].loadout == "drew/pack"
    assert loaded[".agents/skills/vector"].revision == 3


def test_record_overwrites_the_same_path(tmp_path):
    target = tmp_path / ".agents" / "skills" / "vector"
    target.mkdir(parents=True)
    pins.record_pin(tmp_path, target, make_pin())
    pins.record_pin(tmp_path, target, make_pin(loadout="drew/other", revision=9))
    loaded = pins.load_pins(tmp_path)
    assert len(loaded) == 1
    assert loaded[".agents/skills/vector"].loadout == "drew/other"


def test_record_outside_base_stores_absolute(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere" / "skills" / "x"
    elsewhere.mkdir(parents=True)
    pins.record_pin(base, elsewhere, make_pin())
    assert str(elsewhere) in pins.load_pins(base)


def test_load_tolerates_missing_and_garbage(tmp_path):
    assert pins.load_pins(tmp_path) == {}
    p = pins.pins_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("not json")
    assert pins.load_pins(tmp_path) == {}
    p.write_text(json.dumps({"x": {"loadout": 5}}))
    assert pins.load_pins(tmp_path) == {}


def test_prune_drops_dead_paths(tmp_path):
    live = tmp_path / ".agents" / "skills" / "vector"
    live.mkdir(parents=True)
    dead = tmp_path / ".agents" / "skills" / "gone"
    dead.mkdir(parents=True)
    pins.record_pin(tmp_path, live, make_pin())
    pins.record_pin(tmp_path, dead, make_pin(selector="skill:gone"))
    dead.rmdir()
    pins.prune_pins(tmp_path)
    assert list(pins.load_pins(tmp_path)) == [".agents/skills/vector"]


def test_resolve_pins_merges_scopes_and_keys_by_primary_file(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    for base, name in ((proj, "vector"), (home, "tidy")):
        d = base / ".agents" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")
        pins.record_pin(base, d, make_pin(selector=f"skill:{name}"))
    resolved = pins.resolve_pins(proj, home)
    assert str((proj / ".agents/skills/vector/SKILL.md").resolve()) in resolved
    assert str((home / ".agents/skills/tidy/SKILL.md").resolve()) in resolved
```

- [ ] **Step 2: Verify failure** — ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `src/drskill/pins.py`:

```python
"""Bindings from installed directories to the loadout entries they came
from. A pin is not provenance: Provenance answers where content came
from; a pin answers which loadout revision this installed copy belongs
to. One JSON document per scope; the project file is meant to be
committed, like the caches, so teammates resolve the same bindings."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError


class Pin(BaseModel):
    loadout: str
    revision: int | None = None
    selector: str
    source_type: str
    content_hash: str
    installed_at: str = ""


def pins_path(base: Path) -> Path:
    return base / ".drskill" / "pins.json"


def load_pins(base: Path) -> dict[str, Pin]:
    path = pins_path(base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Pin] = {}
    for key, value in data.items():
        try:
            out[str(key)] = Pin.model_validate(value)
        except ValidationError:
            return {}  # a corrupt document is ignored whole; installs rewrite it
    return out


def _write(base: Path, entries: dict[str, Pin]) -> None:
    path = pins_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {k: entries[k].model_dump() for k in sorted(entries)}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def record_pin(base: Path, target: Path, pin: Pin) -> None:
    try:
        key = str(target.relative_to(base))
    except ValueError:
        # A bridge-retargeted install can land outside the scope base; an
        # absolute key still binds on this machine.
        key = str(target)
    entries = load_pins(base)
    entries[key] = pin
    _write(base, entries)


def prune_pins(base: Path) -> None:
    entries = load_pins(base)
    if not entries:
        return
    kept = {k: v for k, v in entries.items()
            if (Path(k) if Path(k).is_absolute() else base / k).is_dir()}
    if len(kept) != len(entries):
        _write(base, kept)


def resolve_pins(project_root: Path, home: Path) -> dict[str, Pin]:
    """Both scopes merged, keyed by the resolved primary-file path so a
    scanned contributor's id looks itself up directly. Project pins win
    a collision."""
    out: dict[str, Pin] = {}
    for base in (home, project_root):
        for key, pin in load_pins(base).items():
            d = Path(key) if Path(key).is_absolute() else base / key
            out[str((d / "SKILL.md").resolve())] = pin
    return out
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_pins.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/pins.py tests/test_pins.py
git commit -m "feat: add the pins store binding installs to loadout entries"
```

---

### Task 2: Install writes pins

**Files:**
- Modify: `src/drskill/cli.py` (loadout `install` command)
- Test: `tests/test_cli_install.py`

**Interfaces:**
- Consumes: `pins.Pin`, `record_pin`, `prune_pins`.
- Produces: behavior — after a hosted or github skill entry ends `installed` or `unchanged`, a pin is recorded for its dest dir under the install scope's base (`root` when scope == "project", else `home`); after the loops, `prune_pins(pin_base)` runs once. MCP entries never pin.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_install.py`:

```python
def test_install_writes_pins_for_skills(env):
    home, project, state = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    from drskill import pins as pins_mod
    pinned = pins_mod.load_pins(project)
    assert ".agents/skills/vector" in pinned
    vec = pinned[".agents/skills/vector"]
    assert vec.loadout == "drew/pack"
    assert vec.revision == 2
    assert vec.selector == "skill:vector"
    assert ".agents/skills/citation" in pinned


def test_reinstall_rebinds_unchanged_entries(env):
    home, project, state = env
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    from drskill import pins as pins_mod
    pins_mod.pins_path(project).unlink()
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert ".agents/skills/vector" in pins_mod.load_pins(project)


def test_user_scope_install_pins_under_home(env):
    home, project, state = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--user"], input="y\n")
    assert result.exit_code == 0, result.output
    from drskill import pins as pins_mod
    assert ".agents/skills/vector" in pins_mod.load_pins(home)
    assert pins_mod.load_pins(project) == {}


def test_mcp_entries_do_not_pin(env):
    home, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    from drskill import pins as pins_mod
    assert pins_mod.load_pins(project) == {}
```

Note: the `env` fixture's `MANIFEST` has revision number 2 in the loadout GET — confirm and match `revision=2` (read the fixture). `--user` install may trigger bridge prompts; existing user-scope tests show what input is needed — mirror them (adjust `input=` accordingly, e.g. extra "y\n" declines/accepts; read `test_install_hosted_and_github_entries`, which installs to the user store by default with plain `input="y\n"` — actually the default scope in the fixture is user (no .git in project); check the existing tests' expectations and write `test_user_scope_install_pins_under_home` accordingly, possibly without `--user` if the default already lands in home).

- [ ] **Step 2: Verify failure** — pins files absent.

- [ ] **Step 3: Implement**

In the loadout `install` command in `cli.py`:

After the `target, scope = _install_target(...)` line, compute the pin base:

```python
    pin_base = root if scope == "project" else home
```

The command needs the revision number as an int: `revision` at that point is a string (number or `sha256:` form). Add:

```python
    pin_revision = int(revision) if str(revision).isdigit() else None
```

In the hosted loop, where `bridged.append((entry["name"], dest))` runs for installed/unchanged, also:

```python
            _record_install_pin(pin_base, dest, owner, slug, pin_revision, entry)
```

Same in the github loop at its `bridged.append(...)` site (dest there is `target / entry["name"]`). Add the helper near `_install_one_github`:

```python
def _record_install_pin(pin_base: Path, dest: Path, owner: str, slug: str,
                        revision: int | None, entry: dict) -> None:
    import datetime as dt

    from drskill import pins

    pins.record_pin(pin_base, dest, pins.Pin(
        loadout=f"{owner}/{slug}", revision=revision,
        selector=entry.get("selector") or f"skill:{entry['name']}",
        source_type=entry.get("source_type") or "",
        content_hash=entry.get("content_hash") or "",
        installed_at=dt.date.today().isoformat(),
    ))
```

(`datetime` is imported at cli.py's top as `dt` — check; if so drop the local import and use the module one.) After both loops (before `_offer_bridges`), add:

```python
    from drskill import pins as pins_mod
    pins_mod.prune_pins(pin_base)
```

MCP entries: no change (their loop never touches `bridged` or pins).

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_cli_install.py -v` (all pre-existing tests must stay green; installs now write a pins file, which no existing test forbids — if one fails, inspect before changing it and report).
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/cli.py tests/test_cli_install.py
git commit -m "feat: pin installed skills to their loadout entries"
```

---

### Task 3: Drift resolves through pins

**Files:**
- Modify: `src/drskill/loadout_drift.py`
- Test: `tests/test_loadout_drift.py`

**Interfaces:**
- Produces: `classify_entries(entries, contributors, servers=None, pins=None, ref=None)` where `pins` is the `resolve_pins` dict (contributor id -> Pin). For a skill entry: if any contributor's id is a key in `pins` whose Pin has `loadout == ref` and `selector == entry.get("selector")`, that contributor is the match — `EntryStatus(entry, c, _compare(entry, c))`, no note. Otherwise today's name search runs unchanged. Update the module docstring: matching is by pin binding first, then by normalized name for unpinned installs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loadout_drift.py` (reuse its contributor/entry helpers; read how existing tests build skill contributors and entries — there are `contributor(...)`-style builders or inline `Contributor(...)` constructions; mirror them):

```python
def test_pinned_entry_matches_the_pinned_contributor_not_the_name_twin():
    # two local skills share the name; only one is the pinned install
    pinned = skill_contributor(id="/store/.agents/skills/vector/SKILL.md",
                               name="vector", content_hash="sha256:" + "aa" * 32)
    twin = skill_contributor(id="/elsewhere/vector/SKILL.md",
                             name="vector", content_hash="sha256:" + "bb" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "aa" * 32)
    pin = pins.Pin(loadout="drew/pack", revision=2, selector="skill:vector",
                   source_type="local", content_hash="sha256:" + "aa" * 32)
    statuses = loadout_drift.classify_entries(
        [entry], [twin, pinned],
        pins={pinned.id: pin}, ref="drew/pack")
    assert statuses[0].contributor is pinned
    assert statuses[0].state == "matches"
    assert statuses[0].note is None


def test_pin_for_another_loadout_is_ignored():
    c = skill_contributor(id="/store/x/SKILL.md", name="vector",
                          content_hash="sha256:" + "aa" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "aa" * 32)
    pin = pins.Pin(loadout="other/pack", selector="skill:vector",
                   source_type="local", content_hash="sha256:" + "aa" * 32)
    statuses = loadout_drift.classify_entries(
        [entry], [c], pins={c.id: pin}, ref="drew/pack")
    assert statuses[0].state == "matches"  # via the name fallback


def test_no_pins_behaves_as_before():
    c = skill_contributor(id="/x/SKILL.md", name="vector",
                          content_hash="sha256:" + "aa" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "aa" * 32)
    assert loadout_drift.classify_entries([entry], [c])[0].state == "matches"
```

Define `skill_contributor`/`skill_entry` helpers in the test file if equivalents don't already exist — model them on the file's existing fixtures (a `Contributor` needs id/name/scope/token_cost/content_hash; a skill entry needs kind/selector/name/source_type "local"/content_hash/local_only/metadata). The `source_type: "local"` entry path in `_compare` compares `contributor.content_hash` to `entry["content_hash"]` without touching the filesystem — use it so tests need no real files.

- [ ] **Step 2: Verify failure** — TypeError on the new kwargs.

- [ ] **Step 3: Implement**

In `src/drskill/loadout_drift.py`:

```python
def classify_entries(entries: list[dict], contributors: list[Contributor],
                     servers: list | None = None,
                     pins: dict | None = None,
                     ref: str | None = None) -> list[EntryStatus]:
```

Before the name-search in the skill branch:

```python
        if pins and ref:
            bound = next(
                (c for c in skills
                 if (p := pins.get(c.id)) is not None
                 and p.loadout == ref and p.selector == entry.get("selector")),
                None,
            )
            if bound is not None:
                out.append(EntryStatus(entry, bound, _compare(entry, bound)))
                continue
```

(place it after the `kind != "skill"` gate and before `candidates = by_name...`). Update the module docstring's first paragraph to: "Matching is by pin binding when the install recorded one, then by normalized name for unpinned installs. A hash tiebreak settles duplicate names; an unresolved duplicate carries an ambiguity note."

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_loadout_drift.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/loadout_drift.py tests/test_loadout_drift.py
git commit -m "feat: resolve loadout entries through pins before the name heuristic"
```

---

### Task 4: Status and update consume pins; docs

**Files:**
- Modify: `src/drskill/cli.py` (`status` and `update` commands), `README.md`
- Test: `tests/test_cli_status.py`

**Interfaces:**
- Consumes: `pins.resolve_pins(project_root, home)`; `classify_entries(..., pins=..., ref=...)`.

Behavior: both commands compute `resolved = pins.resolve_pins(Path.cwd(), home)` once (after the scan) and pass `pins=resolved, ref=f"{owner}/{slug}"` to their `classify_entries` calls (`status` iterates targets — the ref is per target).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_status.py` (its fixture mocks `run_scan` with contributors from `state["world"]`; DRSKILL_HOME points at a tmp home). Build the scenario: two same-named contributors, one pinned; a pins.json written under the CWD the CLI runs in — check how the fixture controls cwd (monkeypatch.chdir?) and write `pins.json` via `pins_mod.record_pin` against that root with a directory whose `SKILL.md` path equals the pinned contributor's id... The mocked contributors use ids like `/tmp/x/SKILL.md` that don't exist on disk, and `resolve_pins` keys by `(base / key / "SKILL.md").resolve()`. To make the lookup land, create a REAL directory under the test project root, use its resolved SKILL.md path as the pinned contributor's id, and record the pin for that directory:

```python
def test_status_prefers_the_pinned_contributor(env, tmp_path, monkeypatch):
    from drskill import pins as pins_mod
    from drskill.models import Contributor, TokenCost

    root = Path.cwd()  # the fixture chdir'd here
    d = root / ".agents" / "skills" / "vector"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("x")
    pinned_id = str((d / "SKILL.md").resolve())
    good = "sha256:" + "aa" * 32
    bad = "sha256:" + "bb" * 32
    env["world"] = [
        Contributor(id="/elsewhere/vector/SKILL.md", name="vector", scope="project",
                    token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
                    content_hash=bad),
        Contributor(id=pinned_id, name="vector", scope="project",
                    token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
                    content_hash=good),
    ]
    env["manifest"]["entries"] = [entry(name="vector", content_hash=good)]
    pins_mod.record_pin(root, d, pins_mod.Pin(
        loadout="drew/pack", revision=2, selector="skill:vector",
        source_type="local", content_hash=good))
    result = runner.invoke(app, ["loadout", "status"])
    assert result.exit_code == 0, result.output
    assert "matches" in result.output
    assert "share this name" not in result.output
```

Adapt to the fixture's real `entry(...)` helper fields (its default entries are `source_type` "local"? read it — if the default entry helper builds a github/drskill entry, pass `source_type="local"` so `_compare` uses content_hash without filesystem reads) and to how `env` exposes state. Without the pin, this scenario yields the ambiguity note or a changed verdict via candidates[0] — the assertion pair (matches + no note) pins the new behavior.

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement**

In `cli.py` `status`: after the `_scan_with_status` call, add

```python
    from drskill import pins as pins_mod
    resolved_pins = pins_mod.resolve_pins(Path.cwd(), home)
```

and the classify call becomes `classify_entries(entries, contributors, servers=world.mcp_servers, pins=resolved_pins, ref=f"{owner}/{slug}")` (inside the per-target loop, owner/slug are loop variables — verify names). Same two lines in `update`, with its single owner/slug. README: in the loadout section, two or three sentences: installs record pins in `.drskill/pins.json` (project) or `~/.drskill/pins.json` (user scope); status and update use them to match entries to the exact installed copy instead of guessing by name; the project file is meant to be committed like the caches.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_cli_status.py tests/test_cli_update.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/cli.py README.md tests/test_cli_status.py
git commit -m "feat: consume pins in loadout status and update"
```
