# Loadout MCP Server Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish MCP servers as installable loadout entries and teach `drskill loadout install` to write them into harness MCP configs, detect ones already installed, and hold drifted ones.

**Architecture:** The scanner already parses every harness's MCP config into an `MCPServer` model with transport, command, args, url, env variable names, and a normalized `config_hash` (`src/drskill/mcp.py`). The wizard currently publishes one throwaway entry per MCP tool. This plan changes the wizard to publish one entry per server with the sanitized config in the entry's `metadata`, then adds an install path that compares config hashes against the target config file and writes missing servers into it. The web service needs no change. `ValidateManifest` already accepts kind `mcp`, any `source_type`, and any `metadata`.

**Tech Stack:** Python 3.11+, typer, pydantic, pytest. No new dependencies.

**Spec:** The design was agreed in conversation and is restated here. Summary: a new `source_type: "mcp"` entry per server carrying `{server_name, transport, command, args, url, env_names, tools}` in metadata and `sha256:<config_hash>` as `content_hash`. Env values never leave the machine. Install writes only `mcp-json` format config files in v1 and prints a paste-ready block for other formats. Install matches by config hash for "already installed", holds a same-named server with a different hash unless `--force`, and suggests `drskill scan --mcp-connect` after installing. `loadout status` classifies mcp entries against the scanned servers.

## Global Constraints

- Python >= 3.11, stdlib plus existing dependencies only (typer, pydantic, rich, questionary).
- Run tests with `uv run pytest` from the repo root `/Users/dbreunig/Development/drskill`.
- Env variable VALUES must never be written into a manifest entry. Only names travel. `mcp.py` already discards values at parse time; do not add any code path that reads them back.
- Code comments state constraints the code cannot show. No comments that narrate the change or the plan.
- Keep `manifest_build.contributors_to_manifest` behavior unchanged for `mcp_tool` contributors. Old manifests with per-tool entries must still fetch, install (skipped as "other"), and classify ("unchecked") exactly as today.
- Commit after each task with a conventional prefix (`feat:`, `fix:`, `test:`).

## Design facts the tasks rely on

- An MCP tool contributor's id is `f"{config_hash}:{tool_name}"` (`src/drskill/pipeline.py:35`). `config_hash` is 64 hex chars, so `id.split(":", 1)[0]` recovers it.
- `World.mcp_servers` is `list[MCPServer]` and `World.mcp_snapshots` is `dict[config_hash, ServerSnapshot]` (`src/drskill/resolution.py:126-132`).
- `MCPServer.config_hash` is sha256 over `{"name", "transport", "command", "args", "url", "env_names"}` sorted-keys JSON (`src/drskill/mcp.py:85-91`). The hash covers the ORIGINAL server name, so the entry metadata must keep `server_name` verbatim; install writes the server under that name or the hash will never match on re-scan.
- Harness MCP config locations and formats come from `HarnessDef.mcp_project_configs`, `mcp_global_configs`, `mcp_format`, `mcp_format_global` (`src/drskill/harnesses.py:26-29`, data in `src/drskill/data/harnesses.toml`). Formats in use: `mcp-json` (claude-code project, cursor), `claude-user-json` (claude-code global), `codex-toml` (codex global).
- `mcp.parse_config(path, fmt, harness, scope, project_root)` returns `(list[MCPServer], errors)` and handles all three formats read-only.

---

### Task 1: Server entries in manifest_build

**Files:**
- Modify: `src/drskill/manifest_build.py`
- Test: `tests/test_manifest_build.py`

**Interfaces:**
- Consumes: `MCPServer` from `drskill.mcp` (existing model).
- Produces: `server_to_entry(server: MCPServer, tool_names: list[str]) -> dict` and `server_portability_notes(server: MCPServer) -> list[str]`. Tasks 2 and 4 use the entry shape exactly as defined here.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_manifest_build.py`:

```python
def make_server(**overrides):
    from drskill.mcp import MCPServer

    fields = dict(
        name="Notion", harness="claude-code", scope="project",
        source="/tmp/.mcp.json", transport="stdio",
        command="npx", args=["-y", "notion-mcp"],
        env_names=["NOTION_TOKEN"], config_hash="cc" * 32,
    )
    fields.update(overrides)
    return MCPServer(**fields)


