# Plugin Stores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scan the skills that harnesses load from their plugin/extension install stores (claude-code, codex, gemini-cli, copilot, droid), with suite pre-attribution, so plugin-delivered skills stop being invisible injection surface.

**Architecture:** A new `stores.py` module holds one adapter per harness; each reads ONLY its harness's state files (never globs for active versions) and returns `InstalledPlugin` records. `discovery.discover()` appends each enabled plugin's skills roots after the harness's native search paths; `build_world` stamps `Provenance(kind="plugin")` and pre-attributes `Contributor.suite`; resolution excludes claude-code plugin instances from shadow pairing (Claude Code namespaces plugin skills); `suites.assign_suites` skips pre-attributed contributors.

**Tech Stack:** Python 3.13, pydantic, tomllib, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-plugin-stores-design.md`

## Global Constraints

- drskill is READ-ONLY: adapters open state files and skill files for reading, never write anywhere.
- Malformed/unreadable state files: the store contributes nothing; the state file path is surfaced through the existing `world.unreadable` list (as `(harness_id, str(path))`), never a crash, never a guess.
- No new checks, CLI commands, or flags. No report changes (the list table already renders `c.source.kind`, so `"plugin"` displays for free).
- Provenance `source` string format: `"name@marketplace==version"`, dropping `@marketplace` when there is none (gemini) and `==version` when unknown.
- All store facts carry dated provenance comments in code (source commits: codex `5bc8da6`, gemini-cli `c0d1924`; empirical probes 2026-08-14, copilot CLI 1.0.80, @factory/cli 0.196.0).
- Run tests with `uv run pytest tests/<file> -q`. Commit after every task.

---

### Task 1: `stores.py` skeleton + claude-code adapter

**Files:**
- Create: `src/drskill/stores.py`
- Test: `tests/test_stores.py`

**Interfaces:**
- Produces: `InstalledPlugin` (pydantic model: `harness: str`, `name: str`, `marketplace: str | None`, `version: str | None`, `scope: Literal["user", "project"]`, `project_path: Path | None`, `skills_roots: list[Path]`, `enabled: bool`, `recursive: bool`, `evidence: Path`, and property `provenance_source -> str`).
- Produces: `discover_plugins(harness_id: str, home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]` — second element is unreadable state-file paths. Returns `([], [])` for harnesses with no adapter.
- Produces: module-level `ADAPTERS: dict[str, Callable]` registry.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stores.py
import json
from pathlib import Path

from drskill.stores import InstalledPlugin, discover_plugins


def _skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def _cc_state(home: Path, plugins: dict) -> Path:
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "plugins": plugins}), encoding="utf-8")
    return p


def _cc_cache(home: Path, marketplace: str, plugin: str, version: str) -> Path:
    d = home / ".claude" / "plugins" / "cache" / marketplace / plugin / version
    _skill(d / "skills", f"{plugin}-skill")
    return d


def test_claude_code_active_user_install(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "6.0.0")
    _cc_cache(home, "mkt", "sp", "4.0.0")  # stale: must NOT be returned
    _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": "6.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "6.0.0"
    assert p.scope == "user" and p.enabled and p.recursive
    assert p.skills_roots == [active / "skills"]
    assert p.provenance_source == "sp@mkt==6.0.0"


def test_claude_code_local_scope_only_matches_its_project(tmp_path):
    home = tmp_path / "home"
    mine, other = tmp_path / "mine", tmp_path / "other"
    mine.mkdir(); other.mkdir()
    active = _cc_cache(home, "mkt", "sp", "4.0.0")
    _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": str(mine),
         "installPath": str(active), "version": "4.0.0"},
    ]})
    plugins, _ = discover_plugins("claude-code", home, mine)
    (p,) = plugins
    assert p.scope == "project" and p.project_path == mine
    assert discover_plugins("claude-code", home, other)[0] == []


def test_claude_code_enabled_plugins(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    a = _cc_cache(home, "mkt", "a", "1.0.0")
    b = _cc_cache(home, "mkt", "b", "1.0.0")
    _cc_state(home, {
        "a@mkt": [{"scope": "user", "installPath": str(a), "version": "1.0.0"}],
        "b@mkt": [{"scope": "user", "installPath": str(b), "version": "1.0.0"}],
    })
    settings = home / ".claude" / "settings.json"
    # explicit false disables; a MISSING key counts as enabled
    settings.write_text(json.dumps({"enabledPlugins": {"a@mkt": False}}))
    plugins, _ = discover_plugins("claude-code", home, proj)
    by_name = {p.name: p for p in plugins}
    assert by_name["a"].enabled is False
    assert by_name["b"].enabled is True


def test_claude_code_malformed_state_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_missing_state_and_unknown_harness_are_empty(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); proj.mkdir()
    assert discover_plugins("claude-code", home, proj) == ([], [])
    assert discover_plugins("pi", home, proj) == ([], [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stores.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'drskill.stores'`

- [ ] **Step 3: Write the implementation**

