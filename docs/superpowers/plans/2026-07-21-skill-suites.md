# Skill Suites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill list` gains a `suite` column that shows which suite each skill came from, recovered by matching installed skills against plugin caches (by content hash) and `skills-lock.json` sources.

**Architecture:** `suites.py` builds a registry: a content-hash-to-plugin-name map from `~/.claude/plugins/cache/`, and a skill-name-to-source map from the lockfile. The pipeline populates a new optional `Contributor.suite` after the world is built. `report.render_harness_tables` prints the column.

**Tech Stack:** Existing only. Reuses `resolution.content_hash`. No new dependencies, no network, no LLM.

**Spec:** `docs/superpowers/specs/2026-07-21-skill-suites-design.md`

## Global Constraints

- Suite is assigned by exact content-hash match to a plugin, else the lockfile `source` for the named skill, else nothing. Never guessed from a path or a name.
- A skill edited after install no longer matches by content and shows no suite. Blank beats wrong.
- Read-only: no process launch, no network. Every test sets `DRSKILL_HOME`.
- The scan report does not change. Only `list` gains the column.

---

### Task 1: The suite registry

**Files:**
- Create: `src/drskill/suites.py`
- Test: `tests/test_suites.py`

**Interfaces:**
- Produces:
  - `build_registry(home: Path) -> tuple[dict[str, str], dict[str, str]]` returning `(by_hash, by_name)`. `by_hash` maps a normalized content hash (the `sha256:...` string from `resolution.content_hash`) to a plugin name. `by_name` maps a skill name to a `source` string, read from every `skills-lock.json` found. A parse error on any file is skipped, not raised.
  - `suite_for(content_hash: str, name: str, by_hash: dict, by_name: dict) -> str | None` applying the order: hash match first, then name-in-lockfile, else None.

- [ ] **Step 1: Write the failing tests** — create `tests/test_suites.py`:

```python
import json
from pathlib import Path

from drskill import suites
from drskill.resolution import content_hash


def write_skill(path: Path, name: str, description: str, body: str = "b") -> str:
    path.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    (path / "SKILL.md").write_text(text)
    return content_hash(text)


def plugin_cache(home: Path, marketplace: str, plugin: str, version: str):
    return home / ".claude" / "plugins" / "cache" / marketplace / plugin / version / "skills"


def test_registry_maps_plugin_skill_by_content_hash(tmp_path):
    home = tmp_path / "home"
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    h = write_skill(skills / "brainstorming", "brainstorming", "Use when planning.")
    by_hash, _ = suites.build_registry(home)
    assert by_hash[h] == "superpowers"


def test_registry_indexes_every_cached_version(tmp_path):
    home = tmp_path / "home"
    old = plugin_cache(home, "official", "superpowers", "4.3.1")
    h_old = write_skill(old / "brainstorming", "brainstorming", "Old wording.")
    new = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(new / "brainstorming", "brainstorming", "New wording.")
    by_hash, _ = suites.build_registry(home)
    assert by_hash[h_old] == "superpowers"  # a match against the old version still counts


def test_registry_reads_lockfile_source(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "skills-lock.json").write_text(json.dumps({
        "version": 1,
        "skills": {"scaffold-docs": {"source": "dbreunig/scaffold-docs-skill",
                                     "sourceType": "github"}},
    }))
    _, by_name = suites.build_registry(home)
    assert by_name["scaffold-docs"] == "dbreunig/scaffold-docs-skill"


def test_registry_skips_corrupt_files(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "skills-lock.json").write_text("{not json")
    by_hash, by_name = suites.build_registry(home)
    assert by_hash == {} and by_name == {}


def test_suite_for_prefers_hash_then_name(tmp_path):
    by_hash = {"sha256:aa": "superpowers"}
    by_name = {"brainstorming": "someone/repo"}
    assert suites.suite_for("sha256:aa", "brainstorming", by_hash, by_name) == "superpowers"
    assert suites.suite_for("sha256:zz", "brainstorming", by_hash, by_name) == "someone/repo"
    assert suites.suite_for("sha256:zz", "unknown", by_hash, by_name) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_suites.py -v`