def test_server_to_entry_stdio():
    entry = manifest_build.server_to_entry(make_server(), ["search", "create-page"])
    assert entry["kind"] == "mcp"
    assert entry["selector"] == "mcp:notion"
    assert entry["name"] == "notion"
    assert entry["source_type"] == "mcp"
    assert entry["source_reference"] == "npx -y notion-mcp"
    assert entry["content_hash"] == "sha256:" + "cc" * 32
    assert entry["local_only"] is False
    md = entry["metadata"]
    assert md["server_name"] == "Notion"
    assert md["transport"] == "stdio"
    assert md["command"] == "npx"
    assert md["args"] == ["-y", "notion-mcp"]
    assert md["url"] is None
    assert md["env_names"] == ["NOTION_TOKEN"]
    assert md["tools"] == ["create-page", "search"]


def test_server_to_entry_http():
    server = make_server(transport="http", command=None, args=[],
                         url="https://mcp.example.com/sse", env_names=[])
    entry = manifest_build.server_to_entry(server, [])
    assert entry["source_reference"] == "https://mcp.example.com/sse"
    assert entry["metadata"]["transport"] == "http"
    assert entry["metadata"]["url"] == "https://mcp.example.com/sse"


def test_portability_notes_flag_machine_local_paths():
    notes = manifest_build.server_portability_notes(
        make_server(command="/Users/drew/bin/server", args=["--db", "~/data.db"]))
    assert len(notes) == 2
    assert "/Users/drew/bin/server" in notes[0]
    assert "~/data.db" in notes[1]