```python
# src/drskill/stores.py
"""Plugin/extension install stores: which suites a harness has installed
and where their ACTIVE skills live.

Each adapter reads only its harness's own state files and never globs for
active versions -- stores retain stale version dirs (observed live on
claude-code) and record enabled/disabled state that determines whether
skills load at all. Store facts are dated per adapter; see
docs/superpowers/specs/2026-08-14-plugin-stores-design.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field


class InstalledPlugin(BaseModel):
    harness: str
    name: str
    marketplace: str | None = None
    version: str | None = None
    scope: Literal["user", "project"] = "user"
    project_path: Path | None = None
    skills_roots: list[Path] = Field(default_factory=list)
    enabled: bool = True
    recursive: bool = True
    evidence: Path

    @property
    def provenance_source(self) -> str:
        s = self.name
        if self.marketplace:
            s += f"@{self.marketplace}"
        if self.version:
            s += f"=={self.version}"
        return s


def _read_json(path: Path, unreadable: list[str]):
    """Parse a JSON file; None when missing, and None + an unreadable
    entry when present but unparseable."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        unreadable.append(str(path))
        return None


# --- claude-code -----------------------------------------------------------
# Observed live 2026-08-14: cache/<marketplace>/<plugin>/<version>/skills
# with STALE version dirs retained; ~/.claude/plugins/installed_plugins.json
# maps "name@marketplace" -> a list of installs, each {scope: "user" |
# "local"+projectPath, installPath, version}; ~/.claude/settings.json
# enabledPlugins maps the same key -> bool. A missing enabledPlugins key
# counts as enabled (installed implies usable; for a scanner, over-scanning
# is safer than missing surface). Claude Code namespaces plugin skills
# ("plugin:skill"), which resolution accounts for separately.
def _claude_code(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    state_path = home / ".claude" / "plugins" / "installed_plugins.json"
    data = _read_json(state_path, unreadable)
    if not isinstance(data, dict):
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".claude" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return [], unreadable
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list):
            continue
        name, _, marketplace = key.partition("@")
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            install_path = inst.get("installPath")
            if not install_path:
                continue
            scope = inst.get("scope")
            if scope == "user":
                pscope, ppath = "user", None
            elif scope == "local":
                pp = inst.get("projectPath")
                if not pp or Path(pp).resolve() != project_root.resolve():
                    continue
                pscope, ppath = "project", Path(pp)
            else:
                continue
            out.append(InstalledPlugin(
                harness="claude-code",
                name=name,
                marketplace=marketplace or None,
                version=inst.get("version"),
                scope=pscope,
                project_path=ppath,
                skills_roots=[Path(install_path) / "skills"],
                enabled=enabled_map.get(key) is not False,
                recursive=True,
                evidence=state_path,
            ))
    return out, unreadable


ADAPTERS: dict[str, Callable[[Path, Path], tuple[list[InstalledPlugin], list[str]]]] = {
    "claude-code": _claude_code,
}


def discover_plugins(
    harness_id: str, home: Path, project_root: Path
) -> tuple[list[InstalledPlugin], list[str]]:
    adapter = ADAPTERS.get(harness_id)
    if adapter is None:
        return [], []
    return adapter(home, project_root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stores.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/drskill/stores.py tests/test_stores.py
git commit -m "feat(stores): InstalledPlugin model and claude-code store adapter"
```

---

### Task 2: codex adapter

**Files:**
- Modify: `src/drskill/stores.py` (add `_codex`, register in `ADAPTERS`)
- Test: `tests/test_stores.py` (append)

**Interfaces:**
- Consumes: `InstalledPlugin`, `_read_json`, `ADAPTERS` from Task 1.
- Produces: `ADAPTERS["codex"]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_stores.py

def _codex_config(home: Path, text: str) -> Path:
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _codex_cache(home: Path, marketplace: str, plugin: str, version: str,
                 manifest: str = "plugin.json") -> Path:
    d = home / ".codex" / "plugins" / "cache" / marketplace / plugin / version
    _skill(d / "skills", f"{plugin}-skill")
    (d / manifest).parent.mkdir(parents=True, exist_ok=True)
    (d / manifest).write_text(
        json.dumps({"name": plugin, "version": version}), encoding="utf-8"
    )
    return d


def test_codex_prefers_local_version_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "sp", "1.0.0")
    local = _codex_cache(home, "mkt", "sp", "local")
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [local / "skills"]
    assert p.version == "local"


def test_codex_highest_version_when_no_local(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "sp", "1.9.0")
    high = _codex_cache(home, "mkt", "sp", "1.10.0")  # numeric, not lexicographic
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    assert plugins[0].skills_roots == [high / "skills"]


def test_codex_disabled_and_missing_cache(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "off", "1.0.0")
    _codex_config(home, (
        '[plugins."off@mkt"]\nenabled = false\n'
        '[plugins."gone@mkt"]\nenabled = true\n'  # no cache dir: skipped
    ))
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.name == "off" and p.enabled is False


def test_codex_agent_plugin_manifest_is_shallow_legacy_recursive(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "ap", "1.0.0", manifest="plugin.json")
    _codex_cache(home, "mkt", "leg", "1.0.0",
                 manifest=".codex-plugin/plugin.json")
    _codex_config(home, (
        '[plugins."ap@mkt"]\nenabled = true\n'
        '[plugins."leg@mkt"]\nenabled = true\n'
    ))
    plugins, _ = discover_plugins("codex", home, proj)
    by_name = {p.name: p for p in plugins}
    assert by_name["ap"].recursive is False
    assert by_name["leg"].recursive is True


def test_codex_manifest_skills_paths_and_migrated_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    (d / ".codex-plugin" / "migrated-command-skills").mkdir(parents=True)
    (d / "myskills").mkdir()
    (d / ".codex-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "sp", "version": "1.0.0", "paths": {"skills": ["myskills"]}}
    ))
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [
        d / "myskills", d / ".codex-plugin" / "migrated-command-skills"
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stores.py -q -k codex`
Expected: FAIL — `discover_plugins("codex", ...)` returns `([], [])` (no adapter yet)

