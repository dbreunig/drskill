# drskill lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `drskill lint [PATH]` command that checks a plugin directory, a skill directory or SKILL.md file, or an MCP config file against its standard and against drskill's existing quality and security checks, with CI-friendly exit codes.

**Architecture:** Lint reuses the scan engine's spine (`World` -> check registry -> `Finding` -> report). A new `lint.py` module classifies the target and builds a `World` without harness deployments. Two new check modules (`checks/plugin_spec.py`, `checks/mcp_spec.py`) cover Agent Plugins 1.0.0 conformance. Each target type runs an explicit list of check ids.

**Tech Stack:** Python 3.11+, pydantic, typer, rich, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-06-agent-plugin-lint-design.md`. Read it before starting any task.

## Global Constraints

- No new dependencies. Schema validation is hand-rolled dict checks, not jsonschema.
- Run tests with `uv run pytest tests/<file> -x -q`. Run the full suite with `uv run pytest -x -q` before each commit.
- Lint makes no LLM calls and no network or subprocess connections unless the user passes `--deep` or `--mcp-connect`.
- Exit codes: 0 clean or below threshold, 1 findings at or above threshold, 2 usage error.
- Severity vocabulary is the existing `error | warning | note`. Spec-fatal violations are `error`, spec non-fatal are `warning`.
- Every new check produces findings whose fingerprints are stable across machines (fingerprint over judged text, never over absolute paths alone).
- Style: `from __future__ import annotations` at top of every new module, pydantic `BaseModel` for data types, match the comment density of neighboring code.

---

### Task 1: PluginManifest and PluginMcpFile models, World fields

**Files:**
- Modify: `src/drskill/models.py` (append after `BrokenSymlink`)
- Modify: `src/drskill/resolution.py:117-132` (the `World` class)
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `models.PluginManifest(root: str, raw: dict, raw_text: str, parse_error: str | None, name: str | None, version: str | None, schema_url: str | None)`; `models.PluginMcpFile(path: str, text: str, data: dict | None, root: str, provisional_root: bool)`; `World.plugin: PluginManifest | None`; `World.plugin_mcp: PluginMcpFile | None`. Later tasks rely on these exact names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_plugin_models_defaults():
    from drskill.models import PluginManifest, PluginMcpFile
    from drskill.resolution import World

    m = PluginManifest(root="/tmp/p")
    assert m.raw == {} and m.parse_error is None and m.name is None
    f = PluginMcpFile(path="/tmp/p/mcp.json", root="/tmp/p")
    assert f.data is None and f.provisional_root is False
    w = World()
    assert w.plugin is None and w.plugin_mcp is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_plugin_models_defaults -x -q`
Expected: FAIL with ImportError on `PluginManifest`.

- [ ] **Step 3: Implement**

Append to `src/drskill/models.py`:

```python
class PluginManifest(BaseModel):
    """A parsed plugin.json from an Agent Plugins 1.0.0 plugin."""

    root: str  # str(resolved plugin root directory)
    raw: dict = Field(default_factory=dict)
    raw_text: str = ""
    parse_error: str | None = None  # JSON error, with line info, when unparseable
    name: str | None = None
    version: str | None = None
    schema_url: str | None = None


class PluginMcpFile(BaseModel):
    """A plugin-flavor mcp.json, kept raw because spec checks need fields
    (type, cwd, env values, headers) that MCPServer normalizes away."""

    path: str
    text: str = ""
    data: dict | None = None  # parsed JSON; None when it failed to parse
    root: str  # plugin root, or the file's parent when linted standalone
    provisional_root: bool = False
```

In `src/drskill/resolution.py`, add to the `World` class after `shell_approved`:

```python
    plugin: PluginManifest | None = None
    plugin_mcp: PluginMcpFile | None = None
```

and add `PluginManifest, PluginMcpFile` to the existing `from drskill.models import ...` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py tests/test_resolution.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/models.py src/drskill/resolution.py tests/test_models.py
git commit -m "feat: plugin manifest and mcp-file models on World"
```

---

### Task 2: Extract make_contributor from build_world

**Files:**
- Modify: `src/drskill/resolution.py:159-222` (`build_world`)
- Test: `tests/test_resolution.py` (append)

**Interfaces:**
- Produces: `resolution.make_contributor(skill_file: Path, scope: str = "project") -> tuple[Contributor | None, list[str]]`. Returns `(None, [])` when the file cannot be read. The second element is the list of unreadable bundled-file paths. Task 4 calls this.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_resolution.py`:

```python
def test_make_contributor_standalone(tmp_path):
    from drskill.resolution import make_contributor

    d = tmp_path / "myskill"
    d.mkdir()
    f = d / "SKILL.md"
    f.write_text("---\nname: myskill\ndescription: Use when testing.\n---\nbody\n")
    c, unreadable = make_contributor(f)
    assert c is not None and c.name == "myskill"
    assert c.deployments == [] and c.scope == "project"
    assert c.token_cost.body_tokens > 0 and unreadable == []
    missing, u2 = make_contributor(tmp_path / "nope" / "SKILL.md")
    assert missing is None and u2 == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_resolution.py::test_make_contributor_standalone -x -q`
Expected: FAIL with ImportError on `make_contributor`.

- [ ] **Step 3: Implement by pure extraction**

In `resolution.py`, move the contributor-construction body of `build_world` (the block from `try: text = real.read_text(...)` through the `Contributor(...)` construction) into:

```python
def make_contributor(
    skill_file: Path, scope: str = "project"
) -> tuple[Contributor | None, list[str]]:
    """Build a Contributor from one skill file, outside any harness.
    Returns (None, []) when the file cannot be read; the second element
    lists bundled files that could not be read."""
    real = skill_file.resolve()
    try:
        text = real.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, []
    fm, raw_fm, body = split_frontmatter(text)
    name = _skill_name(fm, real)
    description = ""
    if fm and isinstance(fm.get("description"), str):
        description = fm["description"]
    provenance = Provenance()
    if fm and GH_PROVENANCE_KEYS & fm.keys():
        provenance = Provenance(kind="gh-skill", source=fm.get("source"))
    elif _in_agents_store(real):
        provenance = Provenance(kind="linked")
    bundled: list[BundledFile] = []
    unreadable: list[str] = []
    if real.name == "SKILL.md":
        bundled, unreadable = collect_bundled_files(real)
    c = Contributor(
        id=str(real),
        name=name,
        scope=scope,
        source=provenance,
        bundled_files=bundled,
        routing_text=description,
        body=body,
        token_cost=TokenCost(
            catalog_tokens=tokens.count(f"{name}: {description}"),
            body_tokens=tokens.count(body),
        ),
        content_hash=content_hash(text),
        frontmatter_valid=fm is not None,
        frontmatter=fm or {},
        frontmatter_text=raw_fm,
    )
    return c, unreadable
```

Rewrite the corresponding block in `build_world` to:

```python
        if c is None:
            c, unreadable_files = make_contributor(inst.skill_file, inst.scope)
            if c is None:
                world.unreadable.append((inst.harness, cid))
                continue
            world.unreadable += [(inst.harness, p) for p in unreadable_files]
            world.contributors[cid] = c
```

- [ ] **Step 4: Run the full suite to verify no behavior change**

Run: `uv run pytest -x -q`
Expected: PASS, same count as before the change.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/resolution.py tests/test_resolution.py
git commit -m "refactor: extract make_contributor from build_world"
```

---

### Task 3: Target classification in lint.py

**Files:**
- Create: `src/drskill/lint.py`
- Test: `tests/test_lint.py` (create)

**Interfaces:**
- Produces: `lint.LintUsageError(Exception)`; `lint.LintTarget(kind: Literal["plugin","skill","mcp"], path: Path, mcp_flavor: Literal["agent-plugins","harness"] | None)`; `lint.classify(path: Path, forced: str | None = None) -> LintTarget`. Task 9's CLI catches `LintUsageError` and exits 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lint.py`:

```python
import json
from pathlib import Path

import pytest

from drskill.lint import LintUsageError, classify


def make_plugin(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "demo-plugin",
    }))


def test_classify_plugin_dir(tmp_path):
    make_plugin(tmp_path / "p")
    t = classify(tmp_path / "p")
    assert t.kind == "plugin"


def test_classify_skill_dir_and_file(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nb\n")
    assert classify(d).kind == "skill"
    assert classify(d / "SKILL.md").kind == "skill"


def test_plugin_wins_over_skill(tmp_path):
    d = tmp_path / "both"
    make_plugin(d)
    (d / "SKILL.md").write_text("---\nname: both\ndescription: d\n---\nb\n")
    assert classify(d).kind == "plugin"


def test_classify_mcp_agent_plugins_flavor(tmp_path):
    f = tmp_path / "anything.json"
    f.write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {},
    }))
    t = classify(f)
    assert t.kind == "mcp" and t.mcp_flavor == "agent-plugins"


def test_classify_mcp_next_to_plugin_json(tmp_path):
    make_plugin(tmp_path)
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {}}))
    assert classify(f).mcp_flavor == "agent-plugins"


def test_classify_mcp_harness_flavor(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"s": {"command": "srv"}}}))
    t = classify(f)
    assert t.kind == "mcp" and t.mcp_flavor == "harness"


def test_classify_unparseable_mcp_json_still_mcp(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text("{not json")
    assert classify(f).kind == "mcp"


def test_classify_rejects_unknown(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(LintUsageError):
        classify(d)
    with pytest.raises(LintUsageError):
        classify(tmp_path / "missing")


def test_forced_type_overrides(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nb\n")
    make_plugin(d)
    assert classify(d, forced="skill").kind == "skill"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: FAIL with ModuleNotFoundError on `drskill.lint`.

- [ ] **Step 3: Implement**

Create `src/drskill/lint.py`:

```python
"""drskill lint: check one authorable unit (an Agent Plugins plugin, a
skill, or an MCP config file) against its standard and drskill's checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_ACCEPTS = (
    "drskill lint takes a plugin directory (with plugin.json), a skill "
    "directory or SKILL.md file, or an MCP config JSON file"
)
_MCP_SCHEMA_RE = re.compile(r"agent-plugins\.org/schemas/[^/]+/mcp\.schema\.json$")


class LintUsageError(Exception):
    pass


class LintTarget(BaseModel):
    kind: Literal["plugin", "skill", "mcp"]
    path: Path
    mcp_flavor: Literal["agent-plugins", "harness"] | None = None


def classify(path: Path, forced: str | None = None) -> LintTarget:
    p = path.expanduser()
    if not p.exists():
        raise LintUsageError(f"{path} does not exist; {_ACCEPTS}")
    if forced == "plugin":
        if not (p.is_dir() and (p / "plugin.json").is_file()):
            raise LintUsageError(f"{path} is not a plugin directory (no plugin.json)")
        return LintTarget(kind="plugin", path=p)
    if forced == "skill":
        f = p if p.is_file() else p / "SKILL.md"
        if not f.is_file():
            raise LintUsageError(f"{path} is not a skill (no SKILL.md)")
        return LintTarget(kind="skill", path=p)
    if forced == "mcp":
        if not p.is_file():
            raise LintUsageError(f"{path} is not an MCP config file")
        return _classify_json(p)
    if p.is_dir():
        if (p / "plugin.json").is_file():
            return LintTarget(kind="plugin", path=p)
        if (p / "SKILL.md").is_file():
            return LintTarget(kind="skill", path=p)
        raise LintUsageError(f"{path} has no plugin.json or SKILL.md; {_ACCEPTS}")
    if p.name == "SKILL.md":
        return LintTarget(kind="skill", path=p)
    if p.suffix == ".json" or p.name.startswith("."):
        return _classify_json(p)
    raise LintUsageError(f"{path} is not a lintable file; {_ACCEPTS}")


def _classify_json(p: Path) -> LintTarget:
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        data = None
    sibling_manifest = (p.parent / "plugin.json").is_file()
    schema = data.get("$schema") if isinstance(data, dict) else None
    if isinstance(schema, str) and _MCP_SCHEMA_RE.search(schema):
        return LintTarget(kind="mcp", path=p, mcp_flavor="agent-plugins")
    if sibling_manifest and p.name == "mcp.json":
        return LintTarget(kind="mcp", path=p, mcp_flavor="agent-plugins")
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        return LintTarget(kind="mcp", path=p, mcp_flavor="harness")
    if data is None and p.name in ("mcp.json", ".mcp.json"):
        flavor = "agent-plugins" if sibling_manifest else "harness"
        return LintTarget(kind="mcp", path=p, mcp_flavor=flavor)
    raise LintUsageError(f"{p} is not a recognized MCP config; {_ACCEPTS}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/lint.py tests/test_lint.py
git commit -m "feat: lint target classification"
```

---

### Task 4: build_lint_world

**Files:**
- Modify: `src/drskill/lint.py`
- Test: `tests/test_lint.py` (append)

**Interfaces:**
- Consumes: `resolution.make_contributor` (Task 2), `models.PluginManifest` / `models.PluginMcpFile` (Task 1), `mcp._servers_from_map(data, harness, scope, source, in_project)` and `discovery._find_broken_symlinks(base, recursive)` (existing).
- Produces: `lint.build_lint_world(target: LintTarget) -> World`. Plugin worlds have `world.plugin` set, skill contributors keyed by resolved path, `world.plugin_mcp` set when an agent-plugins mcp.json exists, `world.mcp_servers` populated for both flavors with `harness=""` and `in_project=True`, parse failures recorded in `world.mcp_config_errors` as `("", path, msg, True)` and in `manifest.parse_error`. Checks in Tasks 6-7 read these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lint.py`:

```python
from drskill.lint import build_lint_world


def write_skill(d: Path, name: str):
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing {name}.\n---\nbody\n"
    )


def test_build_world_skill_target(tmp_path):
    write_skill(tmp_path / "s", "s")
    w = build_lint_world(classify(tmp_path / "s"))
    assert len(w.contributors) == 1
    c = next(iter(w.contributors.values()))
    assert c.name == "s" and c.deployments == []
    assert w.plugin is None


def test_build_world_plugin_target(tmp_path):
    root = tmp_path / "p"
    make_plugin(root)
    write_skill(root / "skills" / "alpha", "alpha")
    write_skill(root / "skills" / "beta", "beta")
    # nested too deep: not discovered as a contributor
    write_skill(root / "skills" / "group" / "gamma", "gamma")
    (root / "mcp.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"srv": {"type": "stdio", "command": "server-bin",
                               "env": {"API_KEY": "sk-live-1234567890abcdef"}}},
    }))
    w = build_lint_world(classify(root))
    assert w.plugin is not None and w.plugin.name == "demo-plugin"
    assert sorted(c.name for c in w.contributors.values()) == ["alpha", "beta"]
    assert len(w.mcp_servers) == 1
    s = w.mcp_servers[0]
    assert s.harness == "" and s.in_project is True
    assert w.plugin_mcp is not None and w.plugin_mcp.data is not None


def test_build_world_bad_manifest_and_bad_mcp(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "plugin.json").write_text("{broken")
    (root / "mcp.json").write_text("{also broken")
    w = build_lint_world(classify(root))
    assert w.plugin.parse_error is not None
    assert w.plugin_mcp.data is None
    assert len(w.mcp_config_errors) == 1


def test_build_world_standalone_mcp_provisional_root(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {},
    }))
    w = build_lint_world(classify(f))
    assert w.plugin_mcp.provisional_root is True
    assert w.plugin_mcp.root == str(tmp_path.resolve())


def test_build_world_harness_mcp(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"a": {"command": "foo"}}}))
    w = build_lint_world(classify(f))
    assert w.plugin_mcp is None
    assert [s.name for s in w.mcp_servers] == ["a"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: FAIL with ImportError on `build_lint_world`.

- [ ] **Step 3: Implement**

Append to `src/drskill/lint.py`:

```python
from drskill.discovery import _find_broken_symlinks
from drskill.mcp import _servers_from_map
from drskill.models import BrokenSymlink, PluginManifest, PluginMcpFile
from drskill.resolution import World, make_contributor


def build_lint_world(target: LintTarget) -> World:
    world = World()
    if target.kind == "skill":
        f = target.path if target.path.is_file() else target.path / "SKILL.md"
        _add_skill(world, f)
    elif target.kind == "plugin":
        root = target.path.resolve()
        world.plugin = _parse_manifest(root)
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            for child in sorted(skills_dir.iterdir()):
                f = child / "SKILL.md"
                if child.is_dir() and f.is_file():
                    _add_skill(world, f)
        mcp_path = root / "mcp.json"
        if mcp_path.is_file():
            _add_mcp_file(world, mcp_path, root, provisional=False)
    else:
        p = target.path.resolve()
        if target.mcp_flavor == "agent-plugins":
            root = p.parent
            provisional = not (root / "plugin.json").is_file()
            _add_mcp_file(world, p, root, provisional)
        else:
            _add_harness_mcp(world, p)
    return world


def _add_skill(world: World, skill_file: Path) -> None:
    c, unreadable = make_contributor(skill_file)
    if c is None:
        world.unreadable.append(("", str(skill_file)))
        return
    world.unreadable += [("", p) for p in unreadable]
    world.contributors[c.id] = c
    for b in _find_broken_symlinks(skill_file.parent, recursive=True):
        world.broken_symlinks.append(BrokenSymlink(harness="", path=b))


def _parse_manifest(root: Path) -> PluginManifest:
    path = root / "plugin.json"
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return PluginManifest(root=str(root), raw_text=text, parse_error=str(e))
    if not isinstance(raw, dict):
        return PluginManifest(
            root=str(root), raw_text=text, parse_error="top level is not an object"
        )
    return PluginManifest(
        root=str(root),
        raw=raw,
        raw_text=text,
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        version=raw.get("version") if isinstance(raw.get("version"), str) else None,
        schema_url=raw.get("$schema") if isinstance(raw.get("$schema"), str) else None,
    )


def _add_mcp_file(world: World, path: Path, root: Path, provisional: bool) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        world.mcp_config_errors.append(("", str(path), str(e), True))
        world.plugin_mcp = PluginMcpFile(
            path=str(path), text=text, root=str(root), provisional_root=provisional
        )
        return
    world.plugin_mcp = PluginMcpFile(
        path=str(path), text=text, data=data if isinstance(data, dict) else None,
        root=str(root), provisional_root=provisional,
    )
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        world.mcp_servers = _servers_from_map(
            servers, harness="", scope="project", source=path, in_project=True
        )


def _add_harness_mcp(world: World, path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        world.mcp_config_errors.append(("", str(path), str(e), True))
        return
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        world.mcp_servers = _servers_from_map(
            servers, harness="", scope="project", source=path, in_project=True
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/lint.py tests/test_lint.py
git commit -m "feat: build a lint world from plugin, skill, and mcp targets"
```

---

### Task 5: run_checks and the lint check suites

**Files:**
- Modify: `src/drskill/checks/__init__.py:64-80` (`run_all`)
- Modify: `src/drskill/lint.py`
- Test: `tests/test_lint.py` (append)

**Interfaces:**
- Produces: `checks.run_checks(world, config, ids: list[str], progress=None) -> list[Finding]` which runs only the named registered checks and applies the same fingerprint-merge as `run_all`; `run_all` becomes a thin wrapper over it. In `lint.py`: `SKILL_CONTENT_CHECKS`, `PLUGIN_SPEC_CHECKS`, `MCP_SPEC_CHECKS`, `MCP_STATIC_CHECKS`, `MCP_CONNECT_CHECKS`, and `checks_for(target: LintTarget, mcp_connect: bool) -> list[str]`. Task 8 calls `checks_for` + `run_checks`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lint.py`:

```python
def test_suites_only_name_registered_checks():
    from drskill.checks import REGISTRY, run_all  # noqa: F401  (run_all imports all modules)
    from drskill.resolution import World
    from drskill.ledger import Config
    from drskill import lint as lint_mod

    run_all(World(), Config())  # force-register every check module
    # plugin_spec / mcp_spec ids only exist after Tasks 6-7; tolerate both
    # phases by checking the suites that must already resolve.
    assert set(lint_mod.SKILL_CONTENT_CHECKS) <= set(REGISTRY)
    assert set(lint_mod.MCP_STATIC_CHECKS) <= set(REGISTRY)
    assert set(lint_mod.MCP_CONNECT_CHECKS) <= set(REGISTRY)


def test_run_checks_runs_only_named(tmp_path):
    from drskill.checks import run_checks
    from drskill.ledger import Config

    write_skill(tmp_path / "s", "other-name")  # folder 's', name mismatch
    w = build_lint_world(classify(tmp_path / "s"))
    findings = run_checks(w, Config(), ["spec-name-mismatch"])
    assert {f.check_id for f in findings} == {"spec-name-mismatch"}


def test_checks_for_shapes():
    from drskill.lint import LintTarget, checks_for, SKILL_CONTENT_CHECKS

    skill = LintTarget(kind="skill", path=Path("."))
    assert checks_for(skill, mcp_connect=False) == SKILL_CONTENT_CHECKS
    plug = LintTarget(kind="plugin", path=Path("."))
    ids = checks_for(plug, mcp_connect=False)
    assert "exact-duplicate" in ids and "mcp-secret-in-config" in ids
    assert "name-shadow" not in ids and "lockfile-drift" not in ids
    harness = LintTarget(kind="mcp", path=Path("."), mcp_flavor="harness")
    ids = checks_for(harness, mcp_connect=False)
    assert "mcp-dead-server" in ids and "mcp-insecure-url" in ids
    agent = LintTarget(kind="mcp", path=Path("."), mcp_flavor="agent-plugins")
    assert "mcp-dead-server" not in checks_for(agent, mcp_connect=False)
    assert "mcp-tool-poisoning" in checks_for(agent, mcp_connect=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: FAIL with ImportError on `run_checks` / `SKILL_CONTENT_CHECKS`.

- [ ] **Step 3: Implement**

In `checks/__init__.py`, restructure `run_all` into:

```python
def run_checks(
    world: World, config: Config, ids: list[str], progress=None
) -> list[Finding]:
    # Import registers every check module exactly once.
    from drskill.checks import budget, duplicates, filesystem, heuristics, injection, lockfile, mcp, mcp_injection, mcp_tools, shadowing, skill_shell, spec  # noqa: F401

    findings: list[Finding] = []
    for check_id in ids:
        fn = REGISTRY[check_id]
        if progress:
            progress(f"checking {check_id}")
        findings.extend(fn(world, config))
```

followed by the existing fingerprint-merge block, and make `run_all` call `run_checks(world, config, list(REGISTRY), progress)` after the module import line (import first so `list(REGISTRY)` is complete).

In `lint.py` add (plugin_spec / mcp_spec ids are defined here now and registered by Tasks 6-7; `run_checks` will raise KeyError until then, which Task 6's Step 2 relies on):

```python
SKILL_CONTENT_CHECKS = [
    "spec-invalid-frontmatter", "spec-name-mismatch", "spec-missing-description",
    "spec-description-too-long", "frontmatter-angle-brackets",
    "missing-activation", "generic-description", "opposing-imperatives",
    "description-overlap",
    "budget-catalog-tokens", "budget-body-tokens",
    "injection-unicode", "injection-encoded-blob", "injection-override",
    "injection-remote-fetch", "injection-egress", "injection-credential-read",
    "injection-mandatory-script",
    "injection-shell-unreviewed", "injection-shell-dangerous",
    "broken-symlink", "unreadable-skill",
]
PLUGIN_SPEC_CHECKS = [
    "plugin-manifest-invalid", "plugin-name-invalid",
    "plugin-manifest-unknown-field", "plugin-schema-unknown",
    "plugin-skill-undiscoverable", "plugin-path-escape",
    "plugin-extension-hygiene",
]
MCP_SPEC_CHECKS = ["mcp-spec-invalid", "mcp-spec-placeholder"]
MCP_STATIC_CHECKS = ["mcp-config-invalid", "mcp-secret-in-config", "mcp-unpinned-server"]
MCP_CONNECT_CHECKS = [
    "mcp-connect-failed", "mcp-tool-collision", "mcp-tools-unreviewed",
    "mcp-tool-poisoning",
]


def checks_for(target: LintTarget, mcp_connect: bool) -> list[str]:
    if target.kind == "skill":
        return list(SKILL_CONTENT_CHECKS)
    if target.kind == "plugin":
        ids = (SKILL_CONTENT_CHECKS + ["exact-duplicate", "near-duplicate"]
               + PLUGIN_SPEC_CHECKS + MCP_SPEC_CHECKS + MCP_STATIC_CHECKS)
    elif target.mcp_flavor == "agent-plugins":
        ids = MCP_SPEC_CHECKS + MCP_STATIC_CHECKS
    else:
        # No spec to enforce; the generic URL and dead-command checks stand in.
        ids = MCP_STATIC_CHECKS + ["mcp-insecure-url", "mcp-dead-server"]
    if mcp_connect:
        ids = ids + MCP_CONNECT_CHECKS
    return ids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lint.py tests/test_checks_spec.py -x -q` then `uv run pytest -x -q`
Expected: PASS (run_all behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/__init__.py src/drskill/lint.py tests/test_lint.py
git commit -m "feat: run_checks subset runner and lint check suites"
```

---

### Task 6: checks/plugin_spec.py

**Files:**
- Create: `src/drskill/checks/plugin_spec.py`
- Modify: `src/drskill/checks/__init__.py` (add `plugin_spec` to the import line in `run_checks`)
- Test: `tests/test_checks_plugin_spec.py` (create)

**Interfaces:**
- Consumes: `world.plugin: PluginManifest | None` (Task 1/4). Every check returns `[]` when `world.plugin is None`, so scan is unaffected.
- Produces: registered check ids `plugin-manifest-invalid` (error), `plugin-name-invalid` (error), `plugin-manifest-unknown-field` (warning), `plugin-schema-unknown` (warning), `plugin-skill-undiscoverable` (warning), `plugin-path-escape` (error), `plugin-extension-hygiene` (warning). Also `valid_plugin_name(name: str) -> bool` for tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checks_plugin_spec.py`:

```python
import json
import os
from pathlib import Path

from drskill.checks import run_checks
from drskill.checks.plugin_spec import valid_plugin_name
from drskill.ledger import Config
from drskill.lint import PLUGIN_SPEC_CHECKS, build_lint_world, classify


def run(root: Path):
    return run_checks(build_lint_world(classify(root)), Config(), PLUGIN_SPEC_CHECKS)


def by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check_id, []).append(f)
    return out


def plugin(tmp_path, manifest: dict | str) -> Path:
    root = tmp_path / "p"
    root.mkdir(exist_ok=True)
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (root / "plugin.json").write_text(text)
    return root


GOOD = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "demo-plugin",
    "version": "1.0.0",
}


def test_name_rules():
    assert valid_plugin_name("my-plugin") and valid_plugin_name("acme.tools")
    assert valid_plugin_name("a") and valid_plugin_name("lint3r")
    for bad in ("", "-x", "x-", ".x", "x.", "a--b", "a..b", "a.-b", "UPPER",
                "has space", "x" * 65):
        assert not valid_plugin_name(bad), bad


def test_clean_plugin_no_findings(tmp_path):
    root = plugin(tmp_path, GOOD)
    assert run(root) == []


def test_unparseable_manifest_is_error(tmp_path):
    root = plugin(tmp_path, "{broken")
    got = by_check(run(root))
    assert got["plugin-manifest-invalid"][0].severity == "error"


def test_missing_required_fields(tmp_path):
    root = plugin(tmp_path, {"description": "no schema, no name"})
    msgs = " ".join(f.message for f in by_check(run(root))["plugin-manifest-invalid"])
    assert "$schema" in msgs and "name" in msgs


def test_bad_name_is_error(tmp_path):
    root = plugin(tmp_path, {**GOOD, "name": "Bad--Name"})
    assert by_check(run(root))["plugin-name-invalid"][0].severity == "error"


def test_unknown_field_and_bad_extensions_warn(tmp_path):
    root = plugin(tmp_path, {**GOOD, "surprise": 1, "extensions": "nope"})
    got = by_check(run(root))
    assert len(got["plugin-manifest-unknown-field"]) == 2
    assert got["plugin-manifest-unknown-field"][0].severity == "warning"


def test_unknown_schema_version_warns(tmp_path):
    root = plugin(tmp_path, {**GOOD,
        "$schema": "https://agent-plugins.org/schemas/9.9.9/plugin.schema.json"})
    assert "plugin-schema-unknown" in by_check(run(root))


def test_undiscoverable_skills_warn(tmp_path):
    root = plugin(tmp_path, GOOD)
    deep = root / "skills" / "group" / "nested"
    deep.mkdir(parents=True)
    (deep / "SKILL.md").write_text("---\nname: nested\ndescription: d\n---\nb\n")
    (root / "skills" / "empty-child").mkdir()
    got = by_check(run(root))
    assert len(got["plugin-skill-undiscoverable"]) == 2


def test_symlink_escape_is_error(tmp_path):
    root = plugin(tmp_path, GOOD)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (root / "skills").mkdir()
    os.symlink(outside, root / "skills" / "leak")
    got = by_check(run(root))
    assert got["plugin-path-escape"][0].severity == "error"


def test_extension_hygiene(tmp_path):
    root = plugin(tmp_path, GOOD)
    # invalid namespace: looks like a namespace (has a dot) but bad label
    (root / "com..bad").mkdir()
    # secret inside a valid namespace dir
    ns = root / "com.example.client"
    ns.mkdir()
    (ns / "settings.json").write_text(json.dumps(
        {"api_key": "sk-live-1234567890abcdef"}))
    # namespace dir shadowing portable components
    shadow = root / "com.example.other"
    (shadow / "skills" / "s").mkdir(parents=True)
    (shadow / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\nb\n")
    got = by_check(run(root))
    assert len(got["plugin-extension-hygiene"]) == 3
    assert all(f.severity == "warning" for f in got["plugin-extension-hygiene"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks_plugin_spec.py -x -q`
Expected: FAIL with ModuleNotFoundError on `drskill.checks.plugin_spec` (and KeyError from `run_checks` if run via lint suites).

- [ ] **Step 3: Implement**

Create `src/drskill/checks/plugin_spec.py`:

```python
"""Agent Plugins 1.0.0 conformance checks for plugin.json and the plugin
layout. Every check no-ops when world.plugin is None, so scan never runs
them against harness skills."""

from __future__ import annotations

import json
import re
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.mcp import looks_secret
from drskill.models import Finding
from drskill.resolution import World

SUPPORTED_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
KNOWN_FIELDS = {
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
}
_NAMESPACE_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
# Extension secret scan cap: skip files larger than this.
_SECRET_SCAN_CAP = 256 * 1024


def valid_plugin_name(name: str) -> bool:
    if not 1 <= len(name) <= 64:
        return False
    if not re.fullmatch(r"[a-z0-9.-]+", name):
        return False
    if name[0] in ".-" or name[-1] in ".-":
        return False
    return re.search(r"[.-]{2}", name) is None


def _pf(check_id, severity, world, message, fix=None, key=""):
    return make_finding(
        check_id, severity, [], message, fix_commands=fix or [],
        extra_key=key, fingerprint_texts=[world.plugin.raw_text or key],
    )


@check("plugin-manifest-invalid")
def manifest_invalid(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    where = f"{m.root}/plugin.json"
    if m.parse_error:
        return [_pf(
            "plugin-manifest-invalid", "error", world,
            f"plugin.json does not parse: {m.parse_error}",
            fix=[f"Fix the JSON syntax in {where}"],
        )]
    out = []
    if not isinstance(m.raw.get("$schema"), str):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json is missing the required $schema string; clients "
            "reject the whole plugin",
            fix=[f'Add "$schema": "{SUPPORTED_SCHEMA}" to {where}'], key="$schema"))
    if not isinstance(m.raw.get("name"), str):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json is missing the required name string; clients "
            "reject the whole plugin",
            fix=[f"Add a name field to {where}"], key="name"))
    for field in ("version", "description", "homepage", "repository", "license"):
        v = m.raw.get(field)
        if v is not None and not isinstance(v, str):
            out.append(_pf("plugin-manifest-invalid", "error", world,
                f"plugin.json field '{field}' must be a string",
                fix=[f"Make '{field}' a string in {where}"], key=field))
    kw = m.raw.get("keywords")
    if kw is not None and not (
        isinstance(kw, list) and all(isinstance(k, str) for k in kw)
    ):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json field 'keywords' must be a list of strings",
            fix=[f"Fix 'keywords' in {where}"], key="keywords"))
    author = m.raw.get("author")
    if author is not None and not isinstance(author, dict):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json field 'author' must be an object",
            fix=[f"Fix 'author' in {where}"], key="author"))
    return out


@check("plugin-name-invalid")
def name_invalid(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None or m.name is None:
        return []
    if valid_plugin_name(m.name):
        return []
    return [_pf(
        "plugin-name-invalid", "error", world,
        f"plugin name '{m.name}' breaks the naming rules: 1 to 64 lowercase "
        "letters, digits, hyphens, or periods; first and last alphanumeric; "
        "no doubled separators",
        fix=[f"Rename the plugin in {m.root}/plugin.json"], key=m.name,
    )]


@check("plugin-manifest-unknown-field")
def unknown_field(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None or m.parse_error:
        return []
    out = []
    for field in sorted(set(m.raw) - KNOWN_FIELDS):
        out.append(_pf("plugin-manifest-unknown-field", "warning", world,
            f"plugin.json has an unknown top level field '{field}'; clients "
            "ignore it", key=field))
    if "extensions" in m.raw and not isinstance(m.raw["extensions"], dict):
        out.append(_pf("plugin-manifest-unknown-field", "warning", world,
            "plugin.json 'extensions' is not an object; clients ignore it",
            key="extensions"))
    return out


@check("plugin-schema-unknown")
def schema_unknown(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None or m.schema_url is None or m.schema_url == SUPPORTED_SCHEMA:
        return []
    return [_pf(
        "plugin-schema-unknown", "warning", world,
        f"plugin.json declares '{m.schema_url}'; drskill validates against "
        "1.0.0", key=m.schema_url,
    )]


@check("plugin-skill-undiscoverable")
def skill_undiscoverable(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    skills_dir = Path(m.root) / "skills"
    if not skills_dir.is_dir():
        return []
    out = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir() and not (child / "SKILL.md").is_file():
            rel = child.relative_to(m.root)
            out.append(_pf("plugin-skill-undiscoverable", "warning", world,
                f"'{rel}' has no SKILL.md, so clients do not load it as a "
                "skill; was that intended?", key=str(rel)))
    for f in sorted(skills_dir.rglob("SKILL.md")):
        if f.parent.parent != skills_dir:
            rel = f.relative_to(m.root)
            out.append(_pf("plugin-skill-undiscoverable", "warning", world,
                f"'{rel}' is nested too deep; clients only discover "
                "skills/<name>/SKILL.md", key=str(rel)))
    return out


@check("plugin-path-escape")
def path_escape(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    root = Path(m.root).resolve()
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_symlink():
            continue
        real = p.resolve()
        if not real.is_relative_to(root):
            rel = p.relative_to(root)
            out.append(_pf("plugin-path-escape", "error", world,
                f"'{rel}' resolves outside the plugin root to {real}; "
                "clients must reject paths that escape the root",
                fix=[f"Replace the symlink {rel} with a file inside the plugin"],
                key=str(rel)))
    return out


@check("plugin-extension-hygiene")
def extension_hygiene(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    root = Path(m.root)
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if d.name == "skills" or d.name.startswith("."):
            continue
        if "." not in d.name:
            continue  # a plain dir (docs, tests) is not an attempted namespace
        if not _NAMESPACE_RE.fullmatch(d.name):
            out.append(_pf("plugin-extension-hygiene", "warning", world,
                f"'{d.name}/' looks like a client extension directory but is "
                "not a valid reverse domain namespace; clients ignore it",
                key=d.name))
            continue
        if (d / "mcp.json").is_file() or any(d.glob("skills/*/SKILL.md")):
            out.append(_pf("plugin-extension-hygiene", "warning", world,
                f"'{d.name}/' contains skills/ or mcp.json; portable "
                "components load only from the plugin root, so these load "
                "for no client unless that namespace defines them",
                key=f"{d.name}:shadow"))
        for jf in sorted(d.rglob("*.json")):
            if jf.stat().st_size > _SECRET_SCAN_CAP:
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            hits = sorted(_secret_keys(data))
            if hits:
                rel = jf.relative_to(root)
                out.append(_pf("plugin-extension-hygiene", "warning", world,
                    f"'{rel}' holds credential-shaped values: {', '.join(hits)}",
                    fix=[f"Move secrets out of {rel}; plugins are published"],
                    key=str(rel)))
    return out


def _secret_keys(data, prefix=""):
    hits = set()
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str) and looks_secret(str(k), v):
                hits.add(str(k))
            else:
                hits |= _secret_keys(v, prefix)
    elif isinstance(data, list):
        for item in data:
            hits |= _secret_keys(item, prefix)
    return hits
```

Then add `plugin_spec` to the check-module import line inside `run_checks` in `checks/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_checks_plugin_spec.py -x -q` then `uv run pytest -x -q`
Expected: PASS; scan tests unaffected (all new checks no-op without `world.plugin`).

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/plugin_spec.py src/drskill/checks/__init__.py tests/test_checks_plugin_spec.py
git commit -m "feat: agent-plugins plugin.json and layout conformance checks"
```

---

### Task 7: checks/mcp_spec.py

**Files:**
- Create: `src/drskill/checks/mcp_spec.py`
- Modify: `src/drskill/checks/__init__.py` (add `mcp_spec` to the import line)
- Test: `tests/test_checks_mcp_spec.py` (create)

**Interfaces:**
- Consumes: `world.plugin_mcp: PluginMcpFile | None` (Task 1/4). Both checks return `[]` when it is None or its `data` is None (parse errors are `mcp-config-invalid`'s job).
- Produces: registered check ids `mcp-spec-invalid` (error) and `mcp-spec-placeholder` (error, with one warning case for headers).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checks_mcp_spec.py`:

```python
import json
from pathlib import Path

from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.lint import MCP_SPEC_CHECKS, build_lint_world, classify

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def run(tmp_path: Path, servers: dict, schema: str | None = SCHEMA,
        make_files: list[str] | None = None):
    data: dict = {"mcpServers": servers}
    if schema:
        data["$schema"] = schema
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps(data))
    for rel in make_files or []:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n")
    w = build_lint_world(classify(f))
    return run_checks(w, Config(), MCP_SPEC_CHECKS)


def ids(findings):
    return sorted(f.check_id for f in findings)


def test_clean_stdio_and_http(tmp_path):
    got = run(tmp_path, {
        "local": {"type": "stdio", "command": "./bin/run",
                  "args": ["--data", "${PLUGIN_DATA}"], "cwd": "${PLUGIN_ROOT}/srv"},
        "remote": {"type": "streamable-http", "url": "https://api.example.com/mcp"},
    }, make_files=["bin/run"])
    assert got == []


def test_missing_schema_and_bad_transport(tmp_path):
    got = run(tmp_path, {"s": {"type": "websocket", "url": "wss://x"}}, schema=None)
    msgs = " ".join(f.message for f in got)
    assert all(f.check_id == "mcp-spec-invalid" for f in got)
    assert "$schema" in msgs and "websocket" in msgs


def test_stdio_command_rules(tmp_path):
    got = run(tmp_path, {
        "multi": {"type": "stdio", "command": "python -m srv"},
        "abs": {"type": "stdio", "command": "/usr/bin/srv"},
        "missing-rel": {"type": "stdio", "command": "./bin/gone"},
    })
    assert len(got) == 3 and all(f.severity == "error" for f in got)


def test_url_rules(tmp_path):
    got = run(tmp_path, {
        "userinfo": {"type": "sse", "url": "https://u:p@example.com/mcp"},
        "fragment": {"type": "streamable-http", "url": "https://example.com/mcp#frag"},
        "plain-http": {"type": "streamable-http", "url": "http://example.com/mcp"},
        "loopback-ok": {"type": "streamable-http", "url": "http://127.0.0.1:8000/mcp"},
    })
    assert len(got) == 3


def test_placeholder_rules(tmp_path):
    got = run(tmp_path, {
        "in-command": {"type": "stdio", "command": "${PLUGIN_ROOT}/bin/run"},
        "env-key": {"type": "stdio", "command": "srv",
                    "env": {"PLUGIN_ROOT": "/x", "${PLUGIN_DATA}": "y"}},
        "bad-cwd": {"type": "stdio", "command": "srv", "cwd": "/absolute"},
    })
    assert "mcp-spec-placeholder" in ids(got)
    assert sum(1 for f in got if f.check_id == "mcp-spec-placeholder") >= 3


def test_header_placeholder_warns(tmp_path):
    got = run(tmp_path, {
        "h": {"type": "streamable-http", "url": "https://example.com/mcp",
              "headers": {"X-Root": "${PLUGIN_ROOT}"}},
    })
    assert [f.severity for f in got] == ["warning"]


def test_standalone_notes_provisional_root(tmp_path):
    got = run(tmp_path, {"missing-rel": {"type": "stdio", "command": "./gone"}})
    assert "assuming" in got[0].message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks_mcp_spec.py -x -q`
Expected: FAIL with ModuleNotFoundError on `drskill.checks.mcp_spec`.

- [ ] **Step 3: Implement**

Create `src/drskill/checks/mcp_spec.py`:

```python
"""Agent Plugins 1.0.0 conformance checks for plugin-flavor mcp.json.
These run off the raw parsed file (world.plugin_mcp) because MCPServer
normalizes away the fields the spec constrains (type, cwd, env values,
headers). Parse failures are mcp-config-invalid's job, not ours."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from drskill.checks import check
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