def test_portability_notes_accept_resolvable_commands():
    assert manifest_build.server_portability_notes(make_server()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_manifest_build.py -v -k "server or portability"`
Expected: FAIL with `AttributeError: module 'drskill.manifest_build' has no attribute 'server_to_entry'`

- [ ] **Step 3: Implement**

Append to `src/drskill/manifest_build.py` (after `contributors_to_manifest`):

```python
def server_to_entry(server, tool_names: list[str]) -> dict:
    """One installable manifest entry per MCP server. The metadata is the
    sanitized config: env variable names only, never values. server_name
    keeps the original casing because config_hash covers it; install must
    write the server back under that exact name for hashes to match."""
    name = normalize_name(server.name)
    if server.transport == "http":
        source_reference = server.url or server.name
    else:
        source_reference = " ".join([server.command or "", *server.args]).strip() or server.name
    return {
        "kind": "mcp",
        "selector": f"mcp:{name}",
        "name": name,
        "source_type": "mcp",
        "source_reference": source_reference,
        "content_hash": f"sha256:{server.config_hash}",
        "local_only": False,
        "metadata": {
            "server_name": server.name,
            "transport": server.transport,
            "command": server.command,
            "args": server.args,
            "url": server.url,
            "env_names": server.env_names,
            "tools": sorted(tool_names),
        },
    }


def server_portability_notes(server) -> list[str]:
    """Warnings for config values that only resolve on this machine."""
    notes = []
    values = ([server.command] if server.command else []) + list(server.args)
    for value in values:
        if value.startswith(("/", "~", "./", "../")):
            notes.append(f"{server.name}: {value!r} is a machine-local path and "
                         "may not start on another machine")
    return notes
```

Add the type import at the top of the file:

```python
from drskill.mcp import MCPServer  # noqa: F401  (documents the server_to_entry parameter type)
```

Only add this import if it creates no circular import. `mcp.py` does not import `manifest_build`, so it is safe. If you prefer, skip the import and leave the parameter untyped like the rest of the module.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_manifest_build.py -v`
Expected: PASS, including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/manifest_build.py tests/test_manifest_build.py
git commit -m "feat: build installable manifest entries from MCP servers"
```

---

### Task 2: Wizard publishes one entry per server

**Files:**
- Modify: `src/drskill/loadout_wizard.py`
- Test: `tests/test_loadout_wizard.py`

**Interfaces:**
- Consumes: `manifest_build.server_to_entry(server, tool_names)` and `manifest_build.server_portability_notes(server)` from Task 1.
- Produces: `_mcp_server_entries(mcp_tools: list[Contributor], world) -> tuple[list[dict], list[str]]`, called only inside `run()`.

Two behavior changes. First, selected `mcp_tool` contributors stop flowing through `contributors_to_manifest` and `_offer_registry` and instead become one `source_type: "mcp"` entry per distinct server. Second, `_offer_registry` no longer sees MCP tools at all. Today it treats them as publishable local skills and would crash in `content.collect_files`; splitting fixes that.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loadout_wizard.py`:

```python
def mcp_tool_contributor(tool_name, config_hash):
    return Contributor(
        id=f"{config_hash}:{tool_name}",
        kind="mcp_tool",
        name=tool_name,
        scope="project",
        deployments=[Deployment(harness="claude-code", path=Path("/tmp/.mcp.json"),
                                scope="project", via_symlink=False, order=0)],
        token_cost=TokenCost(catalog_tokens=1, body_tokens=0),
        content_hash="sha256:" + "cd" * 32,
    )


def make_mcp_world(*skill_contributors):
    from drskill.mcp import MCPServer
    from drskill.mcp_connect import ServerSnapshot, ToolInfo

    cfg = "cc" * 32
    server = MCPServer(
        name="Notion", harness="claude-code", scope="project",
        source="/tmp/.mcp.json", transport="stdio",
        command="npx", args=["-y", "notion-mcp"],
        env_names=["NOTION_TOKEN"], config_hash=cfg,
    )
    tools = [mcp_tool_contributor("search", cfg), mcp_tool_contributor("create-page", cfg)]
    world = World(
        contributors={c.id: c for c in [*skill_contributors, *tools]},
        mcp_servers=[server],
        mcp_snapshots={cfg: ServerSnapshot(server="Notion", config_hash=cfg, date="2026-09-05",
                                           tools=[ToolInfo(name="search", description="d", schema_tokens=1),
                                                  ToolInfo(name="create-page", description="d", schema_tokens=1)])},
    )
    return world


def test_selected_mcp_tools_publish_one_server_entry(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_mcp_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_all)

    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    mcp_entries = [e for e in entries if e["kind"] == "mcp"]
    assert len(mcp_entries) == 1
    entry = mcp_entries[0]
    assert entry["source_type"] == "mcp"
    assert entry["local_only"] is False
    assert entry["metadata"]["server_name"] == "Notion"
    assert entry["metadata"]["tools"] == ["create-page", "search"]
    assert {e["name"] for e in entries} == {"alpha", "notion"}


def test_mcp_tools_are_not_offered_to_the_registry(wizard_env, monkeypatch):
    set_world(monkeypatch, make_mcp_world())
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_all)
    seen = {}

    original = loadout_wizard._offer_registry

    def spy(selected, creds, base_url, home):
        seen["selected"] = selected
        return original(selected, creds, base_url, home)

    monkeypatch.setattr(loadout_wizard, "_offer_registry", spy)
    runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert seen["selected"] == []


def test_stale_mcp_tool_is_skipped_with_a_note(wizard_env, monkeypatch):
    world = make_mcp_world(contributor("alpha"))
    world.mcp_servers = []
    set_world(monkeypatch, world)
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_all)

    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "no longer configured" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loadout_wizard.py -v -k mcp`
Expected: FAIL. The first test fails because the manifest carries two per-tool entries (`search`, `create-page`) instead of one `notion` entry, or because `_offer_registry` crashes on the tool contributors.

- [ ] **Step 3: Implement**

In `src/drskill/loadout_wizard.py`, replace the block in `run()` (currently lines 98-105):

```python
    selected = [row.contributor for row in selected_rows]
    if not selected:
        typer.echo("Nothing selected.")
        raise typer.Exit(1)

    hosted = _offer_registry(selected, creds, base_url, home)
    manifest, notes = manifest_build.contributors_to_manifest(selected, hosted=hosted)
    _print_summary(manifest, notes)
```

with:

```python
    selected = [row.contributor for row in selected_rows]
    if not selected:
        typer.echo("Nothing selected.")
        raise typer.Exit(1)

    skills = [c for c in selected if c.kind != "mcp_tool"]
    mcp_tools = [c for c in selected if c.kind == "mcp_tool"]
    hosted = _offer_registry(skills, creds, base_url, home)
    manifest, notes = manifest_build.contributors_to_manifest(skills, hosted=hosted)
    server_entries, server_notes = _mcp_server_entries(mcp_tools, world)
    manifest["entries"] += server_entries
    notes += server_notes
    _print_summary(manifest, notes)
```

Add the new function after `_offer_registry`:

```python
def _mcp_server_entries(mcp_tools, world) -> tuple[list[dict], list[str]]:
    """One entry per distinct server behind the selected MCP tools. A tool
    contributor's id is "<config_hash>:<tool name>". Selecting any tool
    publishes its whole server; a server installs as a unit."""
    by_hash = {s.config_hash: s for s in world.mcp_servers}
    picked: dict[str, object] = {}
    notes: list[str] = []
    for c in mcp_tools:
        cfg = c.id.split(":", 1)[0]
        server = by_hash.get(cfg)
        if server is None:
            notes.append(f"skipped MCP tool {c.name!r}: its server is no longer configured")
            continue
        picked.setdefault(cfg, server)

    entries: list[dict] = []
    used: set[str] = set()
    for cfg, server in picked.items():
        snap = world.mcp_snapshots.get(cfg)
        tool_names = [t.name for t in snap.tools] if snap else []
        entry = manifest_build.server_to_entry(server, tool_names)
        if entry["selector"] in used:
            notes.append(f"skipped a second server also named {server.name!r}")
            continue
        used.add(entry["selector"])
        entries.append(entry)
        notes += manifest_build.server_portability_notes(server)
    return entries, notes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loadout_wizard.py -v`
Expected: PASS, including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/loadout_wizard.py tests/test_loadout_wizard.py
git commit -m "feat: publish selected MCP tools as one installable entry per server"
```

---

### Task 3: MCP config writer module

**Files:**
- Create: `src/drskill/mcp_write.py`
- Test: `tests/test_mcp_write.py`

**Interfaces:**
- Consumes: `mcp.parse_config` and `MCPServer` (existing).
- Produces:
  - `read_servers(path: Path, fmt: str) -> list[MCPServer]` (missing file returns `[]`)
  - `server_block(metadata: dict) -> dict` (the JSON object install writes or prints)
  - `write_server(path: Path, name: str, block: dict) -> None` (mcp-json format only)
  - `WriteUnsupportedError(Exception)` with a `.message` attribute

v1 writes only `mcp-json` format files. `claude-user-json` (`~/.claude.json`) is a large harness-owned state file and `codex-toml` has no stdlib writer, so Task 4 prints a paste-ready block for those instead of editing them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_write.py`:

```python
import json
from pathlib import Path

import pytest

from drskill import mcp_write

STDIO_METADATA = {
    "server_name": "Notion", "transport": "stdio",
    "command": "npx", "args": ["-y", "notion-mcp"],
    "url": None, "env_names": ["NOTION_TOKEN"], "tools": ["search"],
}

HTTP_METADATA = {
    "server_name": "Linear", "transport": "http",
    "command": None, "args": [], "url": "https://mcp.linear.app/sse",
    "env_names": [], "tools": [],
}


def test_server_block_stdio():
    assert mcp_write.server_block(STDIO_METADATA) == {
        "command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": ""},
    }


def test_server_block_http():
    assert mcp_write.server_block(HTTP_METADATA) == {"url": "https://mcp.linear.app/sse"}


def test_write_server_creates_the_file(tmp_path):
    path = tmp_path / ".mcp.json"
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA))
    data = json.loads(path.read_text())
    assert data == {"mcpServers": {"Notion": {
        "command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": ""}}}}


def test_write_server_preserves_other_keys(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "unrelated": 1}))
    mcp_write.write_server(path, "Linear", mcp_write.server_block(HTTP_METADATA))
    data = json.loads(path.read_text())
    assert data["unrelated"] == 1
    assert set(data["mcpServers"]) == {"other", "Linear"}


def test_written_server_round_trips_through_the_parser(tmp_path):
    path = tmp_path / ".mcp.json"
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA))
    servers = mcp_write.read_servers(path, "mcp-json")
    assert len(servers) == 1
    assert servers[0].name == "Notion"
    assert servers[0].env_names == ["NOTION_TOKEN"]


def test_read_servers_missing_file_is_empty(tmp_path):
    assert mcp_write.read_servers(tmp_path / ".mcp.json", "mcp-json") == []


def test_write_server_rejects_other_formats(tmp_path):
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(tmp_path / "config.toml", "x", {"command": "x"},
                               fmt="codex-toml")


def test_write_server_rejects_a_corrupt_config(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("not json")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", {"command": "x"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_write.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'drskill.mcp_write'`

- [ ] **Step 3: Implement**

Create `src/drskill/mcp_write.py`:

```python
"""Write MCP server entries into harness config files.

Only the shared mcp-json format is writable. claude-user-json is Claude
Code's whole user state file and codex-toml has no stdlib writer, so
callers print a paste-ready block for those formats instead of editing
them. Env variables are written with empty values; the manifest never
carries values, only names."""

from __future__ import annotations

import json
from pathlib import Path

from drskill.mcp import MCPServer, parse_config


class WriteUnsupportedError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def read_servers(path: Path, fmt: str) -> list[MCPServer]:
    if not path.is_file():
        return []
    servers, _errors = parse_config(path, fmt, harness="", scope="project",
                                    project_root=path.parent)
    return servers


def server_block(metadata: dict) -> dict:
    if metadata.get("transport") == "http":
        return {"url": metadata.get("url")}
    block: dict = {"command": metadata.get("command"), "args": list(metadata.get("args") or [])}
    env_names = metadata.get("env_names") or []
    if env_names:
        block["env"] = {name: "" for name in env_names}
    return block


def write_server(path: Path, name: str, block: dict, fmt: str = "mcp-json") -> None:
    if fmt != "mcp-json":
        raise WriteUnsupportedError(f"{fmt} config files are not writable; add the server by hand")
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise WriteUnsupportedError(f"could not read {path}: {e}")
        if not isinstance(data, dict):
            raise WriteUnsupportedError(f"{path} is not a JSON object")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise WriteUnsupportedError(f"{path} has a non-object mcpServers key")
    servers[name] = block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
```

The parser round trip is exact: `parse_config` with `fmt="mcp-json"` reads `data.get("mcpServers")` (`src/drskill/mcp.py:132-138`), which is the same wrapper key `write_server` writes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_write.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/mcp_write.py tests/test_mcp_write.py
git commit -m "feat: add a writer for mcp-json harness config files"
```

---

### Task 4: Install MCP entries in `loadout install`

**Files:**
- Modify: `src/drskill/cli.py` (the `install` command at ~line 1981, plus two new helpers near `_install_one_github`)
- Test: `tests/test_cli_install.py`

**Interfaces:**
- Consumes: `mcp_write.read_servers`, `mcp_write.server_block`, `mcp_write.write_server`, `mcp_write.WriteUnsupportedError` (Task 3); `load_harnesses` (existing).
- Produces: `_mcp_config_target(harness_id, project, user, root, home) -> tuple[Path, str] | str` and `_install_one_mcp(entry, cfg_path, fmt, force) -> str` (returns `"installed" | "unchanged" | "held" | "manual" | "failed"`).

Behavior:

- Entries with `source_type == "mcp"` join the install listing as `  <name>  (MCP server, <transport>)` and no longer count as "other source types".
- Target resolution. With `--harness`, use that harness's MCP config: the first of `mcp_project_configs` at project scope or `mcp_global_configs` at user scope, with the matching format. With no `--harness`, project scope targets `<root>/.mcp.json` in `mcp-json` format, and user scope has no safe shared target, so the entries go to the manual path. Scope follows the same rule as skills: `--project`/`--user` win, else project when `root/.git` or `root/.agents` exists.
- When the resolved format is not `mcp-json`, or resolution returns an explanation string, print the explanation plus a paste-ready JSON block and count the entry as `manual`.
- Already installed: a server in the target config whose `"sha256:" + config_hash` equals the entry's `content_hash` prints `already installed` and counts as `unchanged`.
- Drift: a server with the same name but a different hash prints `local config differs; rerun with --force to replace it` and counts as `held`. With `--force` it is overwritten.
- Absent: write it, print `installed`, and if the entry has `env_names`, print `  fill in env values for: NAME1, NAME2` with the config path.
- When MCP entries are listed, print one warning line before the confirmation: `Installing an MCP server gives your agent live access to its tools.` After any MCP install, print `Run drskill scan --mcp-connect to review the new server's tools.`
- Counts line gains ` · N manual` when `manual` is nonzero. `manual` does not affect the exit code.
- MCP entries never join `bridged` (bridging is for skill directories).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_install.py`:

```python
MCP_HASH = "cc" * 32


def mcp_entry(**metadata_overrides):
    metadata = {"server_name": "Notion", "transport": "stdio",
                "command": "npx", "args": ["-y", "notion-mcp"], "url": None,
                "env_names": ["NOTION_TOKEN"], "tools": ["search"]}
    metadata.update(metadata_overrides)
    return {"kind": "mcp", "selector": "mcp:notion", "name": "notion",
            "source_type": "mcp", "source_reference": "npx -y notion-mcp",
            "content_hash": f"sha256:{MCP_HASH}", "local_only": False,
            "metadata": metadata}


def real_mcp_entry():
    # Built through the real pipeline so content_hash matches what a
    # re-parse of the written file computes.
    from pathlib import Path

    from drskill import manifest_build
    from drskill.mcp import _entry_to_server

    server = _entry_to_server(
        "Notion",
        {"command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": "x"}},
        harness="claude-code", scope="project", source=Path("/tmp/.mcp.json"))
    return manifest_build.server_to_entry(server, ["search"])


