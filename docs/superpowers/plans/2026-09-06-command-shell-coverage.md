# Command Shell Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover Claude Code command files (`.claude/commands/`, `~/.claude/commands/`, nested namespace directories) and put them in front of the shell-injection checks, plus annotate findings when `disableSkillShellExecution` turns the feature off on this machine.

**Architecture:** Command files become contributors with a new `kind: "command"`. The new kind is deliberately conservative: every existing consumer that gates on `kind == "skill"` (budget, duplicates, shadowing, heuristics, spec, lockfile, suites, deep, loadout drift) excludes commands automatically, and we opt in only where commands belong — the injection scan view, the shell-baseline lifecycle, cache pruning, and report rendering. A settings reader populates a new `World.shell_execution_disabled` flag that both shell checks append to their messages.

**Tech Stack:** Python 3.11+, pydantic, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-04-shell-injection-design.md` — its "Follow-ups (logged, not built)" section names exactly these two items. Decisions made for this plan:
- New contributor kind `"command"` rather than riding as `"skill"`: a missed audit then means a command is conservatively absent from a feature, not a false finding in one. Commands never enter loadout wizard rows, suites, budget, duplicates, shadowing, heuristics, spec checks, or deep judging.
- Command discovery is recursive (`**/*.md`) because Claude Code namespaces commands in subdirectories. The command name is the file stem; namespace prefixes are not modeled yet.
- `disableSkillShellExecution` annotates finding messages; it never suppresses checks or changes severities. Precedence: project `.claude/settings.local.json`, then project `.claude/settings.json`, then `~/.claude/settings.json` — the first file that states the key wins.

## Global Constraints

- Work only in the worktree; run every git/test command with `cd` into it in the same compound command.
- Run focused tests per task; full suite before each commit: `uv run --extra connect --extra deep pytest` from the worktree root (the bare command misses extras).
- No behavior change for skills or MCP tools anywhere: every existing test must pass unmodified unless a task explicitly says otherwise.
- Transcribe the plan's docstrings and comments verbatim.
- Commit per task with a conventional prefix; end every commit message with:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

## Code facts the tasks rely on

- `Contributor.kind` is `Literal["skill", "mcp_tool"]` at `src/drskill/models.py:66`; `RawInstance` (models.py:11-19) has no kind field.
- `discovery.discover` (src/drskill/discovery.py:67) walks `h.search_paths(...)` and builds `RawInstance`s; `_walk_dirs` (line 11) is the loop-safe recursive walker.
- `resolution.make_contributor(skill_file, scope="project")` (src/drskill/resolution.py:166) never sets `kind`; `build_world` (line 213) creates contributors from instances; `_mark_shadows` (line 250) iterates `world.harness_loads(hid)` for every contributor.
- `checks/injection.py::scan_view` (line 65) gates `if c.kind != "skill": return []` and reads `Path(c.id)` as the `skillmd` source; bundled files come from `c.bundled_files`, which is empty for anything that is not a `SKILL.md` (resolution.py:191), so command files bring no extra sources.
- `checks/skill_shell.py` implements both shell checks by iterating `world.contributors.values()` and calling `_skillmd(c)`, which returns None when `scan_view` gave nothing — so once `scan_view` admits commands, both checks work unchanged.
- `pipeline.py:109-119` populates `world.shell_approved` for `c.kind == "skill"`; `cli.py` prune computes `valid_keys` over `c.kind == "skill"` (~line 910).
- `report.py:91,123-126` renders rows with `is_tool = c.kind == "mcp_tool"` and labels `"mcp tool" if is_tool else "skill"`.
- `loadout_wizard._build_rows` (src/drskill/loadout_wizard.py:147) includes every non-system contributor as a pickable row.
- Claude Code documents project commands at `.claude/commands/*.md` and personal commands at `~/.claude/commands/*.md`, with subdirectories acting as namespaces.

---

### Task 1: The command kind and harness data

**Files:**
- Modify: `src/drskill/models.py` (Contributor.kind, RawInstance), `src/drskill/harnesses.py` (HarnessDef), `src/drskill/data/harnesses.toml` (claude-code entry)
- Test: `tests/test_harnesses.py`

**Interfaces:**
- Produces: `Contributor.kind: Literal["skill", "mcp_tool", "command"]`; `RawInstance.kind: Literal["skill", "command"] = "skill"`; `HarnessDef.command_project_paths: list[str]` and `command_global_paths: list[str]` (both default empty); the claude-code harness carries `command_project_paths = [".claude/commands"]`, `command_global_paths = ["~/.claude/commands"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_harnesses.py` (reuse its existing helper for loading harness defs; if it has a `get(id)` style helper, use it, else `next(h for h in load_harnesses() if h.id == "claude-code")`):

```python
def test_claude_code_declares_command_paths():
    h = next(h for h in load_harnesses() if h.id == "claude-code")
    assert h.command_project_paths == [".claude/commands"]
    assert h.command_global_paths == ["~/.claude/commands"]


def test_command_paths_default_empty():
    h = next(h for h in load_harnesses() if h.id == "codex")
    assert h.command_project_paths == []
    assert h.command_global_paths == []
```

Add the `load_harnesses` import if the file lacks it (`from drskill.harnesses import load_harnesses`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_harnesses.py -v -k command`
Expected: FAIL with an AttributeError or pydantic error for `command_project_paths`.

- [ ] **Step 3: Implement**

`src/drskill/models.py`: change line 66 to

```python
    kind: Literal["skill", "mcp_tool", "command"] = "skill"
```

and add to `RawInstance` (after `order`):

```python
    # "command": a slash-command markdown file; rides discovery but gets
    # its own contributor kind so skill-only consumers exclude it.
    kind: Literal["skill", "command"] = "skill"
```

`src/drskill/harnesses.py`: add to `HarnessDef` after `root_md_paths`:

```python
    # Slash-command file roots, scanned recursively for *.md; commands are
    # explicitly invoked, so they join only the injection checks, never
    # budget/routing accounting.
    command_project_paths: list[str] = Field(default_factory=list)
    command_global_paths: list[str] = Field(default_factory=list)
```

`src/drskill/data/harnesses.toml`, in the claude-code entry after `root_md_paths = []`:

```toml
# Command files: project .claude/commands and personal ~/.claude/commands,
# per Claude Code's slash-command docs; subdirectories namespace commands.
command_project_paths = [".claude/commands"]
command_global_paths = ["~/.claude/commands"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_harnesses.py tests/test_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS (the new fields are inert until Task 2).

```bash
git add src/drskill/models.py src/drskill/harnesses.py src/drskill/data/harnesses.toml tests/test_harnesses.py
git commit -m "feat: model command files as their own contributor kind"
```

---

### Task 2: Discover command files

**Files:**
- Modify: `src/drskill/discovery.py`, `src/drskill/resolution.py`
- Test: `tests/test_discovery.py`, `tests/test_resolution.py`

**Interfaces:**
- Consumes: Task 1's fields.
- Produces: `discover` returns `RawInstance(kind="command", ...)` for every `*.md` under a command path (recursive); `make_contributor(skill_file, scope="project", kind="skill")` gains the `kind` parameter and stamps it on the contributor; `build_world` passes `inst.kind`; `_mark_shadows` skips non-skill contributors.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discovery.py` (mirror its existing fixture style — it builds throwaway `HarnessDef`s and calls `discover(h, root, home)`):

```python
def test_command_paths_discover_md_files_recursively(tmp_path):
    h = HarnessDef(id="t", display_name="T", project_paths=[".claude/skills"],
                   command_project_paths=[".claude/commands"],
                   command_global_paths=["~/.claude/commands"])
    root, home = tmp_path / "proj", tmp_path / "home"
    (root / ".claude" / "commands" / "ns").mkdir(parents=True)
    (root / ".claude" / "commands" / "deploy.md").write_text("run !`make deploy`\n")
    (root / ".claude" / "commands" / "ns" / "release.md").write_text("release\n")
    (root / ".claude" / "commands" / "notes.txt").write_text("not a command\n")
    (home / ".claude" / "commands").mkdir(parents=True)
    (home / ".claude" / "commands" / "tidy.md").write_text("tidy\n")

    instances, _broken, _unreadable = discover(h, root, home)
    commands = [i for i in instances if i.kind == "command"]
    names = sorted(i.skill_file.name for i in commands)
    assert names == ["deploy.md", "release.md", "tidy.md"]
    scopes = {i.skill_file.name: i.scope for i in commands}
    assert scopes["deploy.md"] == "project"
    assert scopes["tidy.md"] == "user"


def test_skills_are_not_marked_as_commands(tmp_path):
    h = HarnessDef(id="t", display_name="T", project_paths=[".claude/skills"])
    root, home = tmp_path / "proj", tmp_path / "home"
    (root / ".claude" / "skills" / "s").mkdir(parents=True)
    (root / ".claude" / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\nbody\n")
    instances, _broken, _unreadable = discover(h, root, home)
    assert [i.kind for i in instances] == ["skill"]
```

Append to `tests/test_resolution.py` (mirror its imports):

```python
def test_command_contributor_kind_and_name(tmp_path):
    f = tmp_path / ".claude" / "commands" / "ns" / "release.md"
    f.parent.mkdir(parents=True)
    f.write_text("release it with !`make release`\n")
    c, unreadable = make_contributor(f, "project", kind="command")
    assert unreadable == []
    assert c.kind == "command"
    assert c.name == "release"
    assert c.bundled_files == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_discovery.py tests/test_resolution.py -v -k command`
Expected: FAIL — `discover` yields no command instances, and `make_contributor` rejects the `kind` keyword.

- [ ] **Step 3: Implement**

`src/drskill/discovery.py`: add a finder near `_find_skill_files`:

```python
def _find_command_files(base: Path) -> list[Path]:
    out = []
    for dirpath, _dirnames, filenames in _walk_dirs(base):
        out += [dirpath / n for n in filenames if n.endswith(".md")]
    return sorted(out)
```

In `discover`, after the native-path loop and before the plugin block, add:

```python
    # Command files load by explicit invocation, not routing, so they never
    # compete with skills for load order; the 1000 band keeps sorts stable
    # without colliding with native or plugin path indexes.
    command_specs = [(project_root / s, "project", s) for s in h.command_project_paths]
    command_specs += [(home / s.removeprefix("~/"), "user", s) for s in h.command_global_paths]
    if global_only:
        command_specs = [t for t in command_specs if t[1] == "user"]
    for order, (base, scope, _spec) in enumerate(command_specs, start=1000):
        if not base.is_dir():
            continue
        for f in _find_command_files(base):
            if not f.exists():
                continue
            instances.append(
                RawInstance(harness=h.id, scope=scope, skill_file=f,
                            via_symlink=_via_symlink(f, base), order=order,
                            kind="command")
            )
        broken += [BrokenSymlink(harness=h.id, path=p)
                   for p in _find_broken_symlinks(base, recursive=True)]
```

`src/drskill/resolution.py`:

- `make_contributor` signature becomes `def make_contributor(skill_file: Path, scope="project", kind: str = "skill") -> ...` and the `Contributor(...)` construction gains `kind=kind`.
- In `build_world`, the creation call becomes `make_contributor(inst.skill_file, inst.scope, inst.kind)`.
- In `_mark_shadows`, at the top of the `for c, d in world.harness_loads(hid):` loop add:

```python
            if c.kind != "skill":
                # Commands are invoked by explicit name+namespace; they do
                # not shadow or get shadowed by routed skills.
                continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_discovery.py tests/test_resolution.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS. If any existing test fails because a fixture directory accidentally contains stray `.md` files under a configured command path, inspect before changing anything and report it in your notes — expected outcome is no failures since no test fixture uses `.claude/commands`.

```bash
git add src/drskill/discovery.py src/drskill/resolution.py tests/test_discovery.py tests/test_resolution.py
git commit -m "feat: discover claude-code command files as command contributors"
```

---

### Task 3: Shell checks cover commands

**Files:**
- Modify: `src/drskill/checks/injection.py` (scan_view gate + docstring), `src/drskill/pipeline.py` (baseline gate), `src/drskill/cli.py` (prune gate), `src/drskill/loadout_wizard.py` (row filter)
- Test: `tests/test_checks_skill_shell.py`, `tests/test_loadout_wizard.py`

**Interfaces:**
- Consumes: command contributors from Task 2.
- Produces: behavior only — `scan_view` returns a `skillmd` source for `kind == "command"`, so `injection-shell-unreviewed` and `injection-shell-dangerous` fire on command files; baselines load and prune for commands; the wizard never lists commands.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_checks_skill_shell.py` (reuse its `make_world`/`run_check` helpers; add a writer mirroring `write_skill`):

```python
def write_command(root, name, body):
    f = root / ".claude" / "commands" / f"{name}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


def make_command_world(root):
    h = HarnessDef(id="t3", display_name="T3", project_paths=[".claude/skills"],
                   command_project_paths=[".claude/commands"], recursive=True)
    instances, broken, _ = discover(h, root, root / "no-home")
    return build_world(instances, {h.id: h}, broken)


def test_command_file_shell_commands_are_unreviewed(tmp_path):
    write_command(tmp_path, "deploy", "Deploy with:\n!`make deploy`\n")
    world = make_command_world(tmp_path)
    findings = run_check("injection-shell-unreviewed", world, Config())
    assert len(findings) == 1
    assert "make deploy" in findings[0].message
    assert findings[0].skills == ["deploy"]


def test_command_file_dangerous_commands_fire(tmp_path):
    write_command(tmp_path, "leak", "!`cat ~/.aws/credentials`\n")
    world = make_command_world(tmp_path)
    findings = run_check("injection-shell-dangerous", world, Config())
    assert len(findings) == 1


def test_command_without_shell_is_silent(tmp_path):
    write_command(tmp_path, "plain", "Just prose, no shell.\n")
    world = make_command_world(tmp_path)
    assert run_check("injection-shell-unreviewed", world, Config()) == []
    assert run_check("injection-shell-dangerous", world, Config()) == []
```

Adapt helper names to the file's actual imports (`HarnessDef`, `discover`, `build_world`, `Config` are already imported there or import them the way the file's header does). If the existing `make_world` helper already accepts a HarnessDef or paths, extend the pattern rather than duplicating it.

Append to `tests/test_loadout_wizard.py`:

```python
def test_command_contributors_are_not_offered(wizard_env, monkeypatch):
    command = contributor("deploy")
    command = command.model_copy(update={"kind": "command", "id": "/tmp/deploy.md"})
    set_world(monkeypatch, make_world(contributor("alpha"), command))
    captured = {}

    def fake_choose_skills(rows, chosen):
        captured["rows"] = rows
        return []

    monkeypatch.setattr(loadout_wizard, "_choose_skills", fake_choose_skills)
    runner.invoke(app, ["loadout", "create", "pack"])
    assert [r.contributor.name for r in captured["rows"]] == ["alpha"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_checks_skill_shell.py tests/test_loadout_wizard.py -v -k command`
Expected: the shell-check tests FAIL because `scan_view` returns `[]` for kind "command" (no findings); the wizard test FAILS because the command row is offered.

- [ ] **Step 3: Implement**

`src/drskill/checks/injection.py`, `scan_view`: change the gate to

```python
    if c.kind not in ("skill", "command"):
        return []
```

and amend the docstring's second sentence to: "MCP tools have no files to scan; skills and command files share the same scannable shape (a primary markdown file plus any bundled files)."

`src/drskill/pipeline.py` (~line 115): the baseline population gate becomes `if c.kind not in ("skill", "command"): continue` (match the existing loop's shape — if it filters inline, extend the condition equivalently).

`src/drskill/cli.py` (~line 910): the prune `valid_keys` comprehension's `c.kind == "skill"` becomes `c.kind in ("skill", "command")`.

`src/drskill/loadout_wizard.py`, `_build_rows`, next to the `if c.system: continue` guard:

```python
        if c.kind == "command":
            continue  # loadouts do not carry slash commands yet
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_checks_skill_shell.py tests/test_loadout_wizard.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/checks/injection.py src/drskill/pipeline.py src/drskill/cli.py src/drskill/loadout_wizard.py tests/test_checks_skill_shell.py tests/test_loadout_wizard.py
git commit -m "feat: run the shell-injection checks over command files"
```

---

### Task 4: Report renders commands

**Files:**
- Modify: `src/drskill/report.py` (~lines 91-126)
- Test: `tests/test_report.py`

**Interfaces:**
- Produces: report rows label command contributors `command` and sort them between skills and mcp tools; everything else about the row (scope, source kind, notes) renders like a skill row.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report.py`, reusing its `make_contributor` (imported from `tests.test_models`), `tables_to_text` (line 169), and the `world_dual_route` construction style (line 464):

```python
def test_command_contributor_renders_with_command_label():
    from drskill.models import Deployment

    c = make_contributor(id="/real/cmds/deploy.md", name="deploy", kind="command")
    c.deployments.append(Deployment(
        harness="pi", path="/real/cmds/deploy.md",
        scope="project", via_symlink=False, order=1000,
    ))
    world = World(
        contributors={c.id: c},
        harnesses={"pi": HarnessDef(id="pi", display_name="Pi")},
    )
    text = tables_to_text(world)
    assert "deploy" in text
    assert "command" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_report.py -v -k command_label`
Expected: FAIL — the row renders with the `skill` label today.

- [ ] **Step 3: Implement**

In `src/drskill/report.py` around lines 120-126, replace the label and sort key:

```python
            kind_rank = {"skill": 0, "command": 1, "mcp_tool": 2}[c.kind]
            kind_label = {"skill": "skill", "command": "command", "mcp_tool": "mcp tool"}[c.kind]
            key = (kind_rank, c.suite or "￿", c.name)
            rows.append((key, _cells(
                c.name, kind_label, d.scope,
                "" if is_tool else c.source.kind, c.suite or "",
                ", ".join(notes), cat, body,
            )))
```

Leave `is_tool` and the deploys-note logic (lines 91-102) unchanged — commands share the skill-side behavior there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/report.py tests/test_report.py
git commit -m "feat: label command contributors in the report"
```

---

### Task 5: disableSkillShellExecution awareness

**Files:**
- Modify: `src/drskill/checks/skill_shell.py`, `src/drskill/resolution.py` (World field), `src/drskill/pipeline.py`
- Test: `tests/test_checks_skill_shell.py`

**Interfaces:**
- Produces: `skill_shell.shell_disabled(project_root: Path, home: Path) -> bool`; `World.shell_execution_disabled: bool = False`; both shell checks append `skill_shell._DISABLED_NOTE` to every finding message when the flag is set. Severities and check outcomes never change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_checks_skill_shell.py`:

```python
def test_disabled_setting_annotates_findings(tmp_path):
    write_skill(tmp_path, "s", "!`make deploy`\n")
    world = make_world(tmp_path)
    world.shell_execution_disabled = True
    findings = run_check("injection-shell-unreviewed", world, Config())
    assert len(findings) == 1
    assert "disabled by Claude Code settings" in findings[0].message
    write_skill(tmp_path, "d", "!`cat ~/.aws/credentials`\n")
    world = make_world(tmp_path)
    world.shell_execution_disabled = True
    dangerous = run_check("injection-shell-dangerous", world, Config())
    assert all("disabled by Claude Code settings" in f.message for f in dangerous)


def test_enabled_setting_leaves_messages_alone(tmp_path):
    write_skill(tmp_path, "s", "!`make deploy`\n")
    findings = run_check("injection-shell-unreviewed", make_world(tmp_path), Config())
    assert "disabled by Claude Code settings" not in findings[0].message


def test_shell_disabled_reads_settings_with_precedence(tmp_path):
    from drskill.checks import skill_shell

    root, home = tmp_path / "proj", tmp_path / "home"
    (root / ".claude").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    assert skill_shell.shell_disabled(root, home) is False
    (home / ".claude" / "settings.json").write_text('{"disableSkillShellExecution": true}')
    assert skill_shell.shell_disabled(root, home) is True
    (root / ".claude" / "settings.json").write_text('{"disableSkillShellExecution": false}')
    assert skill_shell.shell_disabled(root, home) is False
    (root / ".claude" / "settings.local.json").write_text('{"disableSkillShellExecution": true}')
    assert skill_shell.shell_disabled(root, home) is True
    (root / ".claude" / "settings.local.json").write_text("not json")
    assert skill_shell.shell_disabled(root, home) is False
```

(Adapt `write_skill`/`make_world` usage to the helpers' real signatures in that file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_checks_skill_shell.py -v -k disabled`
Expected: FAIL — no `shell_disabled` attribute, no `shell_execution_disabled` field.

- [ ] **Step 3: Implement**

`src/drskill/resolution.py`, in `World` next to `shell_approved`:

```python
    # True when Claude Code settings turn skill shell execution off on this
    # machine; the shell checks annotate but never suppress.
    shell_execution_disabled: bool = False
```

`src/drskill/checks/skill_shell.py`:

```python
_SETTINGS_KEY = "disableSkillShellExecution"
_DISABLED_NOTE = (" Shell execution is disabled by Claude Code settings on this "
                  "machine, so these commands do not run here.")


def shell_disabled(project_root: Path, home: Path) -> bool:
    """True when Claude Code settings disable skill shell execution.
    Precedence mirrors Claude Code's: project settings.local.json, then
    project settings.json, then the user file — the first file that
    states the key wins. Unreadable files are skipped."""
    paths = [project_root / ".claude" / "settings.local.json",
             project_root / ".claude" / "settings.json",
             home / ".claude" / "settings.json"]
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and _SETTINGS_KEY in data:
            return bool(data[_SETTINGS_KEY])
    return False
```

(`import json` if the module lacks it.) Then, in BOTH check functions, at each `Finding(` construction, append the note to the message when the world flag is set. The cleanest shape: compute once per check invocation `suffix = _DISABLED_NOTE if world.shell_execution_disabled else ""` and concatenate `+ suffix` onto each message argument. Every finding either check emits gets the suffix — unreviewed, changed, and each dangerous category.

`src/drskill/pipeline.py`: in `run_scan`, immediately after the block that populates `world.shell_approved`, add:

```python
    world.shell_execution_disabled = skill_shell.shell_disabled(project_root, home)
```

using the module's existing `skill_shell` import (add one if it imports differently).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_checks_skill_shell.py -v`
Expected: PASS, including all pre-existing lifecycle tests (their worlds default to `shell_execution_disabled = False`).

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/checks/skill_shell.py src/drskill/resolution.py src/drskill/pipeline.py tests/test_checks_skill_shell.py
git commit -m "feat: note when settings disable skill shell execution"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the docs**

Two edits:

1. In the "Shell commands in skills" section (~line 409), add one short paragraph: command files under `.claude/commands` (project) and `~/.claude/commands` (personal) use the same shell syntax and now go through the same two checks, and when `disableSkillShellExecution` is set in Claude Code settings the findings say the commands do not run on this machine.
2. In "Known limitations" (~line 505), remove or rewrite the line saying `.claude/commands` files are invisible to the shell checks — that limitation no longer holds. Read the surrounding lines first and keep the section's voice.

- [ ] **Step 2: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS (docs only).

```bash
git add README.md
git commit -m "docs: describe command-file shell coverage"
```