TRANSPORTS = {"stdio", "streamable-http", "sse"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_PLACEHOLDER_RE = re.compile(r"\$\{(PLUGIN_ROOT|PLUGIN_DATA)\}")
_CWD_RE = re.compile(r"^(\./|\$\{PLUGIN_ROOT\}(/|$)|\$\{PLUGIN_DATA\}(/|$))")


def _finding(check_id, severity, world, name, entry, message, fix=None):
    text = json.dumps({name: entry}, sort_keys=True)
    payload = "|".join([check_id, hashlib.sha256(text.encode()).hexdigest(), message])
    return Finding(
        check_id=check_id, severity=severity,
        contributors=[world.plugin_mcp.path], contributor_names=[name],
        harnesses=[], message=message, fix_commands=fix or [],
        fingerprint="sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
    )


def _entries(world: World):
    f = world.plugin_mcp
    if f is None or f.data is None:
        return None, []
    servers = f.data.get("mcpServers")
    if not isinstance(servers, dict):
        return f, None
    return f, sorted((str(k), v) for k, v in servers.items() if isinstance(v, dict))


@check("mcp-spec-invalid")
def spec_invalid(world: World, config: Config) -> list[Finding]:
    f, entries = _entries(world)
    if f is None:
        return []
    out = []
    root_note = ""
    if f.provisional_root:
        root_note = f" (assuming {f.root} as the plugin root; no plugin.json found)"
    if not isinstance(f.data.get("$schema"), str):
        out.append(_finding("mcp-spec-invalid", "error", world, "mcp.json",
            f.data, f"{f.path} is missing the required $schema string"))
    if entries is None:
        out.append(_finding("mcp-spec-invalid", "error", world, "mcp.json",
            f.data, f"{f.path} 'mcpServers' must be an object"))
        return out
    for name, entry in entries:
        t = entry.get("type")
        if t not in TRANSPORTS:
            out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
                f"server '{name}' has transport '{t}'; the spec allows stdio, "
                "streamable-http, or sse, and clients skip this entry"))
            continue
        if t == "stdio":
            out += _check_stdio(world, name, entry, Path(f.root), root_note)
        else:
            out += _check_url(world, name, entry)
    return out


