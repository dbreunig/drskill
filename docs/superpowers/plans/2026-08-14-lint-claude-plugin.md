# Lint: Claude Code Plugin Layout + Marketplace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill lint` recognizes Claude Code's `.claude-plugin` layout (manifest checks, skills content checks) and lints marketplace descriptors with a pin-based supply-chain check.

**Architecture:** `LintTarget` gains a `plugin_flavor` (mirroring `mcp_flavor`), a `marketplace` kind, and a `dual_manifest` flag; `build_lint_world` parses `.claude-plugin/plugin.json` into a new `World.cc_plugin` and `.claude-plugin/marketplace.json` into a new `World.marketplace`; two new check modules (`checks/claude_plugin.py`, `checks/marketplace.py`) encode the documented format facts as constants with path-free fingerprints in the `mcp_spec` style.

**Tech Stack:** Python 3.13, pydantic, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-lint-claude-plugin-design.md`

## Global Constraints

- READ-ONLY: lint opens files for reading only; never fetches remote sources.
- Format facts cited in code comments: SchemaStore claude-code-plugin-manifest.json (generated 2026-04-23) and code.claude.com/docs plugins-reference.md + plugin-marketplaces.md (retrieved 2026-08-14).
- Fingerprints are path-free (`check_id|sha256(content)|reason-slug`, the `checks/mcp_spec.py` `_finding` style) so committed acks survive across checkouts.
- Severity ladder for `marketplace-unpinned-source` (user decision, pin-based): WARNING = git-form source with neither `sha` nor `ref`, npm without `version`, archive without `sha256`, any `http://` URL; NOTE = git-form with `ref` but no `sha`; fully pinned = nothing.
- An UNRECOGNIZED marketplace source type is a WARNING, never an error (Claude Code added `archive` in v2.1.224 and `command` in v2.1.229; lint must not scream at formats newer than its fact base).
- Kebab-case regex for claude-code plugin/marketplace/entry names: `^[a-z0-9]+(-[a-z0-9]+)*$`.
- Exit codes, `--fail-on`, `render_lint` semantics unchanged.
- Run tests with `uv run pytest tests/<file> -q`; full suite (`uv run pytest -q`) before each commit. TDD throughout.

---

### Task 1: Models, classification, CLI flag

**Files:**
- Modify: `src/drskill/models.py` (add `MarketplaceFile` after `PluginMcpFile`, models.py:109-117)
- Modify: `src/drskill/resolution.py` (World gains `cc_plugin`, `marketplace` after `plugin_mcp`, resolution.py:135-136)
- Modify: `src/drskill/lint.py` (`LintTarget`, `classify`, `_ACCEPTS`)
- Modify: `src/drskill/cli.py` (`--type` help string, cli.py lint command)
- Test: `tests/test_lint.py` (append)

**Interfaces:**
- Produces: `LintTarget.kind` Literal gains `"marketplace"`; `LintTarget.plugin_flavor: Literal["agent-plugins", "claude-code"] | None = None`; `LintTarget.dual_manifest: bool = False`.
- Produces: `MarketplaceFile(BaseModel)`: `path: str`, `root: str` (marketplace repo root), `text: str = ""`, `data: dict | None = None`, `parse_error: str | None = None`.
- Produces: `World.cc_plugin: PluginManifest | None = None`, `World.marketplace: MarketplaceFile | None = None`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_lint.py (reuse its existing imports/helpers; it
# already imports classify, LintUsageError, LintTarget from drskill.lint)
import json


def _cc_plugin(tmp_path, manifest=None):
    root = tmp_path / "ccplug"
    (root / ".claude-plugin").mkdir(parents=True)
    m = {"name": "my-plugin"} if manifest is None else manifest
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(m))
    return root


def test_classify_claude_plugin_dir(tmp_path):
    root = _cc_plugin(tmp_path)
    t = classify(root)
    assert t.kind == "plugin" and t.plugin_flavor == "claude-code"
    assert t.dual_manifest is False


def test_classify_agent_plugins_sets_flavor(tmp_path):
    root = tmp_path / "applug"
    root.mkdir()
    (root / "plugin.json").write_text('{"name": "x"}')
    t = classify(root)
    assert t.kind == "plugin" and t.plugin_flavor == "agent-plugins"


def test_classify_dual_manifest(tmp_path):
    root = _cc_plugin(tmp_path)
    (root / "plugin.json").write_text('{"name": "my-plugin"}')
    t = classify(root)
    assert t.plugin_flavor == "agent-plugins" and t.dual_manifest is True


def test_classify_marketplace_dir_and_file(tmp_path):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    mp = root / ".claude-plugin" / "marketplace.json"
    mp.write_text('{"name": "m", "owner": {"name": "o"}, "plugins": []}')
    assert classify(root).kind == "marketplace"
    assert classify(mp).kind == "marketplace"


def test_classify_forced_marketplace(tmp_path):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text("{}")
    assert classify(root, forced="marketplace").kind == "marketplace"
    plain = tmp_path / "plain"
    plain.mkdir()
    try:
        classify(plain, forced="marketplace")
        raise AssertionError("expected LintUsageError")
    except LintUsageError:
        pass


def test_classify_forced_plugin_accepts_claude_layout(tmp_path):
    root = _cc_plugin(tmp_path)
    t = classify(root, forced="plugin")
    assert t.kind == "plugin" and t.plugin_flavor == "claude-code"


