# MCP Static Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** drskill reads every harness's MCP server configs (plus Claude Desktop's), resolves per-harness server sets, and ships seven read-only checks: invalid config, shadowed, diverged, secrets, unpinned, insecure URL, dead command.

**Architecture:** `src/drskill/mcp.py` owns the `MCPServer` model, one parser per format, secret detection at parse time (values are dropped before the model is built, so no secret ever exists on the model), and discovery. `World` gains `mcp_servers` and `mcp_config_errors`. `checks/mcp.py` registers seven ordinary checks whose findings carry the source file path as the contributor id and the server name as the contributor name, so ack/review/show work unchanged. Report gains the server count, an `mcp_verified` facet for `?` markers, and `list --mcp`.

**Tech Stack:** Existing only (pydantic, tomllib, json, shutil). No new dependencies, no network, no process execution.

**Spec:** `docs/superpowers/specs/2026-07-21-mcp-static-design.md`

## Global Constraints

- A secret value must never appear in any model, finding, fingerprint, ledger, or output. Detection happens in the parser; values are discarded there.
- Secret-check fingerprints hash server identity plus offending variable names, never values.
- `mcp_verified` is set true in `harnesses.toml` only for formats actually verified against official docs during implementation; unverified formats keep `false` and render `?`.
- Findings carry evidence: file path, server name, offending fields.
- Every test sets `DRSKILL_HOME`. Stage only named files.
- Check ids and severities exactly as the spec's table.

---

### Task 1: MCPServer model, secret detection, and the four parsers

**Files:**
- Create: `src/drskill/mcp.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Produces:
  - `class MCPServer(BaseModel)`: `name: str`, `harness: str`, `scope: Literal["project","user"]`, `source: str` (str path of the config file), `transport: Literal["stdio","http"]`, `command: str | None`, `args: list[str]`, `url: str | None`, `env_names: list[str]`, `suspect_env: list[str]` (names whose literal values looked like credentials), `config_hash: str` (sha256 over the normalized entry, no values)
  - `looks_secret(name: str, value: str) -> bool`
  - `parse_config(path: Path, fmt: str, harness: str, scope: str, project_root: Path) -> tuple[list[MCPServer], list[str]]` where `fmt` is one of `"mcp-json"` (a JSON file with an `mcpServers` object — Claude Code project, Cursor, Gemini, Claude Desktop, Cline), `"claude-user-json"` (`~/.claude.json`: top-level `mcpServers` is user scope, and `projects[<realpath>].mcpServers` is project scope), `"vscode-json"` (`servers` key), `"codex-toml"` (`[mcp_servers.<name>]` tables). Errors are returned as messages, one per unparsable file.

- [ ] **Step 1: Write the failing tests** — create `tests/test_mcp.py`:

```python
import json
from pathlib import Path

from drskill import mcp


def test_looks_secret_prefixes_names_and_references():
    assert mcp.looks_secret("ANY", "sk-ant-abcdef1234567890abcdef")
    assert mcp.looks_secret("ANY", "ghp_16charslong16charslong16charslong")
    assert mcp.looks_secret("GITHUB_TOKEN", "hunter2value")  # name rule
    assert mcp.looks_secret("API_KEY", "x" * 8)
    assert not mcp.looks_secret("GITHUB_TOKEN", "${GITHUB_TOKEN}")  # reference is fine
    assert not mcp.looks_secret("LOG_LEVEL", "debug")


def test_parse_mcp_json(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_TOKEN": "ghp_16charslong16charslong16charslong"}},
        "remote": {"url": "http://example.com/sse"},
    }}))
    servers, errors = mcp.parse_config(p, "mcp-json", "claude-code", "project", tmp_path)
    assert errors == []
    by_name = {s.name: s for s in servers}
    gh = by_name["github"]
    assert gh.transport == "stdio" and gh.command == "npx"
    assert gh.env_names == ["GITHUB_TOKEN"] and gh.suspect_env == ["GITHUB_TOKEN"]
    assert "ghp_" not in gh.model_dump_json()  # the value is gone entirely
    rm = by_name["remote"]
    assert rm.transport == "http" and rm.url == "http://example.com/sse"