def _check_stdio(world, name, entry, root: Path, root_note: str):
    out = []
    cmd = entry.get("command")
    if not isinstance(cmd, str) or not cmd:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"stdio server '{name}' is missing a command"))
        return out
    if any(ch.isspace() for ch in cmd):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' command '{cmd}' is not a single token; put "
            "arguments in 'args'"))
    elif cmd.startswith("./"):
        if not (root / cmd[2:]).exists():
            out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
                f"server '{name}' command '{cmd}' does not exist inside the "
                f"plugin{root_note}"))
    elif "/" in cmd and not _PLACEHOLDER_RE.search(cmd):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' command '{cmd}' must be a bare executable name "
            "or a ./ plugin-relative path"))
    return out


def _check_url(world, name, entry):
    out = []
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' is missing a url"))
        return out
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' url must be absolute http or https"))
        return out
    if parts.username or parts.password:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' url must not carry user info"))
    if parts.fragment:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' url must not carry a fragment"))
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' uses plain http to a non-loopback host"))
    return out


@check("mcp-spec-placeholder")
def spec_placeholder(world: World, config: Config) -> list[Finding]:
    f, entries = _entries(world)
    if f is None or entries is None:
        return []
    out = []
    for name, entry in entries:
        cmd = entry.get("command")
        if isinstance(cmd, str) and _PLACEHOLDER_RE.search(cmd):
            out.append(_finding("mcp-spec-placeholder", "error", world, name,
                entry, f"server '{name}' uses a placeholder in 'command'; "
                "placeholders expand only in args, env values, and cwd"))
        env = entry.get("env")
        if isinstance(env, dict):
            for k in sorted(str(k) for k in env):
                if k in ("PLUGIN_ROOT", "PLUGIN_DATA"):
                    out.append(_finding("mcp-spec-placeholder", "error", world,
                        name, entry, f"server '{name}' env defines reserved "
                        f"name '{k}'; the client provides it"))
                elif _PLACEHOLDER_RE.search(k):
                    out.append(_finding("mcp-spec-placeholder", "error", world,
                        name, entry, f"server '{name}' uses a placeholder in "
                        f"env key '{k}'; placeholders never expand in keys"))
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and entry.get("type") == "stdio":
            if not _CWD_RE.match(cwd):
                out.append(_finding("mcp-spec-placeholder", "error", world,
                    name, entry, f"server '{name}' cwd '{cwd}' must start "
                    "with ./, ${PLUGIN_ROOT}, or ${PLUGIN_DATA}"))
        headers = entry.get("headers")
        if isinstance(headers, dict):
            for k, v in sorted(headers.items()):
                if isinstance(v, str) and _PLACEHOLDER_RE.search(v):
                    out.append(_finding("mcp-spec-placeholder", "warning",
                        world, name, entry, f"server '{name}' header '{k}' "
                        "contains a placeholder; placeholders do not expand "
                        "in headers, so it is sent literally"))
    return out