def test_classify_empty_dir_error_mentions_claude_plugin(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    try:
        classify(d)
        raise AssertionError("expected LintUsageError")
    except LintUsageError as e:
        assert ".claude-plugin" in str(e)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lint.py -q -k "classify_claude or classify_agent or classify_dual or classify_marketplace or classify_forced or classify_empty"`
Expected: FAIL (LintTarget has no plugin_flavor; marketplace not a kind)

- [ ] **Step 3: Implement**

`models.py`, after `PluginMcpFile` (models.py:117):

```python
class MarketplaceFile(BaseModel):
    """A parsed .claude-plugin/marketplace.json, kept raw for the
    marketplace-* checks."""

    path: str
    root: str  # marketplace repo root (the dir containing .claude-plugin/)
    text: str = ""
    data: dict | None = None  # parsed JSON; None when it failed to parse
    parse_error: str | None = None
```

`resolution.py` World (after `plugin_mcp`, resolution.py:136), plus add
`MarketplaceFile` to the existing `from drskill.models import ...` line:

```python
    cc_plugin: PluginManifest | None = None
    marketplace: MarketplaceFile | None = None
```

`lint.py` — `_ACCEPTS` becomes:

```python
_ACCEPTS = (
    "drskill lint takes a plugin directory (with plugin.json or "
    ".claude-plugin/plugin.json), a skill directory or SKILL.md file, a "
    "marketplace directory or marketplace.json file, or an MCP config "
    "JSON file"
)
```

`LintTarget`:

```python
class LintTarget(BaseModel):
    kind: Literal["plugin", "skill", "mcp", "marketplace"]
    path: Path
    mcp_flavor: Literal["agent-plugins", "harness"] | None = None
    plugin_flavor: Literal["agent-plugins", "claude-code"] | None = None
    dual_manifest: bool = False
```

`classify` — replace the `forced == "plugin"` block, add a
`forced == "marketplace"` block after the `forced == "mcp"` block, and
replace the `p.is_dir()` block and the file fallthrough:

```python
    if forced == "plugin":
        if p.is_dir() and (p / "plugin.json").is_file():
            return _plugin_target(p)
        if p.is_dir() and (p / ".claude-plugin" / "plugin.json").is_file():
            return LintTarget(kind="plugin", path=p, plugin_flavor="claude-code")
        raise LintUsageError(
            f"{path} is not a plugin directory (no plugin.json or "
            ".claude-plugin/plugin.json)"
        )
```

```python
    if forced == "marketplace":
        if p.is_file() and p.name == "marketplace.json":
            return LintTarget(kind="marketplace", path=p)
        if p.is_dir() and (p / ".claude-plugin" / "marketplace.json").is_file():
            return LintTarget(kind="marketplace", path=p)
        raise LintUsageError(
            f"{path} is not a marketplace (no marketplace.json or "
            ".claude-plugin/marketplace.json)"
        )
```

```python
    if p.is_dir():
        if (p / "plugin.json").is_file():
            return _plugin_target(p)
        if (p / ".claude-plugin" / "plugin.json").is_file():
            return LintTarget(kind="plugin", path=p, plugin_flavor="claude-code")
        if (p / "SKILL.md").is_file():
            return LintTarget(kind="skill", path=p)
        if (p / ".claude-plugin" / "marketplace.json").is_file():
            return LintTarget(kind="marketplace", path=p)
        raise LintUsageError(
            f"{path} has no plugin.json, .claude-plugin/, or SKILL.md; {_ACCEPTS}"
        )
    if p.name == "SKILL.md":
        return LintTarget(kind="skill", path=p)
    if p.name == "marketplace.json":
        return LintTarget(kind="marketplace", path=p)
```

with the small helper (above `classify`):

```python
def _plugin_target(p: Path) -> LintTarget:
    return LintTarget(
        kind="plugin", path=p, plugin_flavor="agent-plugins",
        dual_manifest=(p / ".claude-plugin" / "plugin.json").is_file(),
    )
```

`cli.py` lint command: change the `--type` help string to
`"override detection: plugin, skill, mcp, or marketplace"`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_lint.py -q` then `uv run pytest -q`
Expected: PASS (existing classifier tests still green — agent-plugins
targets now carry `plugin_flavor="agent-plugins"`, which no existing
assertion inspects; fix any that compare whole LintTarget objects).

- [ ] **Step 5: Commit**

```bash
git add src/drskill/models.py src/drskill/resolution.py src/drskill/lint.py src/drskill/cli.py tests/test_lint.py
git commit -m "feat(lint): classify .claude-plugin layouts and marketplace targets"
```

---

### Task 2: build_lint_world for claude-code plugins and marketplaces

**Files:**
- Modify: `src/drskill/lint.py` (`_parse_manifest` grows a `rel` arg; `build_lint_world` branches; new `_load_marketplace`, `_cc_skill_roots`)
- Test: `tests/test_lint.py` (append)

**Interfaces:**
- Consumes: Task 1's target fields and World fields.
- Produces: for `plugin_flavor == "claude-code"` targets, `world.cc_plugin` set (PluginManifest with `root` = plugin root), skills collected from default `skills/`, declared `skills` pointers, and root `SKILL.md`; MCP servers from inline `mcpServers` object or the pointed/default `.mcp.json`. For `dual_manifest` targets, today's agent-plugins world plus `cc_plugin`. For plugin targets with `.claude-plugin/marketplace.json` and for `marketplace` targets, `world.marketplace` set.
- Produces: `_parse_manifest(root: Path, rel: str = "plugin.json") -> PluginManifest`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_lint.py

def _mk_skill_dir(base, name):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing {name}.\n---\nbody\n"
    )


def test_cc_world_collects_manifest_and_skills(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path, {"name": "my-plugin", "skills": ["extra-skills"]})
    _mk_skill_dir(root / "skills", "alpha")
    _mk_skill_dir(root / "extra-skills", "beta")
    world = build_lint_world(classify(root))
    assert world.cc_plugin is not None and world.cc_plugin.name == "my-plugin"
    assert world.plugin is None  # agent-plugins checks must no-op
    names = {c.name for c in world.contributors.values()}
    assert names == {"alpha", "beta"}


def test_cc_world_root_single_skill(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path)
    (root / "SKILL.md").write_text(
        "---\nname: solo\ndescription: Use when testing solo.\n---\nbody\n"
    )
    world = build_lint_world(classify(root))
    assert {c.name for c in world.contributors.values()} == {"solo"}


def test_cc_world_mcp_from_inline_and_default(tmp_path):
    from drskill.lint import build_lint_world

    inline = _cc_plugin(tmp_path, {"name": "p", "mcpServers": {
        "srv": {"command": "run-srv"}
    }})
    world = build_lint_world(classify(inline))
    assert [s.name for s in world.mcp_servers] == ["srv"]

    filed = _cc_plugin(tmp_path / "sub", {"name": "p2"})
    (filed / ".mcp.json").write_text('{"mcpServers": {"filed": {"command": "x"}}}')
    world2 = build_lint_world(classify(filed))
    assert [s.name for s in world2.mcp_servers] == ["filed"]