def test_mcp_entry_installs_into_project_mcp_json(env):
    _, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["Notion"] == {
        "command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": ""}}
    assert "fill in env values for: NOTION_TOKEN" in result.output
    assert "scan --mcp-connect" in result.output
    assert "live access" in result.output


def test_mcp_reinstall_is_a_no_op(env):
    _, project, state = env
    state["manifest"] = manifest([real_mcp_entry()])
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output
    assert "1 already installed" in result.output


def test_mcp_drifted_server_needs_force(env):
    _, project, state = env
    state["manifest"] = manifest([real_mcp_entry()])
    (project / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"Notion": {"command": "other-command"}}}))
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "differs" in result.output
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["Notion"]["command"] == "other-command"

    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--project", "--force"], input="y\n")
    assert result.exit_code == 0, result.output
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["Notion"]["command"] == "npx"


def test_mcp_user_scope_prints_a_manual_block(env):
    home, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--user"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "1 manual" in result.output
    assert '"command": "npx"' in result.output
    assert not (project / ".mcp.json").exists()


def test_mcp_codex_harness_prints_a_manual_block(env):
    _, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    (project / ".codex").mkdir()
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "1 manual" in result.output


def test_mixed_manifest_installs_both_kinds(env):
    home, project, state = env
    state["manifest"] = manifest([hosted_entry(), mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (project / ".agents" / "skills" / "vector" / "SKILL.md").exists()
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["Notion"]
    assert "2 installed" in result.output
```

Note: `test_mcp_reinstall_is_a_no_op` and `test_mcp_drifted_server_needs_force` use `real_mcp_entry()` because "already installed" is a hash comparison against a re-parse of the written file. The hand-built `mcp_entry()` hash (`cc...`) never matches a parsed file, which is exactly what the drift test relies on when it seeds a same-named server.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_install.py -v -k mcp`
Expected: FAIL. Today the MCP entry falls into "no installable entries" (exit 0 with nothing written) or the "other source types" path, so the first assertion on `.mcp.json` fails.

- [ ] **Step 3: Implement the target resolver and per-entry installer**

Add to `src/drskill/cli.py` after `_install_one_github`:

```python
def _mcp_config_target(harness_id: str | None, project: bool, user: bool,
                       root: Path, home: Path) -> tuple[Path, str] | str:
    """(config path, format) for MCP installs, or an explanation string
    when no writable target exists. Formats other than mcp-json go to the
    manual path in the caller."""
    from drskill.harnesses import load_harnesses

    in_project = project or (not user and ((root / ".git").exists() or (root / ".agents").exists()))
    if harness_id is None:
        if in_project:
            return root / ".mcp.json", "mcp-json"
        return ("there is no shared user-scope MCP config; pass --harness to "
                "target a specific harness")
    hd = next((h for h in load_harnesses() if h.id == harness_id), None)
    if hd is None:
        return f"unknown harness {harness_id!r}"
    specs = hd.mcp_project_configs if in_project else hd.mcp_global_configs
    if not specs:
        scope = "project" if in_project else "user"
        return f"{hd.display_name} has no {scope}-scope MCP config"
    fmt = hd.mcp_format if in_project else (hd.mcp_format_global or hd.mcp_format)
    spec = specs[0]
    path = root / spec if in_project else home / spec.removeprefix("~/")
    return path, fmt


def _install_one_mcp(entry: dict, cfg_path: Path, fmt: str, *, force: bool) -> str:
    from drskill import mcp_write

    metadata = entry.get("metadata") or {}
    name = metadata.get("server_name") or entry["name"]
    block = mcp_write.server_block(metadata)
    if fmt != "mcp-json":
        _echo_manual_mcp(entry, name, block, f"{cfg_path} is {fmt} and not writable")
        return "manual"
    existing = {s.name: s for s in mcp_write.read_servers(cfg_path, fmt)}
    current = existing.get(name)
    if current is not None:
        if f"sha256:{current.config_hash}" == entry.get("content_hash"):
            typer.echo(f"  {entry['name']}: already installed")
            return "unchanged"
        if not force:
            typer.echo(f"  {entry['name']}: local config differs; rerun with --force to replace it")
            return "held"
    try:
        mcp_write.write_server(cfg_path, name, block)
    except mcp_write.WriteUnsupportedError as err:
        _echo_manual_mcp(entry, name, block, err.message)
        return "manual"
    typer.echo(f"  {entry['name']}: {'replaced' if current else 'installed'} "
               f"in {_display_path(cfg_path)}")
    env_names = metadata.get("env_names") or []
    if env_names:
        typer.echo(f"    fill in env values for: {', '.join(env_names)}")
    return "installed"


def _echo_manual_mcp(entry: dict, name: str, block: dict, reason: str) -> None:
    typer.echo(f"  {entry['name']}: {reason}; add it by hand:")
    typer.echo(textwrap.indent(json.dumps({name: block}, indent=2), "    "))
```

`textwrap` needs an import at the top of `cli.py` if it is not already there.

- [ ] **Step 4: Wire the entry class into the `install` command**

In the `install` command body, change the partition (currently lines 2020-2027):

```python
    entries = json.loads(document).get("entries", [])
    hosted = [e for e in entries if e.get("source_type") == "drskill"]
    github = [e for e in entries if e.get("source_type") == "github"]
    mcp = [e for e in entries if e.get("source_type") == "mcp"]
    other = len(entries) - len(hosted) - len(github) - len(mcp)
    installable = len(hosted) + len(github) + len(mcp)
```

Extend the listing (after the github loop, before the `other` message):

```python
    for entry in mcp:
        transport = (entry.get("metadata") or {}).get("transport", "?")
        typer.echo(f"  {entry['name']}  (MCP server, {transport})")
    if mcp:
        typer.echo("Installing an MCP server gives your agent live access to its tools.")
```

Leave the `Install N skills into ...` header line untouched; the existing tests assert around it and the new per-entry lines carry the MCP information.

Replace the counts dict with a five-key one:

```python
    counts = {"installed": 0, "unchanged": 0, "held": 0, "failed": 0, "manual": 0}
```

Then add the MCP loop after the github entries loop. The `scan --mcp-connect` hint must fire only when an MCP server was written, not when a skill was, so track the MCP statuses in a local list:

```python
    if mcp:
        target_or_reason = _mcp_config_target(harness, project, user, root, home)
        mcp_statuses = []
        for entry in mcp:
            if isinstance(target_or_reason, str):
                metadata = entry.get("metadata") or {}
                name = metadata.get("server_name") or entry["name"]
                from drskill import mcp_write
                _echo_manual_mcp(entry, name, mcp_write.server_block(metadata), target_or_reason)
                mcp_statuses.append("manual")
                continue
            cfg_path, fmt = target_or_reason
            mcp_statuses.append(_install_one_mcp(entry, cfg_path, fmt, force=force))
        for status in mcp_statuses:
            counts[status] += 1
        if "installed" in mcp_statuses:
            typer.echo("Run drskill scan --mcp-connect to review the new server's tools.")
```

Extend the summary line:

```python
    if counts["manual"]:
        parts.append(f"{counts['manual']} manual")
```

(insert before the `failed` part). Leave the exit-code condition unchanged; `manual` is not a failure.

One more adjustment: the early exit `if not installable` stays as is, and `_offer_bridges` still receives only skill installs because MCP entries never append to `bridged`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_install.py -v`
Expected: PASS, including all pre-existing install tests. If `test_install_hosted_and_github_entries` fails on the header wording, keep the original header text and adjust only what the new tests assert.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/drskill/cli.py tests/test_cli_install.py
git commit -m "feat: install loadout MCP servers into harness configs"
```

---

### Task 5: `loadout status` classifies MCP entries

**Files:**
- Modify: `src/drskill/loadout_drift.py`, `src/drskill/cli.py` (the `status` command, ~line 1795)
- Test: `tests/test_loadout_drift.py`

**Interfaces:**
- Consumes: the entry shape from Task 1; `MCPServer` (existing).
- Produces: `classify_entries(entries, contributors, servers=None)` gains an optional `servers: list[MCPServer] | None` third parameter. Existing callers without it behave exactly as today.

Classification for an entry with `kind == "mcp"` and `source_type == "mcp"` when `servers` is not None:

- some server's `"sha256:" + config_hash` equals the entry's `content_hash` → `matches`
- else some server's name equals `metadata["server_name"]` (or the normalized name equals the entry name) → `changed`
- else → `missing`

All other non-skill entries, including old per-tool mcp entries with `source_type == "local"`, stay `unchecked`. When `servers` is None every mcp entry stays `unchecked`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loadout_drift.py` (reuse that file's existing imports and helpers; add these):

```python
def drift_server(name="Notion", config_hash="cc" * 32):
    from drskill.mcp import MCPServer

    return MCPServer(name=name, harness="claude-code", scope="project",
                     source="/tmp/.mcp.json", transport="stdio",
                     command="npx", args=["-y", "notion-mcp"],
                     env_names=[], config_hash=config_hash)


def drift_mcp_entry(content_hash="sha256:" + "cc" * 32, server_name="Notion"):
    return {"kind": "mcp", "selector": "mcp:notion", "name": "notion",
            "source_type": "mcp", "source_reference": "npx -y notion-mcp",
            "content_hash": content_hash, "local_only": False,
            "metadata": {"server_name": server_name, "transport": "stdio",
                         "command": "npx", "args": ["-y", "notion-mcp"],
                         "url": None, "env_names": [], "tools": []}}


def test_mcp_entry_matches_a_configured_server():
    statuses = loadout_drift.classify_entries(
        [drift_mcp_entry()], [], servers=[drift_server()])
    assert statuses[0].state == "matches"


def test_mcp_entry_with_a_drifted_config_is_changed():
    statuses = loadout_drift.classify_entries(
        [drift_mcp_entry()], [], servers=[drift_server(config_hash="dd" * 32)])
    assert statuses[0].state == "changed"


def test_mcp_entry_with_no_server_is_missing():
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[])
    assert statuses[0].state == "missing"


def test_mcp_entry_without_servers_stays_unchecked():
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [])
    assert statuses[0].state == "unchecked"