- [ ] **Step 3: Write the implementation**

```python
# add to src/drskill/stores.py (after _claude_code); also add
# `import tomllib` at the top of the file.

# --- codex ------------------------------------------------------------------
# Source-verified 2026-08-14 (openai/codex commit 5bc8da6, current main):
# cache at ~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/
# (core-plugins/src/store.rs:26-27,131-139); enablement in config.toml
# [plugins."name@marketplace"] enabled (config/src/plugin_edit.rs:21-34),
# disabled plugins' skills never load (core-plugins/src/loader.rs:843-845);
# active version prefers the "local" dir, else highest (store.rs:168-189);
# skills roots from the manifest's paths.skills, default skills/, plus
# .codex-plugin/migrated-command-skills (loader.rs:1059-1087). Legacy
# manifests scan recursively (depth <= 6), agent-plugins root plugin.json
# scans direct children only -- approximated here as recursive True/False.
# The $CODEX_HOME env override is ignored, matching how drskill already
# reads ~/.codex/config.toml for MCP. drskill's no-shadowing handling for
# codex (search_order "none") extends to plugin skills, matching source
# (ext/skills/src/loader/host_merge.rs:232-233).
_CODEX_LEGACY_MANIFESTS = (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
)


def _codex_version_key(v: str):
    # Numeric-aware ordering approximating codex's compare; "1.10.0" beats
    # "1.9.0". Non-numeric parts rank as 0 with the raw string as tiebreak.
    return [int(p) if p.isdigit() else 0 for p in v.split(".")], v


def _codex(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    config_path = home / ".codex" / "config.toml"
    if not config_path.is_file():
        return [], unreadable
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [str(config_path)]
    plugins_tbl = config.get("plugins")
    if not isinstance(plugins_tbl, dict):
        return [], unreadable
    out: list[InstalledPlugin] = []
    cache = home / ".codex" / "plugins" / "cache"
    for key, entry in sorted(plugins_tbl.items()):
        name, _, marketplace = key.partition("@")
        base = cache / (marketplace or "") / name
        if not base.is_dir():
            continue  # stale config entry, nothing installed
        versions = sorted(d.name for d in base.iterdir() if d.is_dir())
        if not versions:
            continue
        version = ("local" if "local" in versions
                   else max(versions, key=_codex_version_key))
        root = base / version
        manifest_path = root / "plugin.json"
        recursive = False  # agent-plugins root manifest: shallow scan
        if not manifest_path.is_file():
            recursive = True  # legacy manifest flavors scan recursively
            for rel in _CODEX_LEGACY_MANIFESTS:
                if (root / rel).is_file():
                    manifest_path = root / rel
                    break
            else:
                manifest_path = None
        skills_rel = ["skills"]
        if manifest_path is not None:
            manifest = _read_json(manifest_path, unreadable)
            if isinstance(manifest, dict):
                declared = (manifest.get("paths") or {}).get("skills")
                if isinstance(declared, list) and declared:
                    skills_rel = [s for s in declared if isinstance(s, str)]
        roots = [root / rel for rel in skills_rel]
        migrated = root / ".codex-plugin" / "migrated-command-skills"
        if migrated.is_dir():
            roots.append(migrated)
        enabled = entry.get("enabled") is not False if isinstance(entry, dict) else True
        out.append(InstalledPlugin(
            harness="codex",
            name=name,
            marketplace=marketplace or None,
            version=version,
            scope="user",
            skills_roots=roots,
            enabled=enabled,
            recursive=recursive,
            evidence=config_path,
        ))
    return out, unreadable
```

And register it:

```python
ADAPTERS: dict[str, Callable[[Path, Path], tuple[list[InstalledPlugin], list[str]]]] = {
    "claude-code": _claude_code,
    "codex": _codex,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stores.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/stores.py tests/test_stores.py
git commit -m "feat(stores): codex plugin store adapter"
```

---

### Task 3: gemini-cli adapter

**Files:**
- Modify: `src/drskill/stores.py` (add `_gemini_enabled`, `_gemini_cli`, register)
- Test: `tests/test_stores.py` (append)

**Interfaces:**
- Consumes: `InstalledPlugin`, `_read_json`, `ADAPTERS`.
- Produces: `ADAPTERS["gemini-cli"]`; helper `_gemini_enabled(name: str, config: dict, project_root: Path) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_stores.py

def _gemini_ext(home: Path, name: str, version: str = "1.0.0") -> Path:
    d = home / ".gemini" / "extensions" / name
    _skill(d / "skills", f"{name}-skill")
    (d / "gemini-extension.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return d


def test_gemini_extension_discovered_nonrecursive(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = _gemini_ext(home, "ext-a")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "ext-a" and p.marketplace is None
    assert p.version == "1.0.0" and p.enabled
    assert p.recursive is False  # loader globs SKILL.md + */SKILL.md only
    assert p.skills_roots == [d / "skills"]
    assert p.provenance_source == "ext-a==1.0.0"


def test_gemini_enablement_rules(tmp_path):
    # Semantics from extensionEnablement.ts (commit c0d1924): default
    # enabled; overrides iterate in file order, each matching rule sets
    # enabled = not-disable; "!" = disable; trailing "*" = include
    # subdirs; exact rule matches only that dir.
    home = tmp_path / "home"
    proj = tmp_path / "work" / "sub"
    proj.mkdir(parents=True)
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    work = str((tmp_path / "work").resolve())
    enablement.write_text(json.dumps({
        "ext-a": {"overrides": [f"!{work}/*", f"{str(proj.resolve())}/"]}
    }), encoding="utf-8")
    # disabled under work/*, but the later exact rule re-enables at proj
    plugins, _ = discover_plugins("gemini-cli", home, proj)
    assert plugins[0].enabled is True
    # a sibling dir under work/ only matches the disable rule
    sib = tmp_path / "work" / "other"
    sib.mkdir()
    plugins, _ = discover_plugins("gemini-cli", home, sib)
    assert plugins[0].enabled is False


def test_gemini_exact_rule_does_not_match_subdir(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "work" / "sub"
    proj.mkdir(parents=True)
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text(json.dumps({
        "ext-a": {"overrides": [f"!{str((tmp_path / 'work').resolve())}/"]}
    }), encoding="utf-8")
    plugins, _ = discover_plugins("gemini-cli", home, proj)
    assert plugins[0].enabled is True  # exact rule matches work/ only


def test_gemini_corrupt_enablement_defaults_enabled(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text("{broken", encoding="utf-8")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    # matches gemini's own behavior: corrupt file -> everything enabled;
    # drskill additionally surfaces the file as unreadable
    assert plugins[0].enabled is True
    assert unreadable == [str(enablement)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stores.py -q -k gemini`