def test_dual_manifest_world_has_both(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path, {"name": "my-plugin", "version": "2.0.0"})
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "my-plugin", "version": "1.0.0", "description": "d",
    }))
    world = build_lint_world(classify(root))
    assert world.plugin is not None and world.cc_plugin is not None


def test_marketplace_world(tmp_path):
    from drskill.lint import build_lint_world

    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    mp = root / ".claude-plugin" / "marketplace.json"
    mp.write_text('{"name": "m", "owner": {"name": "o"}, "plugins": []}')
    world = build_lint_world(classify(root))
    assert world.marketplace is not None
    assert world.marketplace.root == str(root.resolve())
    assert world.marketplace.data == {"name": "m", "owner": {"name": "o"}, "plugins": []}
    # file target resolves the same root
    world2 = build_lint_world(classify(mp))
    assert world2.marketplace.root == str(root.resolve())


def test_plugin_with_sibling_marketplace_loads_it(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "m", "owner": {"name": "o"}, "plugins": []}'
    )
    world = build_lint_world(classify(root))
    assert world.marketplace is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lint.py -q -k "cc_world or dual_manifest_world or marketplace_world or sibling_marketplace"`
Expected: FAIL (cc_plugin never set; marketplace kind raises in build_lint_world)

- [ ] **Step 3: Implement**

`_parse_manifest` signature change (lint.py:131) — callers pass no `rel`
today, so this is backward-compatible:

```python
def _parse_manifest(root: Path, rel: str = "plugin.json") -> PluginManifest:
    path = root / rel
```

(body unchanged — it already reads `path` and reports `root`; update the
two f-string messages in checks that hardcode `plugin.json`? No: they
live in checks, not here. Nothing else changes.)

New helpers in `lint.py`:

```python
def _cc_skill_roots(root: Path, manifest: PluginManifest) -> list[Path]:
    """Default skills/ plus declared `skills` pointers (docs: `skills`
    ADDS to the default scan), plus the v2.1.142+ root single-skill."""
    roots = [root / "skills"]
    declared = manifest.raw.get("skills")
    entries = [declared] if isinstance(declared, str) else (
        declared if isinstance(declared, list) else []
    )
    for e in entries:
        if isinstance(e, str):
            roots.append(root / e)
    return roots


def _add_cc_skills(world: World, root: Path, manifest: PluginManifest) -> None:
    for base in _cc_skill_roots(root, manifest):
        if not base.is_dir():
            continue  # cc-component-missing reports declared-but-absent
        if (base / "SKILL.md").is_file():
            _add_skill(world, base / "SKILL.md")
            continue
        for child in sorted(base.iterdir()):
            f = child / "SKILL.md"
            if child.is_dir() and f.is_file():
                _add_skill(world, f)
    single = root / "SKILL.md"
    if single.is_file():
        _add_skill(world, single)


def _load_marketplace(world: World, mp_path: Path, root: Path) -> None:
    text = mp_path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
        world.marketplace = MarketplaceFile(
            path=str(mp_path), root=str(root), text=text,
            data=data if isinstance(data, dict) else None,
            parse_error=None if isinstance(data, dict) else "top level is not an object",
        )
    except json.JSONDecodeError as e:
        world.marketplace = MarketplaceFile(
            path=str(mp_path), root=str(root), text=text, parse_error=str(e)
        )
```

(import `MarketplaceFile` in lint.py's existing models import.)

`build_lint_world` — replace the `elif target.kind == "plugin":` branch
body and add a marketplace branch before the final `else`:

```python
    elif target.kind == "plugin":
        root = target.path.resolve()
        if target.plugin_flavor == "claude-code":
            world.cc_plugin = _parse_manifest(root, ".claude-plugin/plugin.json")
            _add_cc_skills(world, root, world.cc_plugin)
            servers = world.cc_plugin.raw.get("mcpServers")
            if isinstance(servers, dict):
                world.mcp_servers = _servers_from_map(
                    servers, harness="", scope="project",
                    source=root / ".claude-plugin" / "plugin.json", in_project=True,
                )
            elif (root / ".mcp.json").is_file():
                _add_harness_mcp(world, root / ".mcp.json")
        else:
            world.plugin = _parse_manifest(root)
            if target.dual_manifest:
                world.cc_plugin = _parse_manifest(root, ".claude-plugin/plugin.json")
            skills_dir = root / "skills"
            if skills_dir.is_dir():
                for child in sorted(skills_dir.iterdir()):
                    f = child / "SKILL.md"
                    if child.is_dir() and f.is_file():
                        _add_skill(world, f)
            mcp_path = root / "mcp.json"
            if mcp_path.is_file():
                _add_mcp_file(world, mcp_path, root, provisional=False)
        mp = root / ".claude-plugin" / "marketplace.json"
        if mp.is_file():
            _load_marketplace(world, mp, root)
    elif target.kind == "marketplace":
        if target.path.is_dir():
            root = target.path.resolve()
            _load_marketplace(world, root / ".claude-plugin" / "marketplace.json", root)
        else:
            p = target.path.resolve()
            root = p.parent.parent if p.parent.name == ".claude-plugin" else p.parent
            _load_marketplace(world, p, root)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_lint.py -q` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/lint.py tests/test_lint.py
git commit -m "feat(lint): build worlds for claude-code plugins and marketplaces"
```

---

### Task 3: checks/claude_plugin.py

**Files:**
- Create: `src/drskill/checks/claude_plugin.py`
- Modify: `src/drskill/checks/__init__.py:68` (add `claude_plugin` to the registration import)
- Test: `tests/test_checks_claude_plugin.py` (new)