def test_parse_claude_user_json_scopes(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "mcpServers": {"global-srv": {"command": "uvx", "args": ["srv"]}},
        "projects": {str(proj.resolve()): {"mcpServers": {"proj-srv": {"command": "x"}}}},
    }))
    servers, errors = mcp.parse_config(p, "claude-user-json", "claude-code", "user", proj)
    scopes = {s.name: s.scope for s in servers}
    assert scopes == {"global-srv": "user", "proj-srv": "project"}


def test_parse_vscode_and_codex(tmp_path):
    v = tmp_path / "mcp.json"
    v.write_text(json.dumps({"servers": {"fetchy": {"command": "fetchy-bin"}}}))
    servers, _ = mcp.parse_config(v, "vscode-json", "copilot", "project", tmp_path)
    assert [s.name for s in servers] == ["fetchy"]
    c = tmp_path / "config.toml"
    c.write_text('[mcp_servers.docs]\ncommand = "docs-mcp"\nargs = ["--stdio"]\n')
    servers, _ = mcp.parse_config(c, "codex-toml", "codex", "user", tmp_path)
    assert servers[0].name == "docs" and servers[0].command == "docs-mcp"


def test_parse_error_is_reported_not_raised(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text("{not json")
    servers, errors = mcp.parse_config(p, "mcp-json", "claude-code", "project", tmp_path)
    assert servers == [] and len(errors) == 1


def test_config_hash_ignores_values_and_orders(tmp_path):
    def entry(env):
        p = tmp_path / "a.json"
        p.write_text(json.dumps({"mcpServers": {"s": {"command": "c", "env": env}}}))
        (srv,), _ = mcp.parse_config(p, "mcp-json", "h", "project", tmp_path)
        return srv
    a = entry({"K1": "value-one", "K2": "x"})
    b = entry({"K2": "y", "K1": "value-two"})  # same names, different values/order
    assert a.config_hash == b.config_hash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp.py -v`
Expected: collection error, `No module named 'drskill.mcp'`

- [ ] **Step 3: Implement** — create `src/drskill/mcp.py`:

```python
"""MCP server discovery: the static half. Parses each harness's MCP config
files read-only. Secret-shaped env values are detected here in the parser
and immediately discarded; no secret value ever exists on a model, in a
fingerprint, or in any output."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

_SECRET_PREFIXES = (
    "sk-", "sk_live_", "pk_live_", "ghp_", "github_pat_", "gho_",
    "xoxb-", "xoxp-", "xapp-", "AKIA", "ASIA", "glpat-", "AIza", "ntn_",
)
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)$", re.IGNORECASE)


def looks_secret(name: str, value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("${") and value.endswith("}"):
        return False  # a reference, resolved by the harness at launch
    if value.startswith(_SECRET_PREFIXES):
        return True
    if _SECRET_NAME.search(name):
        return True
    # a long single token with mixed classes and no spaces reads as a credential
    if len(value) >= 32 and " " not in value and re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value):
        return True
    return False


class MCPServer(BaseModel):
    name: str
    harness: str
    scope: Literal["project", "user"]
    source: str  # str(path of the config file)
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_names: list[str] = Field(default_factory=list)
    suspect_env: list[str] = Field(default_factory=list)
    config_hash: str = ""


def _entry_to_server(
    name: str, entry: dict, harness: str, scope: str, source: Path
) -> MCPServer:
    url = entry.get("url") or entry.get("serverUrl")
    env = entry.get("env") or {}
    env = env if isinstance(env, dict) else {}
    names = sorted(str(k) for k in env)
    suspect = sorted(k for k, v in env.items() if looks_secret(str(k), v))
    command = entry.get("command")
    args = [str(a) for a in entry.get("args") or []]
    normalized = json.dumps(
        {
            "name": name, "transport": "http" if url else "stdio",
            "command": command, "args": args, "url": url, "env_names": names,
        },
        sort_keys=True,
    )
    return MCPServer(
        name=name, harness=harness, scope=scope, source=str(source),
        transport="http" if url else "stdio",
        command=command, args=args, url=url,
        env_names=names, suspect_env=suspect,
        config_hash=hashlib.sha256(normalized.encode()).hexdigest(),
    )


def _servers_from_map(
    data: dict, harness: str, scope: str, source: Path
) -> list[MCPServer]:
    out = []
    for name, entry in data.items():
        if isinstance(entry, dict):
            out.append(_entry_to_server(str(name), entry, harness, scope, source))
    return out


def parse_config(
    path: Path, fmt: str, harness: str, scope: str, project_root: Path
) -> tuple[list[MCPServer], list[str]]:
    try:
        text = path.read_text()
        if fmt == "codex-toml":
            data = tomllib.loads(text)
            table = data.get("mcp_servers") or {}
            return _servers_from_map(table, harness, scope, path), []
        data = json.loads(text)
    except Exception as e:
        return [], [f"{path}: {type(e).__name__}: {e}"]
    if not isinstance(data, dict):
        return [], [f"{path}: expected a JSON object"]
    if fmt == "mcp-json":
        return _servers_from_map(data.get("mcpServers") or {}, harness, scope, path), []
    if fmt == "vscode-json":
        return _servers_from_map(data.get("servers") or {}, harness, scope, path), []
    if fmt == "claude-user-json":
        out = _servers_from_map(data.get("mcpServers") or {}, harness, "user", path)
        projects = data.get("projects") or {}
        proj_entry = projects.get(str(project_root.resolve())) or {}
        out += _servers_from_map(
            proj_entry.get("mcpServers") or {}, harness, "project", path
        )
        return out, []
    return [], [f"{path}: unknown MCP config format '{fmt}'"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/mcp.py tests/test_mcp.py
git commit -m "feat: MCP server model, secret detection, and config parsers"
```

---

### Task 2: Harness data, discovery, and World wiring

**Files:**
- Modify: `src/drskill/harnesses.py` (HarnessDef fields)
- Modify: `src/drskill/data/harnesses.toml` (per-harness MCP entries + claude-desktop harness)
- Modify: `src/drskill/mcp.py` (discover_servers)
- Modify: `src/drskill/resolution.py` (World fields)
- Modify: `src/drskill/pipeline.py` (wire discovery into run_scan)
- Test: `tests/test_mcp.py`

**Interfaces:**
- Produces:
  - `HarnessDef` gains `mcp_project_configs: list[str] = []`, `mcp_global_configs: list[str] = []`, `mcp_format: str = "mcp-json"`, `mcp_verified: bool = False`
  - `mcp.discover_servers(harnesses: dict[str, HarnessDef], project_root: Path, home: Path, global_only: bool) -> tuple[list[MCPServer], list[tuple[str, str]]]` returning `(servers, config_errors)` where an error tuple is `(harness_id, message)`
  - `World.mcp_servers: list[MCPServer] = []`, `World.mcp_config_errors: list[tuple[str, str]] = []`
  - `run_scan` populates both on every scan.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_mcp.py`:

```python
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.pipeline import run_scan


def test_harness_data_has_mcp_entries():
    hs = {h.id: h for h in load_harnesses()}
    assert ".mcp.json" in hs["claude-code"].mcp_project_configs
    assert "~/.claude.json" in hs["claude-code"].mcp_global_configs
    assert hs["claude-code"].mcp_format == "claude-user-json" or hs["claude-code"].mcp_format == "mcp-json"
    assert "claude-desktop" in hs
    assert hs["claude-desktop"].project_paths == []  # no skills, only MCP


def test_run_scan_discovers_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)  # detect claude-code
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    }}))
    world, findings = run_scan(proj, home, config=Config())
    names = {(s.harness, s.name, s.scope) for s in world.mcp_servers}
    assert ("claude-code", "github", "project") in names


def test_run_scan_reports_config_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text("{broken")
    world, findings = run_scan(proj, home, config=Config())
    assert world.mcp_config_errors and world.mcp_config_errors[0][0] == "claude-code"
```

Note on the claude-code format: `.mcp.json` (project) is plain `mcp-json`; `~/.claude.json` (user) is `claude-user-json`. `discover_servers` therefore takes the format per path list: project configs parse with `mcp_format_project`, global configs with `mcp_format_global`. To keep the data model simple, give `HarnessDef` two fields (`mcp_format` for project configs, `mcp_format_global` defaulting to the same value) and set claude-code's global format to `claude-user-json`. Adjust the first test accordingly when implementing: assert `hs["claude-code"].mcp_format_global == "claude-user-json"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp.py -k "harness_data or discovers or config_errors" -v`
Expected: FAIL (`mcp_project_configs` not a HarnessDef field)

- [ ] **Step 3: Implement**

`src/drskill/harnesses.py`, add to `HarnessDef`:

```python
    mcp_project_configs: list[str] = Field(default_factory=list)
    mcp_global_configs: list[str] = Field(default_factory=list)
    mcp_format: str = "mcp-json"
    mcp_format_global: str | None = None  # defaults to mcp_format when None
    mcp_verified: bool = False
```

`src/drskill/data/harnesses.toml`: add to the existing entries (only the harnesses below claim MCP configs; verify each claim against official docs before setting `mcp_verified = true`, and leave it `false` with the paths still listed when documentation is thin):

- `claude-code`: `mcp_project_configs = [".mcp.json"]`, `mcp_global_configs = ["~/.claude.json"]`, `mcp_format = "mcp-json"`, `mcp_format_global = "claude-user-json"`
- `cursor`: `mcp_project_configs = [".cursor/mcp.json"]`, `mcp_global_configs = ["~/.cursor/mcp.json"]`
- `copilot` (VS Code): `mcp_project_configs = [".vscode/mcp.json"]`, `mcp_format = "vscode-json"`
- `codex`: `mcp_global_configs = ["~/.codex/config.toml"]`, `mcp_format = "codex-toml"`
- `gemini-cli`: `mcp_project_configs = [".gemini/settings.json"]`, `mcp_global_configs = ["~/.gemini/settings.json"]`
- `cline`: `mcp_global_configs = ["~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"]` (best effort, `mcp_verified` false)

and a new harness table:

```toml
[[harness]]
id = "claude-desktop"
display_name = "Claude Desktop"
detect = ["~/Library/Application Support/Claude/claude_desktop_config.json"]
mcp_global_configs = ["~/Library/Application Support/Claude/claude_desktop_config.json"]
```

`src/drskill/mcp.py`, append:

```python
def discover_servers(
    harnesses: dict, project_root: Path, home: Path, global_only: bool = False
) -> tuple[list[MCPServer], list[tuple[str, str]]]:
    servers: list[MCPServer] = []
    errors: list[tuple[str, str]] = []
    for hid, h in sorted(harnesses.items()):
        sources = []
        if not global_only:
            sources += [
                (project_root / s, "project", h.mcp_format) for s in h.mcp_project_configs
            ]
        gfmt = h.mcp_format_global or h.mcp_format
        sources += [
            (home / s.removeprefix("~/"), "user", gfmt) for s in h.mcp_global_configs
        ]
        for path, scope, fmt in sources:
            if not path.is_file():
                continue
            found, errs = parse_config(path, fmt, hid, scope, project_root)
            servers += found
            errors += [(hid, e) for e in errs]
    return servers, errors
```

`src/drskill/resolution.py`, add to `World` (import `MCPServer` from `drskill.mcp`):

```python
    mcp_servers: list = Field(default_factory=list)
    mcp_config_errors: list[tuple[str, str]] = Field(default_factory=list)
```

(Use `list` untyped or `list[MCPServer]` with a direct import; `drskill.mcp` imports nothing from `resolution`, so `from drskill.mcp import MCPServer` is cycle-free.)

`src/drskill/pipeline.py`, after `world.lockfile = load_lockfile(project_root)` block:

```python
    from drskill import mcp as mcp_discovery

    world.mcp_servers, world.mcp_config_errors = mcp_discovery.discover_servers(
        world.harnesses, project_root, home, global_only
    )
```

(Top-level import is fine too; `mcp.py` is light. Prefer the top-level import for consistency.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (existing harness/report tests unaffected: new fields have defaults)

- [ ] **Step 5: Commit**

```bash
git add src/drskill/harnesses.py src/drskill/data/harnesses.toml src/drskill/mcp.py src/drskill/resolution.py src/drskill/pipeline.py tests/test_mcp.py
git commit -m "feat: MCP config discovery wired into every scan"
```

---

### Task 3: The seven checks

**Files:**
- Create: `src/drskill/checks/mcp.py`
- Modify: `src/drskill/checks/__init__.py:66` (add `mcp` to the run_all import line)
- Test: `tests/test_checks_mcp.py`

**Interfaces:**
- Consumes: `world.mcp_servers`, `world.mcp_config_errors`, `make_finding`/`fingerprint` — but findings are built directly with `Finding(...)` here because there are no Contributor objects; `contributors=[server.source]`, `contributor_names=[server.name]`, `harnesses=[server.harness]`.
- Produces: check ids `mcp-config-invalid` (error), `mcp-shadowed-server` (warning), `mcp-diverged-server` (warning), `mcp-secret-in-config` (error project / warning user), `mcp-unpinned-server` (warning), `mcp-insecure-url` (warning), `mcp-dead-server` (error).
- Fingerprint bases: the server's `config_hash` (identity-qualified by check id + server name). Exception per spec: `mcp-secret-in-config` hashes server name + source path + sorted suspect variable names. `mcp-config-invalid` hashes the file path only (content may be unparseable).

- [ ] **Step 1: Write the failing tests** — create `tests/test_checks_mcp.py`:

```python
import json
import shutil
from pathlib import Path

from drskill.checks import REGISTRY, run_all
from drskill.ledger import Config
from drskill.pipeline import run_scan


def project_with(tmp_path, mcp: dict, home_claude_json: dict | None = None):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": mcp}))
    if home_claude_json is not None:
        home.mkdir(exist_ok=True)
        (home / ".claude.json").write_text(json.dumps(home_claude_json))
    return proj, home


def by_check(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def scan(proj, home, monkeypatch, tmp_path):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    return run_scan(proj, home, config=Config())


def test_secret_in_project_config_is_error(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {
        "gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "ghp_16charslong16charslong16charslong"}},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-secret-in-config")
    assert f.severity == "error"
    assert "GITHUB_TOKEN" in f.message
    assert "ghp_" not in f.message  # never the value
    assert "ghp_" not in f.fingerprint


def test_secret_in_user_config_is_warning(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {}, home_claude_json={
        "mcpServers": {"gh": {"command": "gh-mcp", "env": {"API_KEY": "literal-value"}}},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-secret-in-config")
    assert f.severity == "warning"


def test_unpinned_and_insecure_and_dead(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {
        "floaty": {"command": "npx", "args": ["-y", "@scope/server-pkg"]},
        "plain": {"url": "http://mcp.example.com/sse"},
        "local": {"url": "http://localhost:3000/sse"},
        "ghost": {"command": "definitely-not-a-real-binary-xyz"},
        "alive": {"command": shutil.which("ls") or "/bin/ls"},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert {f.contributor_names[0] for f in by_check(findings, "mcp-unpinned-server")} == {"floaty"}
    assert {f.contributor_names[0] for f in by_check(findings, "mcp-insecure-url")} == {"plain"}
    dead = by_check(findings, "mcp-dead-server")
    assert {f.contributor_names[0] for f in dead} == {"ghost"}
    assert dead[0].severity == "error"


def test_pinned_version_is_clean(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {
        "pinned": {"command": "npx", "args": ["-y", "@scope/server-pkg@1.2.3"]},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert by_check(findings, "mcp-unpinned-server") == []


def test_shadowed_server_same_harness(tmp_path, monkeypatch):
    proj, home = project_with(
        tmp_path,
        {"gh": {"command": "gh-mcp-project"}},
        home_claude_json={"mcpServers": {"gh": {"command": "gh-mcp-user"}}},
    )
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-shadowed-server")
    assert "gh" in f.contributor_names
    assert "project" in f.message  # claude-code is project-first


def test_diverged_across_harnesses(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {"gh": {"command": "gh-mcp", "args": ["--fast"]}})
    (proj / ".cursor").mkdir()
    (proj / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "gh": {"command": "gh-mcp", "args": ["--slow"]},
    }}))
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-diverged-server")
    assert "args" in f.message


def test_identical_across_harnesses_is_clean(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {"gh": {"command": "gh-mcp"}})
    (proj / ".cursor").mkdir()
    (proj / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "gh": {"command": "gh-mcp"},
    }}))
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert by_check(findings, "mcp-diverged-server") == []
    assert by_check(findings, "mcp-shadowed-server") == []


def test_invalid_config_finding(tmp_path, monkeypatch):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text("{broken")
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-config-invalid")
    assert f.severity == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks_mcp.py -v`
Expected: FAIL (no `mcp-*` findings produced)

- [ ] **Step 3: Implement** — create `src/drskill/checks/mcp.py`:

```python
"""Static MCP checks: read the discovered server entries, launch nothing."""

from __future__ import annotations

import hashlib
import shutil
from collections import defaultdict
from pathlib import Path

from drskill.checks import check
from drskill.ledger import Config
from drskill.mcp import MCPServer
from drskill.models import Finding
from drskill.resolution import World

_PIN_RUNNERS = {"npx", "uvx", "bunx", "pnpm"}
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")


def _fp(check_id: str, parts: list[str]) -> str:
    payload = "|".join([check_id, *sorted(parts)])
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _finding(check_id, severity, s: MCPServer, message, fix=None, fp_parts=None):
    return Finding(
        check_id=check_id, severity=severity,
        contributors=[s.source], contributor_names=[s.name],
        harnesses=[s.harness], message=message,
        fix_commands=fix or [],
        fingerprint=_fp(check_id, fp_parts or [s.name, s.config_hash]),
    )


@check("mcp-config-invalid")
def config_invalid(world: World, config: Config) -> list[Finding]:
    out = []
    for hid, msg in world.mcp_config_errors:
        path = msg.split(":", 1)[0]
        out.append(Finding(
            check_id="mcp-config-invalid", severity="error",
            contributors=[path], contributor_names=[],
            harnesses=[hid],
            message=f"MCP config does not parse: {msg}",
            fix_commands=[f"Fix the syntax in {path}"],
            fingerprint=_fp("mcp-config-invalid", [hid, path]),
        ))
    return out


@check("mcp-secret-in-config")
def secret_in_config(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if not s.suspect_env:
            continue
        names = ", ".join(s.suspect_env)
        sev = "error" if s.scope == "project" else "warning"
        where = "a committable project file" if s.scope == "project" else "a user-scope file"
        out.append(_finding(
            "mcp-secret-in-config", sev, s,
            f"server '{s.name}' holds credential-shaped values in {where}: "
            f"{names}\n        {s.source}",
            fix=[f"Move {names} out of {s.source} into your environment or a secret manager"],
            fp_parts=[s.name, s.source, *s.suspect_env],
        ))
    return out


@check("mcp-unpinned-server")
def unpinned_server(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if s.command not in _PIN_RUNNERS:
            continue
        pkgs = [a for a in s.args if not a.startswith("-") and a not in ("dlx", "exec")]
        if not pkgs:
            continue
        pkg = pkgs[0]
        base, _, ver = pkg.rpartition("@")
        pinned = bool(base) and ver != "" and ver != "latest" and not pkg.endswith("@latest")
        if pkg.startswith("@") and pkg.count("@") == 1:
            pinned = False  # a scoped package with no version at all
        if not pinned:
            out.append(_finding(
                "mcp-unpinned-server", "warning", s,
                f"server '{s.name}' runs an unpinned package "
                f"('{s.command} {' '.join(s.args)}'): whatever publishes next runs next"
                f"\n        {s.source}",
                fix=[f"Pin it, e.g. {s.command} {pkg.split('@latest')[0].rstrip('@')}@<version>"],
            ))
    return out


@check("mcp-insecure-url")
def insecure_url(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if s.url and s.url.startswith("http://"):
            host = s.url.removeprefix("http://").split("/", 1)[0].split(":", 1)[0]
            if host in _LOCAL_HOSTS:
                continue
            out.append(_finding(
                "mcp-insecure-url", "warning", s,
                f"server '{s.name}' uses plaintext http: {s.url}\n        {s.source}",
                fix=["Use https for remote MCP servers"],
            ))
    return out


@check("mcp-dead-server")
def dead_server(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if s.transport != "stdio" or not s.command:
            continue
        p = Path(s.command)
        exists = p.exists() if p.is_absolute() else shutil.which(s.command) is not None
        if not exists:
            out.append(_finding(
                "mcp-dead-server", "error", s,
                f"server '{s.name}' command not found: {s.command}\n        {s.source}",
                fix=[f"Install {s.command} or remove the entry from {s.source}"],
            ))
    return out


@check("mcp-shadowed-server")
def shadowed_server(world: World, config: Config) -> list[Finding]:
    out = []
    per = defaultdict(list)
    for s in world.mcp_servers:
        per[(s.harness, s.name)].append(s)
    for (hid, name), entries in sorted(per.items()):
        scopes = {e.scope for e in entries}
        if scopes != {"project", "user"}:
            continue
        if len({e.config_hash for e in entries}) == 1:
            continue  # identical duplicate config: harmless
        h = world.harnesses.get(hid)
        winner = "user" if h and h.search_order == "global-first" else "project"
        srcs = "".join(f"\n        {e.scope}: {e.source}" for e in sorted(entries, key=lambda e: e.scope))
        out.append(_finding(
            "mcp-shadowed-server", "warning", entries[0],
            f"'{name}' is configured in both scopes of {hid} with different "
            f"settings; the {winner} entry wins{srcs}",
            fix=[f"Keep one entry for '{name}' in {hid}"],
            fp_parts=[name, hid, *sorted(e.config_hash for e in entries)],
        ))
    return out


@check("mcp-diverged-server")
def diverged_server(world: World, config: Config) -> list[Finding]:
    out = []
    per = defaultdict(list)
    for s in world.mcp_servers:
        per[s.name].append(s)
    for name, entries in sorted(per.items()):
        variants = {}
        for e in entries:
            variants.setdefault(e.config_hash, []).append(e)
        if len(variants) < 2:
            continue
        harnesses = sorted({e.harness for e in entries})
        if len(harnesses) < 2:
            continue  # same-harness drift is the shadow check's job
        fields = _differing_fields([v[0] for v in variants.values()])
        lines = "".join(
            f"\n        {e.harness} ({e.scope}): {e.source}"
            for v in variants.values() for e in v
        )
        out.append(Finding(
            check_id="mcp-diverged-server", severity="warning",
            contributors=sorted({e.source for e in entries}),
            contributor_names=[name], harnesses=harnesses,
            message=(
                f"'{name}' is configured differently across harnesses; "
                f"differing fields: {', '.join(fields)}{lines}"
            ),
            fix_commands=[f"Align '{name}' across harnesses, or rename intentionally different servers"],
            fingerprint=_fp("mcp-diverged-server", [name, *sorted(variants)]),
        ))
    return out


def _differing_fields(variants: list[MCPServer]) -> list[str]:
    fields = []
    for attr in ("command", "args", "url", "env_names"):
        if len({repr(getattr(v, attr)) for v in variants}) > 1:
            fields.append(attr)
    return fields or ["config"]
```

Register in `src/drskill/checks/__init__.py` by adding `mcp` to the run_all import line:

```python
    from drskill.checks import budget, duplicates, filesystem, heuristics, injection, lockfile, mcp, shadowing, spec  # noqa: F401
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/mcp.py src/drskill/checks/__init__.py tests/test_checks_mcp.py
git commit -m "feat: seven static MCP checks"
```

---

### Task 4: Report and CLI surface

**Files:**
- Modify: `src/drskill/report.py` (header count ~line 182; `_facet_unverified` ~line 96)
- Modify: `src/drskill/cli.py` (`list_cmd` ~line 406)
- Modify: `src/drskill/ledger.py` (`ack_destination` scope fallback ~line 96)
- Test: `tests/test_checks_mcp.py`

**Interfaces:**
- Produces: header reads "…, N skills, M MCP servers" when M > 0; `mcp-*` findings get `?` markers from `HarnessDef.mcp_verified`; `drskill list --mcp` prints a per-harness server table; an ack of a finding whose contributors are all user-scope MCP sources routes to the machine ledger.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_checks_mcp.py`:

```python
from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"DRSKILL_HOME": str(home), "COLUMNS": "200"}


def test_header_counts_servers_and_facet_marker(tmp_path):
    proj, home = project_with(tmp_path, {"gh": {"command": "gh-mcp-not-installed-xyz"}})
    r = runner.invoke(app, ["scan", "--root", str(proj)], env=env_for(tmp_path))
    assert "1 MCP server" in r.output
    # claude-code's MCP formats are doc-verified, so no ? on the finding
    assert "mcp-dead-server" in r.output


def test_list_mcp_table(tmp_path):
    proj, home = project_with(tmp_path, {"gh": {"command": "gh-mcp"}})
    r = runner.invoke(app, ["list", "--mcp", "--root", str(proj)], env=env_for(tmp_path))
    assert "gh" in r.output and "stdio" in r.output and "project" in r.output


def test_user_scope_mcp_ack_routes_to_machine_ledger(tmp_path):
    proj, home = project_with(tmp_path, {}, home_claude_json={
        "mcpServers": {"gh": {"command": "gh-mcp", "env": {"API_KEY": "literal-value"}}},
    })
    r = runner.invoke(
        app, ["ack", "mcp-secret-in-config", "--root", str(proj)], env=env_for(tmp_path)
    )
    assert r.exit_code == 0, r.output
    assert (home := Path(env_for(tmp_path)["DRSKILL_HOME"])) is not None
    assert (home / ".drskill.toml").is_file()
    assert not (proj / "drskill.toml").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks_mcp.py -k "header or list_mcp or routes" -v`
Expected: FAIL ("MCP server" absent; `--mcp` unknown option; ack lands in project ledger)

- [ ] **Step 3: Implement**

`src/drskill/report.py` — in `render`, extend the header after the skills count:

```python
    if world.mcp_servers:
        n_mcp = len(world.mcp_servers)
        header += f", {n_mcp} MCP server{'s' if n_mcp != 1 else ''}"
```

and in `_facet_unverified`, before the existing return logic, add an MCP branch (the function receives the finding; check its `check_id`):

```python
    if f.check_id.startswith("mcp-"):
        return not hdef.mcp_verified
```

`src/drskill/cli.py` — `list_cmd` gains `mcp: bool = typer.Option(False, "--mcp", help="list MCP servers instead of skills")`; when set, run the scan pipeline and print one table: harness, server name, transport, scope, source path (rich table, escaped cells), then return before the skill tables.

`src/drskill/ledger.py` — in `ack_destination`, replace the scopes comprehension with a lookup that also consults MCP servers:

```python
    scopes = set()
    by_source = {s.source: s.scope for s in getattr(world, "mcp_servers", [])}
    for cid in finding.contributors:
        if cid in world.contributors:
            scopes.add(world.contributors[cid].scope)
        elif cid in by_source:
            scopes.add(by_source[cid])
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/report.py src/drskill/cli.py src/drskill/ledger.py tests/test_checks_mcp.py
git commit -m "feat: MCP servers in the report header, list --mcp, scope-routed acks"
```

---

### Task 5: Format verification and docs

**Files:**
- Modify: `src/drskill/data/harnesses.toml` (`mcp_verified` flags)
- Modify: `README.md` (Checks table + a new MCP section)

- [ ] **Step 1: Verify each claimed format against official documentation** (WebFetch the harness docs: Claude Code MCP docs, Cursor MCP docs, VS Code MCP docs, Codex config docs, Gemini CLI docs, Claude Desktop quickstart). Set `mcp_verified = true` only for formats the docs confirm, recording nothing where they don't. Cline stays `false`.

- [ ] **Step 2: README** in the plain style: add the seven `mcp-*` rows to the Checks table; add an "MCP servers" section after Deep checks covering what is read (config files only, nothing launched), the checks, `list --mcp`, the `?` marker meaning for MCP formats, and the coming handshake cycle in one sentence.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/drskill/data/harnesses.toml README.md
git commit -m "docs: MCP static checks documented and formats verified"
```

---

### Post-plan gates (run by the driver, not a subagent)

1. **Real machine gate:** `uv run drskill scan` on the author's machine (has `~/.claude.json` and Claude Desktop config with real servers). Review every mcp finding by hand; false positives get fixed, not shipped. Also `uv run drskill list --mcp`.
2. **Code review pass**, then finishing-a-development-branch.