Expected: FAIL — no gemini adapter registered

- [ ] **Step 3: Write the implementation**

```python
# add to src/drskill/stores.py

# --- gemini-cli -------------------------------------------------------------
# Source-verified 2026-08-14 (google-gemini/gemini-cli commit c0d1924):
# store at ~/.gemini/extensions/<name>/ with identity in
# gemini-extension.json (required name, version; extensions/storage.ts:23-39,
# extension.ts:24-26); skills in a FIXED skills/ subdir loaded
# NON-recursively -- glob exactly SKILL.md + */SKILL.md
# (extension-manager.ts:921-922, skillLoader.ts:127). Enablement in
# extension-enablement.json: {name: {overrides: [rule, ...]}}; default
# enabled, rules iterate in file order and each MATCHING rule sets
# enabled = not-disable; "!" prefix = disable, trailing "*" = include
# subdirs, base rule is slash-wrapped; exact rules match only that dir
# (extensionEnablement.ts:146-178, 35-43, 102-108). Missing or corrupt
# file = everything enabled (:189-202). Extension skills rank below user
# and workspace skills (skillManager.ts:54-99), which discovery encodes
# by appending plugin roots after native paths.
def _gemini_enabled(name: str, config: dict, project_root: Path) -> bool:
    entry = config.get(name)
    overrides = entry.get("overrides") if isinstance(entry, dict) else None
    if not isinstance(overrides, list):
        return True
    cwd = "/" + str(project_root.resolve()).replace("\\", "/").strip("/") + "/"
    enabled = True
    for rule in overrides:
        if not isinstance(rule, str) or not rule:
            continue
        disable = rule.startswith("!")
        r = rule[1:] if disable else rule
        subdirs = r.endswith("*")
        base = r[:-1] if subdirs else r
        if cwd == base or (subdirs and cwd.startswith(base)):
            enabled = not disable
    return enabled


def _gemini_cli(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    ext_root = home / ".gemini" / "extensions"
    if not ext_root.is_dir():
        return [], unreadable
    enablement = _read_json(ext_root / "extension-enablement.json", unreadable)
    if not isinstance(enablement, dict):
        enablement = {}
    out: list[InstalledPlugin] = []
    for ext_dir in sorted(p for p in ext_root.iterdir() if p.is_dir()):
        manifest_path = ext_dir / "gemini-extension.json"
        manifest = _read_json(manifest_path, unreadable)
        if not isinstance(manifest, dict) or not manifest.get("name"):
            continue
        name = str(manifest["name"])
        out.append(InstalledPlugin(
            harness="gemini-cli",
            name=name,
            marketplace=None,
            version=str(manifest["version"]) if manifest.get("version") else None,
            scope="user",
            skills_roots=[ext_dir / "skills"],
            enabled=_gemini_enabled(name, enablement, project_root),
            recursive=False,
            evidence=manifest_path,
        ))
    return out, unreadable
```

Register `"gemini-cli": _gemini_cli` in `ADAPTERS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stores.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/stores.py tests/test_stores.py
git commit -m "feat(stores): gemini-cli extension store adapter"
```

---

### Task 4: copilot and droid adapters

**Files:**
- Modify: `src/drskill/stores.py` (add `_copilot`, `_droid`, register both)
- Test: `tests/test_stores.py` (append)

**Interfaces:**
- Consumes: `InstalledPlugin`, `_read_json`, `ADAPTERS`.
- Produces: `ADAPTERS["copilot"]`, `ADAPTERS["droid"]`; helper `_read_jsonc(path, unreadable)` (JSON with leading `//` comment lines).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_stores.py

def test_copilot_installed_plugin_with_jsonc_header(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    store = home / ".copilot" / "installed-plugins" / "mkt" / "sp"
    _skill(store / "skills", "sp-skill")
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # copilot writes comment lines BEFORE the JSON body (observed 1.0.80)
    cfg.write_text(
        "// User settings belong in settings.json.\n"
        "// This file is managed automatically.\n"
        + json.dumps({"installedPlugins": [
            {"name": "sp", "marketplace": "mkt", "version": "1.2.3",
             "cache_path": str(store)},
        ]}),
        encoding="utf-8",
    )
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"sp@mkt": True}}), encoding="utf-8"
    )
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "1.2.3"
    assert p.skills_roots == [store / "skills"]
    assert p.enabled and p.scope == "user"