**Interfaces:**
- Consumes: `world.cc_plugin` (PluginManifest), `world.plugin` (for the mismatch check).
- Produces: check ids `cc-manifest-invalid`, `cc-manifest-unknown-field`, `cc-component-missing`, `cc-manifest-mismatch`. Every check no-ops when `world.cc_plugin is None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_checks_claude_plugin.py
import json
from pathlib import Path

from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.lint import build_lint_world, classify

CC_CHECKS = [
    "cc-manifest-invalid", "cc-manifest-unknown-field",
    "cc-component-missing", "cc-manifest-mismatch",
]


def _world(tmp_path, manifest, extra=None):
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        manifest if isinstance(manifest, str) else json.dumps(manifest)
    )
    for rel in (extra or []):
        (root / rel).mkdir(parents=True)
    return build_lint_world(classify(root)), root


def _run(world):
    return run_checks(world, Config(), CC_CHECKS)


def test_valid_manifest_no_findings(tmp_path):
    world, _ = _world(tmp_path, {"name": "my-plugin", "version": "1.0.0"})
    assert _run(world) == []


def test_unparseable_and_missing_name_are_errors(tmp_path):
    world, _ = _world(tmp_path, "{nope")
    (f,) = _run(world)
    assert f.check_id == "cc-manifest-invalid" and f.severity == "error"

    world2, _ = _world(tmp_path / "b", {"version": "1.0.0"})
    (f2,) = _run(world2)
    assert f2.check_id == "cc-manifest-invalid" and "name" in f2.message


def test_name_not_kebab_is_error(tmp_path):
    world, _ = _world(tmp_path, {"name": "My_Plugin"})
    (f,) = _run(world)
    assert f.check_id == "cc-manifest-invalid" and "kebab" in f.message


def test_pointer_wrong_type_is_error(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "commands": 42})
    fs = [f for f in _run(world) if f.check_id == "cc-manifest-invalid"]
    assert fs and "commands" in fs[0].message


def test_inline_object_ok_for_hooks_not_commands(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "hooks": {"PreToolUse": []}})
    assert _run(world) == []
    world2, _ = _world(tmp_path / "b", {"name": "p", "commands": {"x": 1}})
    assert any(f.check_id == "cc-manifest-invalid" for f in _run(world2))


def test_unknown_field_is_warning(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "colour": "red"})
    (f,) = _run(world)
    assert f.check_id == "cc-manifest-unknown-field" and f.severity == "warning"
    assert "colour" in f.message


def test_declared_component_missing_is_error(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "commands": "./cmds"})
    (f,) = _run(world)
    assert f.check_id == "cc-component-missing" and f.severity == "error"
    world2, _ = _world(
        tmp_path / "b", {"name": "p", "commands": "./cmds"}, extra=["cmds"]
    )
    assert _run(world2) == []


def test_absent_defaults_never_flagged(tmp_path):
    # no skills/, no commands/ etc. and no pointers: zero findings
    world, _ = _world(tmp_path, {"name": "p"})
    assert _run(world) == []


def test_dual_manifest_mismatch_is_warning(tmp_path):
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "my-plugin", "version": "2.0.0"})
    )
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "my-plugin", "version": "1.0.0", "description": "d",
    }))
    world = build_lint_world(classify(root))
    fs = [f for f in run_checks(world, Config(), CC_CHECKS)
          if f.check_id == "cc-manifest-mismatch"]
    (f,) = fs
    assert f.severity == "warning" and "1.0.0" in f.message and "2.0.0" in f.message


def test_fingerprints_are_path_free(tmp_path):
    world_a, _ = _world(tmp_path / "a", {"name": "My_Plugin"})
    world_b, _ = _world(tmp_path / "b", {"name": "My_Plugin"})
    (fa,), (fb,) = _run(world_a), _run(world_b)
    assert fa.fingerprint == fb.fingerprint
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks_claude_plugin.py -q`
Expected: FAIL (unknown check ids — module doesn't exist)

- [ ] **Step 3: Implement**

```python
# src/drskill/checks/claude_plugin.py
"""Claude Code .claude-plugin manifest checks.

Format facts: SchemaStore claude-code-plugin-manifest.json (generated
2026-04-23) and code.claude.com/docs plugins-reference.md (retrieved
2026-08-14). `name` is the only required field (kebab-case, no spaces);
component-pointer fields take string|array (skills/commands/agents/
workflows/outputStyles/themes/monitors) or string|array|object (hooks/
mcpServers/lspServers); default component dirs are optional and never
flagged. Every check no-ops when world.cc_plugin is None."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

CC_KNOWN_FIELDS = {
    "$schema", "name", "version", "displayName", "description", "author",
    "homepage", "repository", "license", "keywords", "metadata",
    "defaultEnabled", "userConfig", "channels", "dependencies", "settings",
    "skills", "commands", "agents", "workflows", "hooks", "mcpServers",
    "lspServers", "outputStyles", "themes", "monitors", "experimental",
}
# pointer field -> allowed value types
_PATHISH = (str, list)
_PATHISH_OR_INLINE = (str, list, dict)
CC_POINTER_TYPES = {
    "skills": _PATHISH, "commands": _PATHISH, "agents": _PATHISH,
    "workflows": _PATHISH, "outputStyles": _PATHISH, "themes": _PATHISH,
    "monitors": _PATHISH,
    "hooks": _PATHISH_OR_INLINE, "mcpServers": _PATHISH_OR_INLINE,
    "lspServers": _PATHISH_OR_INLINE,
}
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _ccf(check_id, severity, world, message, reason, fix=None):
    """Path-free fingerprint: manifest content + reason slug, so acks
    survive across checkouts (mcp_spec convention)."""
    text = world.cc_plugin.raw_text or reason
    payload = "|".join(
        [check_id, hashlib.sha256(text.encode()).hexdigest(), reason]
    )
    return make_finding(
        check_id, severity, [], message,
        fix_commands=fix or [], fingerprint_texts=[payload],
    )


@check("cc-manifest-invalid")
def cc_manifest_invalid(world: World, config: Config) -> list[Finding]:
    m = world.cc_plugin
    if m is None:
        return []
    where = f"{m.root}/.claude-plugin/plugin.json"
    if m.parse_error:
        return [_ccf(
            "cc-manifest-invalid", "error", world,
            f"{where} does not parse: {m.parse_error}",
            "parse-error", fix=[f"Fix the JSON syntax in {where}"],
        )]
    out = []
    name = m.raw.get("name")
    if not isinstance(name, str) or not name:
        out.append(_ccf(
            "cc-manifest-invalid", "error", world,
            f"{where} is missing the required `name` string "
            "(the only required field)",
            "missing-name",
        ))
    elif not _KEBAB_RE.fullmatch(name):
        out.append(_ccf(
            "cc-manifest-invalid", "error", world,
            f"plugin name {name!r} is not kebab-case "
            "(lowercase letters/digits separated by single hyphens)",
            "name-not-kebab",
        ))
    for field, allowed in CC_POINTER_TYPES.items():
        v = m.raw.get(field)
        if v is not None and not isinstance(v, allowed):
            kinds = "string or array" if allowed is _PATHISH else (
                "string, array, or object"
            )
            out.append(_ccf(
                "cc-manifest-invalid", "error", world,
                f"`{field}` must be a {kinds}, got {type(v).__name__}",
                f"pointer-type:{field}",
            ))
    return out


@check("cc-manifest-unknown-field")
def cc_manifest_unknown_field(world: World, config: Config) -> list[Finding]:
    m = world.cc_plugin
    if m is None or m.parse_error:
        return []
    out = []
    for field in sorted(set(m.raw) - CC_KNOWN_FIELDS):
        out.append(_ccf(
            "cc-manifest-unknown-field", "warning", world,
            f"unknown manifest field `{field}`; known fields are "
            f"{', '.join(sorted(CC_KNOWN_FIELDS))}",
            f"unknown:{field}",
        ))
    return out


@check("cc-component-missing")
def cc_component_missing(world: World, config: Config) -> list[Finding]:
    m = world.cc_plugin
    if m is None or m.parse_error:
        return []
    root = Path(m.root)
    out = []
    for field, allowed in CC_POINTER_TYPES.items():
        v = m.raw.get(field)
        entries = [v] if isinstance(v, str) else (v if isinstance(v, list) else [])
        for e in entries:
            if not isinstance(e, str):
                continue  # wrong element types are cc-manifest-invalid's job
            if not (root / e).exists():
                out.append(_ccf(
                    "cc-component-missing", "error", world,
                    f"`{field}` declares {e!r} but {root / e} does not exist",
                    f"missing:{field}:{e}",
                ))
    return out


@check("cc-manifest-mismatch")
def cc_manifest_mismatch(world: World, config: Config) -> list[Finding]:
    a, b = world.plugin, world.cc_plugin
    if a is None or b is None or a.parse_error or b.parse_error:
        return []
    out = []
    for field in ("name", "version"):
        va, vb = a.raw.get(field), b.raw.get(field)
        if isinstance(va, str) and isinstance(vb, str) and va != vb:
            out.append(_ccf(
                "cc-manifest-mismatch", "warning", world,
                f"plugin.json and .claude-plugin/plugin.json disagree on "
                f"`{field}`: {va!r} vs {vb!r} — regenerate or reconcile "
                "the manifests",
                f"mismatch:{field}",
            ))
    return out
```

Registration: in `checks/__init__.py:68` add `claude_plugin` (and, for
Task 4, `marketplace`) to the import list.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_checks_claude_plugin.py -q` then `uv run pytest -q`
Expected: PASS. Note `test_absent_defaults_never_flagged` also proves
pointer-less manifests produce nothing.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/claude_plugin.py src/drskill/checks/__init__.py tests/test_checks_claude_plugin.py
git commit -m "feat(checks): claude-code plugin manifest checks"
```

---

### Task 4: checks/marketplace.py

**Files:**
- Create: `src/drskill/checks/marketplace.py`
- Modify: `src/drskill/checks/__init__.py:68` (add `marketplace` if not already added in Task 3)
- Test: `tests/test_checks_marketplace.py` (new)

**Interfaces:**
- Consumes: `world.marketplace` (MarketplaceFile).
- Produces: check ids `marketplace-invalid`, `marketplace-unpinned-source`, `marketplace-command-source`, `marketplace-entry-missing`. Every check no-ops when `world.marketplace is None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_checks_marketplace.py
import json
from pathlib import Path

from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.lint import build_lint_world, classify

MP_CHECKS = [
    "marketplace-invalid", "marketplace-unpinned-source",
    "marketplace-command-source", "marketplace-entry-missing",
]


def _world(tmp_path, data, extra_dirs=None):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        data if isinstance(data, str) else json.dumps(data)
    )
    for rel in (extra_dirs or []):
        (root / rel).mkdir(parents=True)
    return build_lint_world(classify(root))


def _mp(entries, **top):
    return {"name": "market", "owner": {"name": "o"}, "plugins": entries, **top}


def _run(world):
    return run_checks(world, Config(), MP_CHECKS)


def test_valid_pinned_marketplace_no_findings(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "local-one", "source": "./plugins/one"},
        {"name": "gh-pinned", "source": {
            "source": "github", "repo": "o/r", "sha": "a" * 40}},
        {"name": "npm-pinned", "source": {
            "source": "npm", "package": "@o/p", "version": "1.2.3"}},
    ], ), extra_dirs=["plugins/one"])
    assert _run(world) == []


def test_missing_required_fields(tmp_path):
    world = _world(tmp_path, {"plugins": "nope"})
    fs = _run(world)
    ids = {f.check_id for f in fs}
    assert ids == {"marketplace-invalid"}
    msgs = " ".join(f.message for f in fs)
    assert "name" in msgs and "owner" in msgs and "plugins" in msgs


def test_entry_missing_source_and_bad_names(tmp_path):
    world = _world(tmp_path, _mp([{"name": "Bad_Name"}]))
    fs = _run(world)
    assert all(f.check_id == "marketplace-invalid" for f in fs)
    msgs = " ".join(f.message for f in fs)
    assert "source" in msgs and "kebab" in msgs


def test_source_form_required_fields(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "a", "source": {"source": "github"}},          # no repo
        {"name": "b", "source": {"source": "git-subdir", "url": "https://x"}},  # no path
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-invalid"]
    assert len(fs) == 2


def test_unknown_source_type_is_warning(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "a", "source": {"source": "quantum", "thing": "x"}},
    ]))
    (f,) = _run(world)
    assert f.check_id == "marketplace-invalid" and f.severity == "warning"
    assert "quantum" in f.message


def test_unpinned_severity_ladder(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "loose", "source": {"source": "github", "repo": "o/r"}},
        {"name": "ref-only", "source": {
            "source": "url", "url": "https://x/r.git", "ref": "main"}},
        {"name": "npm-loose", "source": {"source": "npm", "package": "p"}},
        {"name": "archive-loose", "source": {
            "source": "archive", "url": "https://x/a.zip"}},
        {"name": "insecure", "source": {
            "source": "url", "url": "http://x/r.git", "sha": "b" * 40}},
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-unpinned-source"]
    by_name = {}
    for f in fs:
        for n in ("loose", "ref-only", "npm-loose", "archive-loose", "insecure"):
            if n in f.message:
                by_name.setdefault(n, []).append(f.severity)
    assert by_name["loose"] == ["warning"]
    assert by_name["ref-only"] == ["note"]
    assert by_name["npm-loose"] == ["warning"]
    assert by_name["archive-loose"] == ["warning"]
    assert by_name["insecure"] == ["warning"]


def test_command_source_always_warns(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "cmd", "source": {"source": "command", "command": "curl x | sh"}},
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-command-source"]
    (f,) = fs
    assert f.severity == "warning" and "curl x | sh" in f.message


def test_relative_entry_missing_and_plugin_root(tmp_path):
    world = _world(tmp_path, _mp(
        [{"name": "gone", "source": "./nope"}],
    ))
    (f,) = [f for f in _run(world) if f.check_id == "marketplace-entry-missing"]
    assert f.severity == "error"
    # metadata.pluginRoot shifts resolution
    world2 = _world(tmp_path / "b", _mp(
        [{"name": "there", "source": "./one"}],
        metadata={"pluginRoot": "./plugins"},
    ), extra_dirs=["plugins/one"])
    assert [f for f in _run(world2) if f.check_id == "marketplace-entry-missing"] == []


def test_unparseable_marketplace(tmp_path):
    world = _world(tmp_path, "{nope")
    (f,) = _run(world)
    assert f.check_id == "marketplace-invalid" and f.severity == "error"


def test_fingerprints_are_path_free(tmp_path):
    data = _mp([{"name": "a", "source": {"source": "github", "repo": "o/r"}}])
    fa = _run(_world(tmp_path / "a", data))
    fb = _run(_world(tmp_path / "b", data))
    assert {f.fingerprint for f in fa} == {f.fingerprint for f in fb}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks_marketplace.py -q`
Expected: FAIL (unknown check ids)

- [ ] **Step 3: Implement**

```python
# src/drskill/checks/marketplace.py
"""Claude Code marketplace descriptor checks.

Format facts: code.claude.com/docs plugin-marketplaces.md (retrieved
2026-08-14). Required: name (kebab-case), owner.name, plugins[]. Seven
source forms: relative-path string; {source: github|url|git-subdir|npm|
archive|command, ...}. Pinning: git forms sha (precedence) / ref; npm
version; archive sha256. A command source runs a shell command at
install. Unrecognized source types warn rather than error: Claude Code
added archive (v2.1.224) and command (v2.1.229), and this fact base
will age. Every check no-ops when world.marketplace is None."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World
from drskill.text import one_line

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# source type -> fields required for that form
SOURCE_REQUIRED = {
    "github": ["repo"], "url": ["url"], "git-subdir": ["url", "path"],
    "npm": ["package"], "archive": ["url"], "command": ["command"],
}
_GIT_FORMS = {"github", "url", "git-subdir"}


def _mf(check_id, severity, world, message, reason, fix=None):
    payload = "|".join([
        check_id,
        hashlib.sha256((world.marketplace.text or reason).encode()).hexdigest(),
        reason,
    ])
    return make_finding(
        check_id, severity, [], message,
        fix_commands=fix or [], fingerprint_texts=[payload],
    )


def _entries(world):
    mp = world.marketplace
    if mp is None or mp.parse_error or mp.data is None:
        return []
    plugins = mp.data.get("plugins")
    return plugins if isinstance(plugins, list) else []


def _ename(entry, i):
    name = entry.get("name") if isinstance(entry, dict) else None
    return name if isinstance(name, str) and name else f"entry {i}"


@check("marketplace-invalid")
def marketplace_invalid(world: World, config: Config) -> list[Finding]:
    mp = world.marketplace
    if mp is None:
        return []
    if mp.parse_error:
        return [_mf(
            "marketplace-invalid", "error", world,
            f"{mp.path} does not parse: {mp.parse_error}",
            "parse-error", fix=[f"Fix the JSON syntax in {mp.path}"],
        )]
    out = []
    name = mp.data.get("name")
    if not isinstance(name, str) or not name:
        out.append(_mf("marketplace-invalid", "error", world,
                       "marketplace is missing the required `name` string",
                       "missing-name"))
    elif not _KEBAB_RE.fullmatch(name):
        out.append(_mf("marketplace-invalid", "error", world,
                       f"marketplace name {name!r} is not kebab-case",
                       "name-not-kebab"))
    owner = mp.data.get("owner")
    if not (isinstance(owner, dict) and isinstance(owner.get("name"), str)
            and owner["name"]):
        out.append(_mf("marketplace-invalid", "error", world,
                       "marketplace is missing `owner` with a `name` string",
                       "missing-owner"))
    plugins = mp.data.get("plugins")
    if not isinstance(plugins, list):
        out.append(_mf("marketplace-invalid", "error", world,
                       "marketplace is missing the required `plugins` array",
                       "missing-plugins"))
        return out
    for i, entry in enumerate(plugins):
        ename = _ename(entry, i)
        if not isinstance(entry, dict):
            out.append(_mf("marketplace-invalid", "error", world,
                           f"plugins[{i}] is not an object", f"entry-shape:{i}"))
            continue
        n = entry.get("name")
        if not isinstance(n, str) or not n:
            out.append(_mf("marketplace-invalid", "error", world,
                           f"plugins[{i}] is missing the required `name`",
                           f"entry-missing-name:{i}"))
        elif not _KEBAB_RE.fullmatch(n):
            out.append(_mf("marketplace-invalid", "error", world,
                           f"plugin entry name {n!r} is not kebab-case",
                           f"entry-name-not-kebab:{n}"))
        src = entry.get("source")
        if src is None:
            out.append(_mf("marketplace-invalid", "error", world,
                           f"{ename} is missing the required `source`",
                           f"entry-missing-source:{ename}"))
        elif isinstance(src, dict):
            stype = src.get("source")
            required = SOURCE_REQUIRED.get(stype) if isinstance(stype, str) else None
            if required is None:
                out.append(_mf(
                    "marketplace-invalid", "warning", world,
                    f"{ename} has unrecognized source type {stype!r} "
                    "(newer than this lint's fact base, or a typo); known "
                    f"types: {', '.join(sorted(SOURCE_REQUIRED))}",
                    f"unknown-source-type:{ename}",
                ))
            else:
                for req in required:
                    if not isinstance(src.get(req), str) or not src.get(req):
                        out.append(_mf(
                            "marketplace-invalid", "error", world,
                            f"{ename} source form {stype!r} requires `{req}`",
                            f"source-missing:{ename}:{req}",
                        ))
        elif not isinstance(src, str):
            out.append(_mf("marketplace-invalid", "error", world,
                           f"{ename} `source` must be a string or object",
                           f"source-shape:{ename}"))
    return out


@check("marketplace-unpinned-source")
def marketplace_unpinned(world: World, config: Config) -> list[Finding]:
    out = []
    for i, entry in enumerate(_entries(world)):
        if not isinstance(entry, dict):
            continue
        ename = _ename(entry, i)
        src = entry.get("source")
        if not isinstance(src, dict):
            continue  # local paths have nothing to pin
        stype = src.get("source")
        for field in ("url", "repo"):
            v = src.get(field)
            if isinstance(v, str) and v.startswith("http://"):
                out.append(_mf(
                    "marketplace-unpinned-source", "warning", world,
                    f"{ename} uses insecure (non-TLS) source URL {v!r}",
                    f"insecure-url:{ename}",
                ))
        if stype in _GIT_FORMS:
            if isinstance(src.get("sha"), str):
                continue
            if isinstance(src.get("ref"), str):
                out.append(_mf(
                    "marketplace-unpinned-source", "note", world,
                    f"{ename} pins only a ref ({src['ref']!r}); a ref can "
                    "be a movable branch — pin `sha` for immutability",
                    f"ref-only:{ename}",
                ))
            else:
                out.append(_mf(
                    "marketplace-unpinned-source", "warning", world,
                    f"{ename} has no `sha` or `ref` — installs track the "
                    "remote's default branch and can change under you",
                    f"unpinned:{ename}",
                ))
        elif stype == "npm" and not isinstance(src.get("version"), str):
            out.append(_mf(
                "marketplace-unpinned-source", "warning", world,
                f"{ename} has no npm `version` — installs float to latest",
                f"npm-unpinned:{ename}",
            ))
        elif stype == "archive" and not isinstance(src.get("sha256"), str):
            out.append(_mf(
                "marketplace-unpinned-source", "warning", world,
                f"{ename} archive has no `sha256` — contents unverifiable",
                f"archive-unpinned:{ename}",
            ))
    return out


@check("marketplace-command-source")
def marketplace_command_source(world: World, config: Config) -> list[Finding]:
    out = []
    for i, entry in enumerate(_entries(world)):
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        if isinstance(src, dict) and src.get("source") == "command":
            cmd = src.get("command")
            shown = one_line(cmd, 120) if isinstance(cmd, str) else "<missing>"
            out.append(_mf(
                "marketplace-command-source", "warning", world,
                f"{_ename(entry, i)} installs by RUNNING a shell command: "
                f"{shown!r} — review it before trusting this marketplace",
                f"command:{_ename(entry, i)}",
            ))
    return out


@check("marketplace-entry-missing")
def marketplace_entry_missing(world: World, config: Config) -> list[Finding]:
    mp = world.marketplace
    out = []
    for i, entry in enumerate(_entries(world)):
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        if not isinstance(src, str):
            continue
        base = Path(mp.root)
        meta = mp.data.get("metadata")
        plugin_root = meta.get("pluginRoot") if isinstance(meta, dict) else None
        if isinstance(plugin_root, str):
            base = base / plugin_root
        resolved = base / src
        if not resolved.is_dir():
            out.append(_mf(
                "marketplace-entry-missing", "error", world,
                f"{_ename(entry, i)} points at {src!r} but "
                f"{resolved} does not exist",
                f"missing:{_ename(entry, i)}:{src}",
            ))
    return out
```

(If `one_line` in `drskill/text.py` has a different signature, adapt the
call — it exists per the MCP tool-description truncation work; check
`grep -n "def one_line" src/drskill/text.py`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_checks_marketplace.py -q` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/marketplace.py src/drskill/checks/__init__.py tests/test_checks_marketplace.py
git commit -m "feat(checks): marketplace descriptor checks with pin-based sources"
```

---

### Task 5: checks_for wiring + end-to-end lint tests + README

**Files:**
- Modify: `src/drskill/lint.py` (`CC_PLUGIN_CHECKS`, `MARKETPLACE_CHECKS` lists; `checks_for`)
- Modify: `README.md` (lint section: new accepted targets + checks)
- Test: `tests/test_cli_lint.py` (append end-to-end runs)

**Interfaces:**
- Consumes: everything above.
- Produces: `checks_for` routing — claude-code plugin targets get `SKILL_CONTENT_CHECKS + ["exact-duplicate", "near-duplicate"] + CC_PLUGIN_CHECKS + MARKETPLACE_CHECKS + MCP_SPEC-static suites`; dual-manifest agent-plugins targets get today's suite + `CC_PLUGIN_CHECKS` + `MARKETPLACE_CHECKS`; marketplace targets get `MARKETPLACE_CHECKS` only.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli_lint.py (reuse its CliRunner/invoke helpers —
# read the file's existing pattern first and match it; the assertions
# below are the contract)

def test_lint_claude_plugin_end_to_end(tmp_path):
    # a claude-code plugin with a bad-name manifest and one good skill
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "Bad_Name"}')
    d = root / "skills" / "good"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: good\ndescription: Use when the user asks for a "
        "good-skill demo.\n---\nbody\n"
    )
    r = _invoke_lint(tmp_path, str(root))
    assert r.exit_code == 1  # error-severity finding present
    assert "cc-manifest-invalid" in r.output