def test_legacy_per_tool_entry_stays_unchecked():
    entry = {"kind": "mcp", "selector": "mcp:search", "name": "search",
             "source_type": "local", "source_reference": "x",
             "content_hash": "sha256:" + "ab" * 32, "local_only": True,
             "metadata": {}}
    statuses = loadout_drift.classify_entries([entry], [], servers=[drift_server()])
    assert statuses[0].state == "unchecked"
```

The module import name in that file may be `loadout_drift` or something else; match the file's existing imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loadout_drift.py -v -k mcp`
Expected: FAIL with `TypeError: classify_entries() got an unexpected keyword argument 'servers'`

- [ ] **Step 3: Implement**

In `src/drskill/loadout_drift.py`, change the signature and the non-skill branch:

```python
def classify_entries(entries: list[dict], contributors: list[Contributor],
                     servers: list | None = None) -> list[EntryStatus]:
    skills = [c for c in contributors if c.kind == "skill"]
    by_name: dict[str, list[Contributor]] = {}
    for c in skills:
        by_name.setdefault(normalize_name(c.name), []).append(c)

    out: list[EntryStatus] = []
    for entry in entries:
        if entry.get("kind") != "skill":
            if entry.get("source_type") == "mcp" and servers is not None:
                out.append(EntryStatus(entry, None, _mcp_state(entry, servers)))
            else:
                out.append(EntryStatus(entry, None, "unchecked"))
            continue
        ...  # the skill branch is unchanged
    return out


def _mcp_state(entry: dict, servers: list) -> str:
    expected = entry.get("content_hash")
    if any(f"sha256:{s.config_hash}" == expected for s in servers):
        return "matches"
    metadata = entry.get("metadata") or {}
    wanted = metadata.get("server_name")
    for s in servers:
        if s.name == wanted or normalize_name(s.name) == entry.get("name"):
            return "changed"
    return "missing"
```