```

Then add `mcp_spec` to the check-module import line inside `run_checks`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_checks_mcp_spec.py tests/test_lint.py -x -q` then `uv run pytest -x -q`
Expected: PASS, including Task 5's suite test which now covers the new ids.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/mcp_spec.py src/drskill/checks/__init__.py tests/test_checks_mcp_spec.py
git commit -m "feat: agent-plugins mcp.json conformance checks"
```

---

### Task 8: run_lint orchestration and config walk-up

**Files:**
- Modify: `src/drskill/lint.py`
- Test: `tests/test_lint.py` (append)

**Interfaces:**
- Consumes: `checks.run_checks`, `lint.checks_for`, `ledger.load_effective_config` (existing), `deep.cache_dir/load_cache/judge_pairs/apply_verdicts` (existing), `mcp_connect.snapshot_dir/run_handshakes/load_snapshots/approved_dir` and `pipeline._add_tool_contributors` (existing).
- Produces: `lint.find_config_root(start: Path) -> Path` (nearest ancestor with drskill.toml, else the start dir itself); `lint.run_lint(target, config, config_root: Path, home: Path, mcp_connect: bool = False, judge=None, rewriter=None, max_calls: int | None = 25, progress=None) -> tuple[World, list[Finding]]`. All returned findings have `harnesses == []`. Task 9's CLI calls both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lint.py`:

```python
def test_find_config_root_walks_up(tmp_path):
    from drskill.lint import find_config_root

    (tmp_path / "drskill.toml").write_text("")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config_root(nested) == tmp_path
    lone = tmp_path.parent  # no drskill.toml above tmp_path's parent is not guaranteed;
    # use an isolated dir instead
    iso = tmp_path / "iso"
    iso.mkdir()
    (iso / "x").mkdir()
    # config root falls back to the start dir when nothing is found before
    # the filesystem root that contains one; assert the found root is an
    # ancestor-or-self of the start
    got = find_config_root(iso / "x")
    assert got in [iso / "x", *(iso / "x").parents]


def test_run_lint_skill_target(tmp_path):
    from drskill.ledger import Config
    from drskill.lint import run_lint

    write_skill(tmp_path / "s", "other-name")  # name mismatch -> error
    target = classify(tmp_path / "s")
    world, findings = run_lint(target, Config(), tmp_path, tmp_path / "home")
    assert any(f.check_id == "spec-name-mismatch" for f in findings)
    assert all(f.harnesses == [] for f in findings)


def test_run_lint_plugin_end_to_end(tmp_path):
    from drskill.ledger import Config
    from drskill.lint import run_lint

    root = tmp_path / "p"
    make_plugin(root)
    write_skill(root / "skills" / "alpha", "alpha")
    (root / "mcp.json").write_text(json.dumps({
        "mcpServers": {"bad": {"type": "websocket", "url": "wss://x"}}}))
    world, findings = run_lint(
        classify(root), Config(), tmp_path, tmp_path / "home")
    got = {f.check_id for f in findings}
    assert "mcp-spec-invalid" in got  # missing $schema + bad transport
    assert all(f.harnesses == [] for f in findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: FAIL with ImportError on `find_config_root` / `run_lint`.

- [ ] **Step 3: Implement**

Append to `src/drskill/lint.py`:

```python
from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.models import Finding