def test_lint_marketplace_end_to_end(tmp_path):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "m", "owner": {"name": "o"},
        "plugins": [{"name": "x", "source": {"source": "github", "repo": "o/r"}}],
    }))
    r = _invoke_lint(tmp_path, str(root))
    # warning severity only -> default --fail-on error passes
    assert r.exit_code == 0
    assert "marketplace-unpinned-source" in r.output


def test_lint_dual_manifest_runs_both_suites(tmp_path):
    from drskill.lint import checks_for, classify

    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "p"}')
    (root / "plugin.json").write_text('{"name": "p"}')
    ids = checks_for(classify(root), mcp_connect=False)
    assert "plugin-manifest-invalid" in ids and "cc-manifest-invalid" in ids
    assert "marketplace-invalid" in ids


def test_marketplace_target_gets_only_marketplace_checks(tmp_path):
    from drskill.lint import MARKETPLACE_CHECKS, checks_for, classify

    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text("{}")
    assert checks_for(classify(root), mcp_connect=False) == MARKETPLACE_CHECKS
```

(Adapt `_invoke_lint` to `tests/test_cli_lint.py`'s existing invoke
helper name; add `import json` if the file lacks it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_lint.py -q -k "claude_plugin or marketplace or dual_manifest"`
Expected: FAIL (checks_for KeyError/branch missing; new ids absent)