In `src/drskill/cli.py`, the `status` command's `classify_entries` call becomes:

```python
        for st in loadout_drift.classify_entries(entries, contributors,
                                                 servers=world.mcp_servers):
```

The `update` command's call stays two-argument on purpose. `update` republishes changed skills from local files; republishing a changed MCP config is a separate feature, and passing servers there would make `update` see mcp entries as "changed" with no code to refresh them.

`_STATUS_LINES` in `cli.py` already covers `matches`, `changed`, `missing`, and `unchecked`. Change the `unchecked` label from `"not checked (mcp)"` to `"not checked"` since checked mcp entries now exist.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loadout_drift.py tests/test_cli_status.py -v`
Expected: PASS. If a test in `test_cli_status.py` asserts the literal `"not checked (mcp)"` label, update it to `"not checked"`.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/loadout_drift.py src/drskill/cli.py tests/test_loadout_drift.py tests/test_cli_status.py
git commit -m "feat: report MCP server drift in loadout status"
```

---

### Task 6: Document MCP entries

**Files:**
- Modify: `README.md` (the "MCP servers" section, ~line 421)

- [ ] **Step 1: Write the docs**

Add a subsection at the end of the "MCP servers" section (before "The ledger"):

```markdown
### MCP servers in loadouts

`drskill loadout create` lists the MCP tools it finds next to your skills.
Selecting a tool adds its whole server to the loadout as one entry. The
entry records the server's transport, command, arguments, url, and the
names of its env variables. Env values never leave your machine.

`drskill loadout install` writes each MCP entry into the target MCP
config. In a project it writes `.mcp.json`. Pass `--harness` to target a
harness that reads a different file. A server that is already configured
with the same settings is reported as already installed. A server with
the same name but different settings is held unless you pass `--force`.
Configs that drskill cannot write, such as Codex's `config.toml`, get a
printed block you can paste in yourself.

After installing a server, fill in its env values and run
`drskill scan --mcp-connect` to review the tools it exposes.

`drskill loadout status` compares each MCP entry against your configured
servers and reports matches, changed, or missing.
```

Adjust the placement and heading level to match the surrounding file.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: describe MCP server entries in loadouts"
```