def find_config_root(start: Path) -> Path:
    """The nearest directory at or above `start` holding a drskill.toml.
    Falls back to `start` (or its parent for a file) so acks and budgets
    default sanely when the author has no ledger yet."""
    cur = (start if start.is_dir() else start.parent).resolve()
    for d in [cur, *cur.parents]:
        if (d / "drskill.toml").is_file():
            return d
    return cur


def run_lint(
    target: LintTarget,
    config: Config,
    config_root: Path,
    home: Path,
    mcp_connect: bool = False,
    judge=None,
    rewriter=None,
    max_calls: int | None = 25,
    progress=None,
) -> tuple[World, list[Finding]]:
    if progress:
        progress(f"reading {target.kind}")
    world = build_lint_world(target)
    if mcp_connect and world.mcp_servers:
        from drskill import mcp_connect as mcpc
        from drskill.pipeline import _add_tool_contributors

        sdir = mcpc.snapshot_dir(config_root, home, False)
        _, world.mcp_connect_failures = mcpc.run_handshakes(
            world.mcp_servers, sdir, progress=progress
        )
        _add_tool_contributors(world, mcpc.load_snapshots(sdir))
        world.mcp_approved = mcpc.load_snapshots(mcpc.approved_dir(sdir))
    findings = run_checks(
        world, config, checks_for(target, mcp_connect), progress=progress
    )
    if judge is not None:
        from drskill import deep

        cdir = deep.cache_dir(config_root, home, False)
        cache = deep.load_cache(cdir)
        acked_fps = {a.fingerprint for a in config.ack}
        active = [f for f in findings if f.fingerprint not in acked_fps]
        deep.judge_pairs(
            world, active, cache, cdir, judge, config.deep.model, max_calls,
            rewriter=rewriter, progress=progress,
        )
        findings = deep.apply_verdicts(world, findings, cache, acked_fps)
    # Lint has no harness context; strip any attribution a shared check set.
    findings = [
        f.model_copy(update={"harnesses": []}) if f.harnesses else f
        for f in findings
    ]
    return world, findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lint.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/lint.py tests/test_lint.py
git commit -m "feat: run_lint orchestration with config walk-up"
```

---

### Task 9: report.render_lint and the CLI lint command

**Files:**
- Modify: `src/drskill/report.py` (append `render_lint` after `render`)
- Modify: `src/drskill/cli.py` (new command after `scan`)
- Test: `tests/test_cli_lint.py` (create)

**Interfaces:**
- Consumes: `lint.classify/LintUsageError/find_config_root/run_lint` (Tasks 3/8), `ledger.filter_findings`, `report.to_json`, `report._print_finding` (existing).
- Produces: `report.render_lint(world, target, active, acked, console) -> None`; CLI command `drskill lint [PATH] [--type] [--json] [--fail-on error|warn] [--deep] [--max-calls N] [--mcp-connect]` with exit codes 0/1/2 per Global Constraints.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_lint.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()

GOOD_MANIFEST = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "demo-plugin",
}


def make_plugin(root: Path, manifest=None, skill_ok=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps(manifest or GOOD_MANIFEST))
    d = root / "skills" / "alpha"
    d.mkdir(parents=True)
    name = "alpha" if skill_ok else "wrong-name"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when the user asks to test alpha.\n---\nbody\n"
    )


def test_clean_plugin_exits_zero(tmp_path):
    make_plugin(tmp_path / "p")
    r = runner.invoke(app, ["lint", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    assert "No findings" in r.output


def test_error_exits_one(tmp_path):
    make_plugin(tmp_path / "p", manifest={"name": "demo-plugin"})  # no $schema
    r = runner.invoke(app, ["lint", str(tmp_path / "p")])
    assert r.exit_code == 1
    assert "plugin-manifest-invalid" in r.output


def test_warning_passes_by_default_fails_with_fail_on_warn(tmp_path):
    make_plugin(tmp_path / "p", manifest={**GOOD_MANIFEST, "surprise": 1})
    r = runner.invoke(app, ["lint", str(tmp_path / "p")])
    assert r.exit_code == 0
    r = runner.invoke(app, ["lint", str(tmp_path / "p"), "--fail-on", "warn"])
    assert r.exit_code == 1


def test_usage_error_exits_two(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = runner.invoke(app, ["lint", str(empty)])
    assert r.exit_code == 2
    r = runner.invoke(app, ["lint", str(empty), "--fail-on", "bogus"])
    assert r.exit_code == 2


def test_json_output(tmp_path):
    make_plugin(tmp_path / "p", manifest={"name": "demo-plugin"})
    r = runner.invoke(app, ["lint", str(tmp_path / "p"), "--json"])
    assert r.exit_code == 1
    payload = json.loads(r.output)
    assert any(f["check_id"] == "plugin-manifest-invalid" for f in payload)


def test_skill_file_target(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\nb\n")
    r = runner.invoke(app, ["lint", str(d / "SKILL.md")])
    assert r.exit_code == 1
    assert "spec-name-mismatch" in r.output


def test_ack_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "p"
    make_plugin(root, manifest={**GOOD_MANIFEST, "surprise": 1})
    (root / "drskill.toml").write_text("")
    r = runner.invoke(app, ["lint", str(root), "--fail-on", "warn", "--json"])
    fid = json.loads(r.output)[0]["fingerprint"]
    import tomllib  # noqa: F401  (ledger owns the format; append via its API)
    from drskill import ledger
    ledger.append_ack(root / "drskill.toml", ledger.Ack(fingerprint=fid))
    r = runner.invoke(app, ["lint", str(root), "--fail-on", "warn"])
    assert r.exit_code == 0, r.output
```