- [ ] **Step 3: Implement**

`lint.py` — new lists after `PLUGIN_SPEC_CHECKS` (lint.py:204):

```python
CC_PLUGIN_CHECKS = [
    "cc-manifest-invalid", "cc-manifest-unknown-field",
    "cc-component-missing", "cc-manifest-mismatch",
]
MARKETPLACE_CHECKS = [
    "marketplace-invalid", "marketplace-unpinned-source",
    "marketplace-command-source", "marketplace-entry-missing",
]
```

`checks_for` becomes:

```python
def checks_for(target: LintTarget, mcp_connect: bool) -> list[str]:
    if target.kind == "skill":
        return list(SKILL_CONTENT_CHECKS)
    if target.kind == "marketplace":
        return list(MARKETPLACE_CHECKS)
    if target.kind == "plugin":
        ids = SKILL_CONTENT_CHECKS + ["exact-duplicate", "near-duplicate"]
        if target.plugin_flavor == "claude-code":
            ids += CC_PLUGIN_CHECKS + MARKETPLACE_CHECKS
            ids += MCP_SPEC_CHECKS + MCP_STATIC_CHECKS
        else:
            ids += PLUGIN_SPEC_CHECKS + MCP_SPEC_CHECKS + MCP_STATIC_CHECKS
            if target.dual_manifest:
                ids += CC_PLUGIN_CHECKS + MARKETPLACE_CHECKS
    elif target.mcp_flavor == "agent-plugins":
        ids = MCP_SPEC_CHECKS + MCP_STATIC_CHECKS
    else:
        # No spec to enforce; the generic URL and dead-command checks stand in.
        ids = MCP_STATIC_CHECKS + ["mcp-insecure-url", "mcp-dead-server"]
    if mcp_connect:
        ids = ids + MCP_CONNECT_CHECKS
    return ids
```