Expected: collection error, `No module named 'drskill.suites'`

- [ ] **Step 3: Implement** — create `src/drskill/suites.py`:

```python
"""Recover which suite a skill came from. drskill matches installed skills
against the plugin caches on disk (by content hash) and against
skills-lock.json sources. It never guesses from a path or a name."""

from __future__ import annotations

import json
from pathlib import Path

from drskill.resolution import content_hash


def _plugin_hashes(home: Path) -> dict[str, str]:
    by_hash: dict[str, str] = {}
    cache = home / ".claude" / "plugins" / "cache"
    if not cache.is_dir():
        return by_hash
    # cache/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md
    for skill_md in cache.glob("*/*/*/skills/*/SKILL.md"):
        plugin = skill_md.parents[3].name
        try:
            h = content_hash(skill_md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        by_hash.setdefault(h, plugin)  # first plugin to claim a hash wins, stable
    return by_hash


def _lockfile_sources(home: Path) -> dict[str, str]:
    by_name: dict[str, str] = {}
    for lock in home.rglob("skills-lock.json"):
        try:
            data = json.loads(lock.read_text())
        except Exception:
            continue
        entries = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if isinstance(entry, dict) and isinstance(entry.get("source"), str):
                by_name.setdefault(str(name), entry["source"])
    return by_name


def build_registry(home: Path) -> tuple[dict[str, str], dict[str, str]]:
    return _plugin_hashes(home), _lockfile_sources(home)


def suite_for(
    content_hash: str, name: str, by_hash: dict[str, str], by_name: dict[str, str]
) -> str | None:
    if content_hash in by_hash:
        return by_hash[content_hash]
    return by_name.get(name)
```

Note on the lockfile walk: `home.rglob("skills-lock.json")` covers a machine-level lockfile under the home dir. The project lockfile is read separately in Task 2 from the project root, so both scopes are covered.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_suites.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/suites.py tests/test_suites.py
git commit -m "feat: suite registry from plugin caches and lockfile sources"
```

---

### Task 2: The suite field, populated in the pipeline

**Files:**
- Modify: `src/drskill/models.py` (Contributor.suite)
- Modify: `src/drskill/pipeline.py` (populate suite)
- Test: `tests/test_suites.py`

**Interfaces:**
- Produces: `Contributor.suite: str | None = None`, populated in `run_scan` from `suites.build_registry(home)` plus the project lockfile source, matched per contributor by content hash then name.
- Consumes: Task 1's `build_registry` and `suite_for`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_suites.py`:

```python
from drskill.ledger import Config
from drskill.pipeline import run_scan


def test_pipeline_assigns_plugin_suite_to_a_flat_copy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    # a plugin cache defines 'brainstorming'
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(skills / "brainstorming", "brainstorming", "Use when planning a feature.")
    # the same skill is installed flat for claude-code, with identical content
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "brainstorming",
                "brainstorming", "Use when planning a feature.")
    world, _ = run_scan(proj, home, config=Config())
    c = next(c for c in world.contributors.values() if c.name == "brainstorming")
    assert c.suite == "superpowers"


def test_pipeline_leaves_suite_none_when_unknown(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "solo", "solo", "Use when doing a solo task.")
    world, _ = run_scan(proj, home, config=Config())
    c = next(c for c in world.contributors.values() if c.name == "solo")
    assert c.suite is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_suites.py -k "assigns_plugin or leaves_suite" -v`
Expected: FAIL — `suite` not a Contributor field, or not populated.

- [ ] **Step 3: Implement**

`src/drskill/models.py`, add to `Contributor`:

```python
    suite: str | None = None  # the plugin or repo this skill came from, when known
```

`src/drskill/pipeline.py`, after the lockfile-provenance block and before the MCP discovery, populate the suite:

```python
    from drskill import suites

    by_hash, by_name = suites.build_registry(home)
    # a project-scope lockfile source counts too
    if world.lockfile:
        for name, entry in world.lockfile.items():
            src = entry.get("source") if isinstance(entry, dict) else None
            if isinstance(src, str):
                by_name.setdefault(name, src)
    for cid, c in list(world.contributors.items()):
        if c.kind != "skill":
            continue
        found = suites.suite_for(c.content_hash, c.name, by_hash, by_name)
        if found is not None:
            world.contributors[cid] = c.model_copy(update={"suite": found})
```

(The project lockfile is already loaded into `world.lockfile` earlier in `run_scan`; its entries carry the same `source` field the machine lockfile does.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/models.py src/drskill/pipeline.py tests/test_suites.py
git commit -m "feat: populate Contributor.suite from the registry on every scan"
```

---

### Task 3: The suite column in list

**Files:**
- Modify: `src/drskill/report.py` (`render_harness_tables`)
- Test: `tests/test_suites.py`

**Interfaces:**
- Consumes: `Contributor.suite`.
- Produces: `list` and `list --tokens` render a `suite` column: the suite string, or empty when None, escaped.

- [ ] **Step 1: Write the failing test** — append to `tests/test_suites.py`:

```python
from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def test_list_shows_suite_column(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(skills / "brainstorming", "brainstorming", "Use when planning a feature.")
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "brainstorming",
                "brainstorming", "Use when planning a feature.")
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "suite" in r.output and "superpowers" in r.output


def test_list_suite_column_escapes_markup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "skills-lock.json").write_text(json.dumps({
        "version": 1,
        "skills": {"weird": {"source": "[red]x[/red]/repo"}},
    }))
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "weird", "weird", "Use when doing a weird task.")
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert "[red]x[/red]/repo" in r.output and "\x1b[31m" not in r.output
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_suites.py -k "suite_column or escapes_markup" -v`
Expected: FAIL — no suite column in the output.

- [ ] **Step 3: Implement** — in `src/drskill/report.py`, `render_harness_tables`:

Add the column after `source`:

```python
        table.add_column("source")
        table.add_column("suite")
```

Extend the row (the tokens columns come after suite, before notes — keep notes last). The current row build is:

```python
            row = [escape(c.name), escape(d.scope), escape(c.source.kind)]
            if tokens:
                row += [str(c.token_cost.catalog_tokens), str(c.token_cost.body_tokens)]
            row.append(escape(", ".join(notes)))
```

Change the first line to include the suite cell, so the column order matches the headers (skill, scope, source, suite, [catalog, body], notes):

```python
            row = [
                escape(c.name), escape(d.scope), escape(c.source.kind),
                escape(c.suite or ""),
            ]
            if tokens:
                row += [str(c.token_cost.catalog_tokens), str(c.token_cost.body_tokens)]
            row.append(escape(", ".join(notes)))
```

Update the `total (effective)` row to add one empty cell for the new column so its cells still line up. The current total row is:

```python
            table.add_row("total (effective)", "", "", str(cat_total), str(body_total), "",
                          style="bold")
```

It has cells for skill, scope, source, catalog, body, notes. Add one empty cell for suite (between source and catalog):

```python
            table.add_row("total (effective)", "", "", "", str(cat_total), str(body_total),
                          "", style="bold")
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (existing list/report tests updated in the same commit if they assert exact column counts)

- [ ] **Step 5: Commit**

```bash
git add src/drskill/report.py tests/test_suites.py
git commit -m "feat: suite column in the list tables"
```

---

### Task 4: Docs and verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README.** In the "Reading the report" section, add a sentence to the paragraph that describes the `list` `source` column: `list` also shows a `suite` column naming the plugin or repo a skill came from, recovered by matching the skill's content against the plugin caches on disk and the lockfile, and left blank when the origin cannot be verified. Plain style, no dashes.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: the suite column in list"
```

---

### Post-plan gates (run by the driver, not a subagent)

1. **Real machine gate:** `drskill list` on the author's loadout. Confirm the superpowers skills show `superpowers` in the suite column, lockfile skills show their `owner/repo`, and skills with no recoverable origin are blank. No path or name guessing produced a wrong label.
2. **Code review pass**, then finishing-a-development-branch.