Note: check `ledger.Ack`'s required fields (`src/drskill/ledger.py:35`) and construct accordingly; if `append_ack` needs more fields (check name, date), fill them the way `cli.py`'s `ack` command does.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_lint.py -x -q`
Expected: FAIL, `lint` is not a registered command (exit code 2 from typer with "No such command").

- [ ] **Step 3: Implement render_lint**

Append to `src/drskill/report.py`:

```python
def render_lint(world, target, active, acked, console) -> None:
    from drskill.lint import LintTarget  # noqa: F401  (type only)

    if target.kind == "plugin":
        name = (world.plugin.name if world.plugin and world.plugin.name
                else target.path.name)
        n = sum(1 for c in world.contributors.values() if c.kind == "skill")
        head = f"[bold]drskill lint[/bold] — plugin '{escape(name)}', {n} skill{'s' if n != 1 else ''}"
        if world.mcp_servers:
            m = len(world.mcp_servers)
            head += f", {m} MCP server{'s' if m != 1 else ''}"
    elif target.kind == "skill":
        names = [c.name for c in world.contributors.values()] or [target.path.name]
        head = f"[bold]drskill lint[/bold] — skill '{escape(names[0])}'"
    else:
        m = len(world.mcp_servers)
        flavor = "Agent Plugins" if target.mcp_flavor == "agent-plugins" else "harness"
        head = (f"[bold]drskill lint[/bold] — MCP config ({flavor} flavor), "
                f"{m} server{'s' if m != 1 else ''}")
    console.print(head)
    if not active:
        console.print("\n[green]No findings.[/green]")
    seen = {f.fingerprint for f in active}  # suppress 'new' tags in lint
    for title, style, sev in (("ERRORS", "red bold", "error"),
                              ("WARNINGS", "yellow bold", "warning"),
                              ("NOTES", "bold", "note")):
        group = [f for f in active if f.severity == sev]
        if group:
            console.print(f"\n[{style}]{title}[/{style}]")
            print_findings(world, group, console, seen=seen)
    if acked:
        console.print(f"[dim]{len(acked)} acknowledged finding"
                      f"{'s' if len(acked) != 1 else ''} hidden[/dim]")
```

- [ ] **Step 4: Implement the CLI command**

Add to `src/drskill/cli.py` after `scan` (reuse `scan`'s `--deep` setup block verbatim for judge/rewriter/budget):

```python
@app.command()
def lint(
    path: Path = typer.Argument(Path("."), help="plugin directory, skill directory or SKILL.md, or MCP config file"),
    target_type: str | None = typer.Option(None, "--type", help="override detection: plugin, skill, or mcp"),
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
    if target_type not in (None, "plugin", "skill", "mcp"):
        console.print(f"[red]--type takes plugin, skill, or mcp, not[/red] {escape(target_type)}")
        raise typer.Exit(2)
    try:
        target = lint_mod.classify(path, target_type)
    except lint_mod.LintUsageError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(2)
    home = _home()
    config_root = lint_mod.find_config_root(target.path)
    config = _load_effective_config_or_exit(config_root, home, False)
    # --deep setup: same block as scan (judge, rewriter, budget), then:
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_lint.py -x -q` then `uv run pytest -x -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/drskill/report.py src/drskill/cli.py tests/test_cli_lint.py
git commit -m "feat: drskill lint command with CI exit codes and JSON output"
```

---

### Task 10: Golden fixtures, end-to-end test, and README

**Files:**
- Create: `tests/fixtures/plugins/valid-plugin/` (plugin.json, skills/summarize/SKILL.md, mcp.json)
- Create: `tests/fixtures/plugins/kitchen-sink/` (every violation class that travels well in git; symlink cases stay in Task 6's tmp_path tests)
- Test: `tests/test_lint_golden.py` (create)
- Modify: `README.md` (new section after Quick start)

**Interfaces:**
- Consumes: the finished `lint` CLI (Task 9).

- [ ] **Step 1: Create the golden valid plugin**

`tests/fixtures/plugins/valid-plugin/plugin.json`:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "valid-plugin",
  "version": "1.0.0",
  "description": "A fully conformant fixture plugin.",
  "license": "MIT"
}
```

`tests/fixtures/plugins/valid-plugin/skills/summarize/SKILL.md`:

```markdown
---
name: summarize
description: Use when the user asks to summarize a document into key points.
---

Read the document, list the main claims, and produce a short summary.
```

`tests/fixtures/plugins/valid-plugin/mcp.json`:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "docs": {
      "type": "streamable-http",
      "url": "https://mcp.example.com/docs"
    }
  }
}
```

- [ ] **Step 2: Create the kitchen-sink plugin**

`tests/fixtures/plugins/kitchen-sink/plugin.json`:

```json
{
  "$schema": "https://agent-plugins.org/schemas/9.9.9/plugin.schema.json",
  "name": "Kitchen--Sink",
  "version": 2,
  "surprise": true,
  "extensions": "not-an-object"
}
```

`tests/fixtures/plugins/kitchen-sink/skills/wrong/SKILL.md`:

```markdown
---
name: mismatched
---

No description, mismatched name.
```

`tests/fixtures/plugins/kitchen-sink/skills/nested/deeper/SKILL.md`:

```markdown
---
name: deeper
description: Too deep to be discovered.
---

body
```

`tests/fixtures/plugins/kitchen-sink/mcp.json`:

```json
{
  "mcpServers": {
    "bad": {
      "type": "websocket",
      "command": "python -m server",
      "env": {"PLUGIN_ROOT": "/x", "API_TOKEN": "sk-live-1234567890abcdef"},
      "cwd": "/absolute"
    }
  }
}
```

- [ ] **Step 3: Write the end-to-end test**

Create `tests/test_lint_golden.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "plugins"


def test_valid_plugin_is_clean():
    r = runner.invoke(app, ["lint", str(FIXTURES / "valid-plugin")])
    assert r.exit_code == 0, r.output


def test_kitchen_sink_reports_every_class():
    r = runner.invoke(app, ["lint", str(FIXTURES / "kitchen-sink"), "--json"])
    assert r.exit_code == 1
    got = {f["check_id"] for f in json.loads(r.output)}
    expected = {
        "plugin-manifest-invalid",     # version: 2 is not a string
        "plugin-name-invalid",         # Kitchen--Sink
        "plugin-manifest-unknown-field",  # surprise + extensions
        "plugin-schema-unknown",       # 9.9.9
        "plugin-skill-undiscoverable", # nested/deeper
        "mcp-spec-invalid",            # websocket transport, missing $schema
        "mcp-spec-placeholder",        # reserved env name, bad cwd
        "mcp-secret-in-config",        # API_TOKEN value
        "spec-name-mismatch",          # wrong/ vs mismatched
        "spec-missing-description",
    }
    assert expected <= got, expected - got
```

- [ ] **Step 4: Run and fix until green**

Run: `uv run pytest tests/test_lint_golden.py -x -q`
Expected: PASS. If a check id is missing, debug that check rather than shrinking `expected` (the fixture exists to hold the full set).

- [ ] **Step 5: Document in README**

Add after the Quick start section of `README.md` (match the README's existing plain voice):

```markdown
## Lint a plugin, skill, or MCP config before you publish

`drskill lint` points at one thing you are writing and checks it:

```
drskill lint ./my-plugin        # an Agent Plugins directory with plugin.json
drskill lint ./skills/foo       # one skill folder, or its SKILL.md
drskill lint ./mcp.json         # an MCP config file
```

A plugin is checked against the Agent Plugins 1.0.0 specification: the
manifest, the name rules, skill discovery, mcp.json transports and
placeholders, and path containment. On top of that, every skill and server
inside gets the same quality and security checks `drskill scan` runs.

Exit codes fit CI: 0 is clean, 1 means findings at or above the failure
threshold (errors by default; tighten with `--fail-on warn`), 2 is a usage
error. Use `--json` for machine-readable findings, and `drskill ack` to
accept a finding so the build goes green until the content changes.
```

- [ ] **Step 6: Full suite and commit**

Run: `uv run pytest -x -q`
Expected: PASS.

```bash
git add tests/fixtures/plugins tests/test_lint_golden.py README.md
git commit -m "test: golden lint fixtures and end-to-end coverage; document lint"
```

---

## Self-review notes (already applied)

- Spec coverage: every spec section maps to a task — command surface and detection (3), MCP flavors (3, 4, 7), architecture (1, 2, 4, 5, 8), plugin_spec checks (6), mcp_spec checks (7), reuse list (5), error boundaries (4, 6, 7), exit codes and flags (9), ack walk-up (8, 9), fixtures and table-style conformance coverage (6, 7, 10), README (10).
- `mcp-insecure-url` and `mcp-dead-server` run only for harness-flavor MCP targets; for the Agent Plugins flavor `mcp-spec-invalid` owns URL and command rules with plugin-root context, so the two never double-report one line.
- Deviation from the spec doc worth knowing: the spec says `World` gains one field; the implementation adds two (`plugin`, `plugin_mcp`) because `MCPServer` normalizes away the fields the mcp spec checks need. The spec's intent (raw file available to checks) is unchanged.
```