(`MARKETPLACE_CHECKS` on plugin targets is safe: every marketplace check
no-ops when `world.marketplace is None`. `MCP_SPEC_CHECKS` on
claude-code targets is safe the same way via `world.plugin_mcp is None`
— verify that guard exists in checks/mcp_spec.py; if a spec check keys
off something else, drop `MCP_SPEC_CHECKS` from the claude-code branch
and keep only `MCP_STATIC_CHECKS`, noting it in the commit message.)

README: in the lint section, add the new accepted targets (claude-code
plugin dirs, marketplace dirs/files) and one sentence on the marketplace
supply-chain checks, matching the section's existing style.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_lint.py tests/test_lint.py -q` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/lint.py README.md tests/test_cli_lint.py
git commit -m "feat(lint): wire claude-plugin and marketplace check suites"
```

---

### Task 6: Live gate + spec status

**Files:**
- Modify: `docs/superpowers/specs/2026-08-14-lint-claude-plugin-design.md` (status + gate results)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q` — all green.

- [ ] **Step 2: Live gate (read-only)**

```bash
# a real installed claude-code plugin (active superpowers version)
uv run drskill lint ~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0
# the everyharness kitchen-sink fixture if still present (dual-manifest)
ls /private/tmp/claude-501/-Users-dbreunig-Development-drskill/*/scratchpad/everyharness/fixtures 2>/dev/null
# any locally-known marketplace descriptor
find ~/.claude/plugins/marketplaces -name marketplace.json 2>/dev/null | head -3
uv run drskill lint <one of the found marketplace.json paths>
```