def test_copilot_disabled_and_default_store_path(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    store = home / ".copilot" / "installed-plugins" / "mkt" / "sp"
    _skill(store / "skills", "sp-skill")
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # no cache_path: fall back to installed-plugins/<mkt>/<name>
    cfg.write_text(json.dumps({"installedPlugins": [
        {"name": "sp", "marketplace": "mkt", "version": "1.2.3"},
    ]}), encoding="utf-8")
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"sp@mkt": False}}), encoding="utf-8"
    )
    plugins, _ = discover_plugins("copilot", home, proj)
    (p,) = plugins
    assert p.skills_roots == [store / "skills"]
    assert p.enabled is False


def test_droid_installed_plugins(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    install = home / ".factory" / "plugins" / "cache" / "mkt-ab" / "sp-cd" / "v1"
    _skill(install / "skills", "sp-skill")
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    (state_dir / "sp-mkt-user-1234.json").write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "user", "installPath": str(install), "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "v1"
    assert p.skills_roots == [install / "skills"]
    assert p.scope == "user" and p.enabled


def test_droid_malformed_entry_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stores.py -q -k "copilot or droid"`
Expected: FAIL — no adapters registered

- [ ] **Step 3: Write the implementation**

```python
# add to src/drskill/stores.py

def _read_jsonc(path: Path, unreadable: list[str]):
    """JSON preceded by // comment lines (copilot's config.json style)."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        body = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("//")
        )
        return json.loads(body)
    except (OSError, ValueError):
        unreadable.append(str(path))
        return None


# --- copilot ----------------------------------------------------------------
# Empirical 2026-08-14 (GitHub Copilot CLI 1.0.80, sandboxed install probe):
# store at ~/.copilot/installed-plugins/<marketplace>/<plugin>/ (whole
# plugin copied once, unversioned dirs) with skills under skills/; state in
# ~/.copilot/config.json (JSONC: comment lines precede the JSON) --
# installedPlugins[] with name/marketplace/version/cache_path -- and
# ~/.copilot/settings.json enabledPlugins keyed "name@marketplace".
# Collision rank probed stable: project > personal > plugin, which
# discovery encodes by appending plugin roots after native paths.
# Recursion inside a plugin's skills dir was not probed; harness default
# (recursive) assumed.
def _copilot(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    config_path = home / ".copilot" / "config.json"
    config = _read_jsonc(config_path, unreadable)
    if not isinstance(config, dict):
        return [], unreadable
    installed = config.get("installedPlugins")
    if not isinstance(installed, list):
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".copilot" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    for entry in installed:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        marketplace = entry.get("marketplace")
        cache_path = entry.get("cache_path")
        root = (Path(cache_path) if cache_path
                else home / ".copilot" / "installed-plugins" / str(marketplace or "") / name)
        key = f"{name}@{marketplace}" if marketplace else name
        out.append(InstalledPlugin(
            harness="copilot",
            name=name,
            marketplace=str(marketplace) if marketplace else None,
            version=str(entry["version"]) if entry.get("version") else None,
            scope="user",
            skills_roots=[root / "skills"],
            enabled=enabled_map.get(key) is not False,
            recursive=True,
            evidence=config_path,
        ))
    return out, unreadable


# --- droid ------------------------------------------------------------------
# Empirical 2026-08-14 (@factory/cli 0.196.0, sandboxed install probe):
# store at ~/.factory/plugins/cache/<mkt>-<hash>/<plugin>-<hash>/<installId>/
# with skills under skills/; one JSON per install under
# ~/.factory/plugins/installed_plugins/ ({schemaVersion, pluginId:
# "name@marketplace", entry: {scope, installPath, version}}). droid has no
# skill-enumeration verb, so THAT these cached skills load (and at what
# rank) is BEST-EFFORT, resting on prime-radiant-inc/everyharness's
# container install checks. Scopes other than "user" are skipped (only
# "user" observed).
def _droid(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    if not state_dir.is_dir():
        return [], unreadable
    out: list[InstalledPlugin] = []
    for state_path in sorted(state_dir.glob("*.json")):
        data = _read_json(state_path, unreadable)
        if not isinstance(data, dict):
            continue
        plugin_id = data.get("pluginId")
        entry = data.get("entry")
        if not isinstance(plugin_id, str) or not isinstance(entry, dict):
            continue
        if entry.get("scope") != "user":
            continue
        install_path = entry.get("installPath")
        if not install_path:
            continue
        name, _, marketplace = plugin_id.partition("@")
        out.append(InstalledPlugin(
            harness="droid",
            name=name,
            marketplace=marketplace or None,
            version=str(entry["version"]) if entry.get("version") else None,
            scope="user",
            skills_roots=[Path(install_path) / "skills"],
            enabled=entry.get("enabled") is not False,
            recursive=True,
            evidence=state_path,
        ))
    return out, unreadable
```

Register both in `ADAPTERS` (`"copilot": _copilot`, `"droid": _droid`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stores.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/stores.py tests/test_stores.py
git commit -m "feat(stores): copilot and droid store adapters"
```

---

### Task 5: models + discovery integration

**Files:**
- Modify: `src/drskill/models.py` (`RawInstance` gains `plugin`, `Provenance.kind` gains `"plugin"`)
- Modify: `src/drskill/discovery.py` (`discover()` appends plugin roots; returns unreadable state paths)
- Modify: `src/drskill/pipeline.py` (thread the new unreadable list into `world.unreadable` — find the `discover(h, project_root, home, global_only)` call, currently pipeline.py:75)
- Test: `tests/test_discovery.py` (append)

**Interfaces:**
- Consumes: `stores.discover_plugins`, `InstalledPlugin` (Tasks 1–4).
- Produces: `RawInstance.plugin: InstalledPlugin | None = None`; `discover(...)` now returns `tuple[list[RawInstance], list[BrokenSymlink], list[tuple[str, str]]]` — third element is `(harness_id, state_path_str)` unreadable pairs.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_discovery.py (reuse that file's existing imports
# and helpers; add `import json` if absent)

def _mk_skill(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    return d / "SKILL.md"


def _install_cc_plugin(home, proj_unused, name="sp"):
    active = home / ".claude" / "plugins" / "cache" / "mkt" / name / "1.0.0"
    _mk_skill(active / "skills", f"{name}-plugin-skill")
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 1, "plugins": {f"{name}@mkt": [
        {"scope": "user", "installPath": str(active), "version": "1.0.0"},
    ]}}), encoding="utf-8")
    return active


def test_discover_appends_plugin_roots_after_native(tmp_path):
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    _mk_skill(proj / ".claude" / "skills", "native-skill")
    _mk_skill(home / ".claude" / "skills", "global-skill")
    _install_cc_plugin(home, proj)
    instances, _broken, unreadable = discover(h, proj, home)
    assert unreadable == []
    by_name = {i.skill_file.parent.name: i for i in instances}
    plug = by_name["sp-plugin-skill"]
    native_orders = [i.order for i in instances if i.plugin is None]
    assert plug.plugin is not None and plug.plugin.name == "sp"
    assert plug.scope == "user"
    assert plug.order > max(native_orders)  # plugin roots rank below native


def test_discover_skips_disabled_plugins(tmp_path):
    import json as _json
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _install_cc_plugin(home, proj)
    (home / ".claude" / "settings.json").write_text(
        _json.dumps({"enabledPlugins": {"sp@mkt": False}}), encoding="utf-8"
    )
    instances, _b, _u = discover(h, proj, home)
    assert all(i.plugin is None for i in instances)


def test_discover_global_only_keeps_user_scope_plugins(tmp_path):
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _install_cc_plugin(home, proj)
    instances, _b, _u = discover(h, proj, home, global_only=True)
    assert any(i.plugin is not None for i in instances)


def test_discover_gemini_plugin_roots_nonrecursive(tmp_path):
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "gemini-cli")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    ext = home / ".gemini" / "extensions" / "ext-a"
    _mk_skill(ext / "skills", "top-skill")
    _mk_skill(ext / "skills" / "nest1" / "nest2", "deep-skill")
    (ext / "gemini-extension.json").write_text(
        json.dumps({"name": "ext-a", "version": "1.0.0"}), encoding="utf-8"
    )
    instances, _b, _u = discover(h, proj, home)
    names = {i.skill_file.parent.name for i in instances}
    assert "top-skill" in names and "deep-skill" not in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discovery.py -q`
Expected: FAIL — `discover` returns 2 values and `RawInstance` has no `plugin` field

- [ ] **Step 3: Modify `models.py`**

In `RawInstance` (models.py:9-16) add the field, and extend `Provenance.kind` (models.py:28-30):

```python
class RawInstance(BaseModel):
    """A skill file as one harness sees it, before resolution."""

    harness: str
    scope: Literal["project", "user"]
    skill_file: Path
    via_symlink: bool
    order: int  # index of the containing search path in the harness's list
    plugin: "InstalledPlugin | None" = None  # set for store-delivered skills
```

with, at the top of models.py, a guarded import to avoid a cycle
(`stores.py` imports nothing from models except pydantic basics, but keep
the import late to be safe):

```python
from drskill.stores import InstalledPlugin  # noqa: E402  (after BaseModel defs if needed)
```

If placing the import at the top creates an import cycle (stores.py does
NOT import models, so it should not), fall back to
`plugin: object | None = None` typed via `InstalledPlugin` in a
`TYPE_CHECKING` block and call `RawInstance.model_rebuild()` in stores or
discovery. Prefer the direct import.

```python
class Provenance(BaseModel):
    kind: Literal["skills-lock", "gh-skill", "linked", "unmanaged", "plugin"] = "unmanaged"
    source: str | None = None
```

- [ ] **Step 4: Modify `discovery.py`**

Change `discover()` (discovery.py:66) to append plugin roots and return
the unreadable pairs:

```python
from drskill.stores import discover_plugins


def discover(
    h: HarnessDef, project_root: Path, home: Path, global_only: bool = False
) -> tuple[list[RawInstance], list[BrokenSymlink], list[tuple[str, str]]]:
    instances: list[RawInstance] = []
    broken: list[BrokenSymlink] = []
    native_paths = h.search_paths(project_root, home, global_only)
    for order, (base, scope, spec_str) in enumerate(native_paths):
        ...  # existing body unchanged
    # Store-delivered skills: enabled plugins' roots rank BELOW every
    # native path (proven on gemini and copilot; codex keeps its
    # no-shadowing semantics via search_order "none").
    plugins, unreadable_states = discover_plugins(h.id, home, project_root)
    unreadable = [(h.id, p) for p in unreadable_states]
    order = len(native_paths)
    for plug in plugins:
        if not plug.enabled:
            continue  # disabled plugins' skills demonstrably do not load
        if global_only and plug.scope == "project":
            continue
        for base in plug.skills_roots:
            if not base.is_dir():
                order += 1
                continue
            for f in _find_skill_files(base, plug.recursive):
                if not f.exists():
                    continue
                instances.append(RawInstance(
                    harness=h.id,
                    scope=plug.scope,
                    skill_file=f,
                    via_symlink=_via_symlink(f, base),
                    order=order,
                    plugin=plug,
                ))
            broken += [
                BrokenSymlink(harness=h.id, path=p)
                for p in _find_broken_symlinks(base, plug.recursive)
            ]
            order += 1
    return instances, broken, unreadable
```

(The existing native-path loop body stays exactly as it is; only the
signature, the trailing plugin block, and the return tuple change.)

- [ ] **Step 5: Update the callers of `discover()`**

Find every call site (`grep -rn "discover(" src tests`). In
`pipeline.py` (the `i, b = discover(h, project_root, home, global_only)`
line, currently pipeline.py:75) unpack the third element and extend the
world's unreadable list after `build_world` (which owns
`world.unreadable`):

```python
        i, b, u = discover(h, project_root, home, global_only)
        all_instances += i
        all_broken += b
        unreadable_states += u   # accumulate; initialize `unreadable_states = []` before the loop
```

and after the existing `world = build_world(...)` call:

```python
    world.unreadable += [p for p in unreadable_states if p not in world.unreadable]
```

Adjust any existing tests that unpack two values from `discover()` to
unpack three (mechanical `i, b = ` → `i, b, _u = `).

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_discovery.py tests/test_stores.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q` — fix any
remaining two-value unpacks of `discover()` the grep missed.

- [ ] **Step 7: Commit**

```bash
git add src/drskill/models.py src/drskill/discovery.py src/drskill/pipeline.py tests/test_discovery.py
git commit -m "feat(discovery): scan enabled plugin-store skill roots"
```

---

### Task 6: resolution provenance/suite stamping + claude-code shadow exclusion + suites skip

**Files:**
- Modify: `src/drskill/resolution.py` (`build_world` stamps provenance/suite; `_mark_shadows` exclusion)
- Modify: `src/drskill/suites.py` (`assign_suites` skips pre-attributed)
- Test: `tests/test_resolution.py`, `tests/test_suites.py` (append)

**Interfaces:**
- Consumes: `RawInstance.plugin`, `InstalledPlugin.provenance_source` (Tasks 1, 5).
- Produces: plugin contributors carry `source == Provenance(kind="plugin", source=...)` and `suite == plugin.name`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_resolution.py (reuse its existing world-building
# helpers/imports; the snippets below assume a helper that builds a World
# from RawInstances — follow the file's established pattern)

def test_plugin_instance_stamps_provenance_and_suite(tmp_path):
    from drskill.discovery import discover
    from drskill.harnesses import load_harnesses
    from drskill.resolution import build_world

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = home / ".claude" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    d = active / "skills" / "toolbox"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: toolbox\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 1, "plugins": {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": "1.0.0"},
    ]}}), encoding="utf-8")
    i, b, _u = discover(h, proj, home)
    world = build_world(i, {h.id: h}, b)
    (c,) = [c for c in world.contributors.values() if c.name == "toolbox"]
    assert c.source.kind == "plugin"
    assert c.source.source == "sp@mkt==1.0.0"
    assert c.suite == "sp"


def test_claude_code_plugin_native_name_pair_not_shadowed(tmp_path):
    # Claude Code namespaces plugin skills (plugin:skill), so a plugin
    # skill sharing a native skill's name is NOT a real load conflict.
    from drskill.discovery import discover
    from drskill.harnesses import load_harnesses
    from drskill.resolution import build_world

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    nd = proj / ".claude" / "skills" / "toolbox"
    nd.mkdir(parents=True)
    (nd / "SKILL.md").write_text(
        "---\nname: toolbox\ndescription: native\n---\nnative body\n",
        encoding="utf-8",
    )
    active = home / ".claude" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    pd = active / "skills" / "toolbox"
    pd.mkdir(parents=True)
    (pd / "SKILL.md").write_text(
        "---\nname: toolbox\ndescription: plugin\n---\nplugin body\n",
        encoding="utf-8",
    )
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 1, "plugins": {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": "1.0.0"},
    ]}}), encoding="utf-8")
    i, b, _u = discover(h, proj, home)
    world = build_world(i, {h.id: h}, b)
    for c in world.contributors.values():
        for d in c.deployments:
            assert d.shadowed_by is None


def test_gemini_native_shadows_extension_skill(tmp_path):
    # gemini-cli DOES shadow: user/workspace skills override extension
    # skills on a name collision (last-wins in skillManager).
    from drskill.discovery import discover
    from drskill.harnesses import load_harnesses
    from drskill.resolution import build_world

    h = next(x for x in load_harnesses() if x.id == "gemini-cli")
    home, proj = tmp_path / "home", tmp_path / "proj"
    nd = proj / ".gemini" / "skills" / "toolbox"
    nd.mkdir(parents=True)
    (nd / "SKILL.md").write_text(
        "---\nname: toolbox\ndescription: native\n---\nnative body\n",
        encoding="utf-8",
    )
    ext = home / ".gemini" / "extensions" / "ext-a"
    ed = ext / "skills" / "toolbox"
    ed.mkdir(parents=True)
    (ed / "SKILL.md").write_text(
        "---\nname: toolbox\ndescription: ext\n---\next body\n",
        encoding="utf-8",
    )
    (ext / "gemini-extension.json").write_text(
        json.dumps({"name": "ext-a", "version": "1.0.0"}), encoding="utf-8"
    )
    i, b, _u = discover(h, proj, home)
    world = build_world(i, {h.id: h}, b)
    ext_c = next(c for c in world.contributors.values()
                 if c.source.kind == "plugin")
    assert any(d.shadowed_by for d in ext_c.deployments)
```

```python
# append to tests/test_suites.py (uses that file's existing helpers:
# write_skill, plugin_cache, _contrib, _world)

def test_assign_suites_skips_preattributed_plugin_contributors(tmp_path):
    # A store-scanned contributor arrives with suite already set; the
    # content-hash registry must not overwrite it, even when the cache
    # would content-match it to a DIFFERENT plugin name.
    home = tmp_path / "home"
    skills = plugin_cache(home, "official", "other", "1.0.0")
    h = write_skill(skills / "toolbox", "toolbox", "Use when boxing tools.")
    c = _contrib("toolbox", h, source_kind="plugin", source="sp@mkt==1.0.0")
    c.suite = "sp"
    world = _world(c)
    suites.assign_suites(world, home)
    assert c.suite == "sp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resolution.py tests/test_suites.py -q`
Expected: FAIL — provenance stays "unmanaged", shadow marking hits the
claude-code pair, suites overwrite

- [ ] **Step 3: Modify `resolution.py`**

In `build_world` (resolution.py:208), after the contributor is created or
fetched, stamp store provenance (place right after the
`world.contributors[cid] = c` insertion, guarded so a native deployment
never overwrites and a plugin one wins only on first sight):

```python
        if inst.plugin is not None and c.source.kind == "unmanaged":
            c.source = Provenance(kind="plugin", source=inst.plugin.provenance_source)
            c.suite = inst.plugin.name
```

In `_mark_shadows` (resolution.py:242), skip claude-code plugin
contributors on both sides of the pairing:

```python
def _mark_shadows(world: World) -> None:
    for hid in world.harnesses:
        if world.harnesses[hid].search_order == "none":
            continue  # this harness keeps every same-name copy visible
        first_by_name: dict[str, Contributor] = {}
        for c, d in world.harness_loads(hid):
            # Claude Code namespaces plugin skills ("plugin:skill"), so a
            # plugin/native name collision is not a real load conflict
            # there: plugin instances neither shadow nor get shadowed.
            if hid == "claude-code" and c.source.kind == "plugin":
                continue
            prior = first_by_name.get(c.name)
            if prior is None:
                first_by_name[c.name] = c
            elif prior.id != c.id and prior.content_hash != c.content_hash:
                d.shadowed_by = prior.id
```

- [ ] **Step 4: Modify `suites.py`**

In `assign_suites`, skip pre-attributed store contributors (insert before
the content-hash lookup):

```python
        if c.kind != "skill":
            continue
        if c.source.kind == "plugin" and c.suite:
            continue  # store-scanned: suite pre-attributed by the adapter
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_resolution.py tests/test_suites.py tests/test_stores.py tests/test_discovery.py -q`
Expected: PASS. Then the full suite: `uv run pytest -q`.

- [ ] **Step 6: Commit**

```bash
git add src/drskill/resolution.py src/drskill/suites.py tests/test_resolution.py tests/test_suites.py
git commit -m "feat(resolution): plugin provenance, suite stamping, claude-code shadow exclusion"
```

---

### Task 7: README, real-machine gate, follow-ups log

**Files:**
- Modify: `README.md` (plugin-store coverage in the feature list / example-scans area, matching the file's existing tone and emoji-list style)
- Modify: `docs/superpowers/specs/2026-08-14-plugin-stores-design.md` (record gate results, any deviations)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (baseline was 611 before this feature).

- [ ] **Step 2: Real-machine gate (read-only; scan this machine)**

Run each and eyeball:

```bash
uv run drskill list | head -60          # superpowers skills appear suite-attributed with source "plugin"
uv run drskill scan | tail -30          # no crash; new findings only where real
uv run drskill scan --json > /dev/null; echo $?
```

Verify specifically:
- superpowers 6.3.0 skills appear for claude-code with `source` = `plugin`, `suite` = `superpowers`, scope `user`.
- The plumb project's 4.3.1 pin does NOT appear when scanning drskill's repo (project-scope confinement).
- The opencode `pyportal` double-load finding from 2026-08-13 is unchanged.
- Any NEW findings (plugin skills now participate in overlap/duplicates/injection) are real — read each once; leave acks to the user.

- [ ] **Step 3: README**

Add plugin-store coverage where the README enumerates what scan covers
(follow the existing phrasing style; one or two sentences naming the five
stores and that disabled plugins/stale versions are excluded).

- [ ] **Step 4: Record gate results in the spec**

Append a short "Shipped" note to the spec's Status line and record any
implementation deviations discovered during Tasks 1–6.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-08-14-plugin-stores-design.md
git commit -m "docs: plugin-store scanning in README; record gate results"
```

---

## Self-Review Notes

- Spec coverage: adapters (Tasks 1–4), models/discovery/precedence/enablement (Task 5), provenance/suite/shadow-exclusion/suites-skip (Task 6), CLI needs no task (list renders `source.kind` already), testing traps distributed across task tests, real-machine gate (Task 7). Follow-ups intentionally unplanned (spec logs them).
- The `world.unreadable` threading (Task 5 Step 5) names pipeline.py:75 but line numbers may drift — the grep instruction covers it.
- Task 6's suites test deliberately defers to `tests/test_suites.py`'s existing fixtures rather than inventing a parallel world-builder; the assertion contract is stated exactly.