Read every finding once. Expected: superpowers lints clean or with real,
explainable findings (its skills already pass scan); marketplace
descriptors may legitimately produce unpinned-source findings — judge
signal vs noise. A crash, a nonsense finding, or a flood of
false positives on the real superpowers plugin is a BLOCKED-level bug:
stop and report rather than patching around it.

- [ ] **Step 3: Record results**

Append `; shipped 2026-08-14` to the spec's Status line and add a
`## Gate results (2026-08-14)` section with concrete bullets (targets
linted, findings seen, verdicts).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-08-14-lint-claude-plugin-design.md
git commit -m "docs(spec): record lint claude-plugin gate results"
```

---

## Self-Review Notes

- Spec coverage: classification (T1), world building incl. dual-manifest and marketplace roots (T2), the four cc-* checks (T3), the four marketplace-* checks with the pin ladder and unknown-type warning (T4), wiring + README (T5), live gate (T6). Out-of-scope items from the spec have no tasks, by design.
- Types checked across tasks: `MarketplaceFile` fields used by `_mf`/`_entries` match Task 1's model; `_parse_manifest(root, rel)` used in T2 matches T1's unchanged callers; check-id lists in T5 match T3/T4 registrations.
- T5 carries an explicit contingency for MCP_SPEC_CHECKS guard semantics rather than a placeholder.
- Test helpers `_cc_plugin` (T1) and `_world` (T3/T4) are defined in their own files where used — T3/T4 do not import from test_lint.py.
