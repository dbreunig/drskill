# MCP Handshake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill scan --mcp-connect` connects to each configured MCP server, enumerates its tools, and writes committed snapshots; every scan then builds tool contributors from snapshots, runs three new checks (connect-failed, tool-collision, tools-unreviewed/rug-pull), routes tool descriptions through description-overlap and the deep judge, and prints the context bill.

**Architecture:** `mcp_connect.py` holds the snapshot model plus the SDK-backed handshake behind a lazy `mcp` import. `Contributor.kind` gains `mcp_tool`; skill checks filter to `kind == "skill"` while description-overlap runs over both kinds. The pipeline builds tool contributors from loaded snapshots and merges them into `world.contributors`, so every existing consumer sees them. `checks/mcp_tools.py` adds the three checks. Report gains the token bill.

**Tech Stack:** Existing, plus the official `mcp` SDK as a `connect` extra on drskill-core (lazy import). No SDK import without `--mcp-connect`.

**Spec:** `docs/superpowers/specs/2026-07-21-mcp-handshake-design.md`

## Global Constraints

- Enumeration only: `initialize`, `initialized`, `tools/list`. Never a tool call, never resources or prompts.
- Env values and http headers are read at connect time, passed to the child, never written to a snapshot, finding, fingerprint, or output.
- Snapshots are value-free JSON, keyed by the server's cycle-1 `config_hash`, committed.
- The `mcp` SDK imports only under `--mcp-connect`; a missing extra prints a one-line install hint and exits 1.
- Per-server 15s timeout; a stdio overrun is killed; a failed server yields `mcp-connect-failed` and the run continues.
- Skill checks ignore `mcp_tool` contributors; description-overlap and the deep judge include them.
- Every test sets `DRSKILL_HOME`; no test launches a third-party server. Stage only named files.

---

### Task 1: Contributor.kind gains mcp_tool, and skill checks filter to it

**Files:**
- Modify: `src/drskill/models.py:50` (Contributor.kind)
- Modify: `src/drskill/checks/heuristics.py:17` (`_skill_md`), and add `_routing_contributors`
- Modify: `src/drskill/checks/heuristics.py:147` (description-overlap source)
- Modify: `src/drskill/checks/spec.py:17` (`_skill_md_contributors`)
- Modify: `src/drskill/checks/budget.py:13,38` (kind guard)
- Modify: `src/drskill/checks/duplicates.py:55,84` (kind guard)
- Test: `tests/test_mcp_tools.py` (create)

**Interfaces:**
- Produces: `Contributor.kind: Literal["skill", "mcp_tool"] = "skill"`. Skill checks skip `mcp_tool`; `heuristics._routing_contributors(world)` returns skills + tools for description-overlap.

- [ ] **Step 1: Write the failing audit test** — create `tests/test_mcp_tools.py`:

```python
from drskill.checks import run_all
from drskill.ledger import Config
from drskill.models import Contributor, Deployment, TokenCost
from drskill.resolution import World
from drskill.harnesses import HarnessDef


def skill(name, desc, cid, harness="claude-code", body="body text here"):
    return Contributor(
        id=cid, name=name, scope="project", routing_text=desc, body=body,
        token_cost=TokenCost(catalog_tokens=10, body_tokens=5), content_hash=cid,
        deployments=[Deployment(harness=harness, path=cid, scope="project",
                                via_symlink=False, order=0)],
    )


def tool(name, desc, cid, harness="claude-code"):
    return Contributor(
        id=cid, name=name, kind="mcp_tool", scope="user", routing_text=desc,
        token_cost=TokenCost(catalog_tokens=8, body_tokens=0), content_hash=cid,
        deployments=[Deployment(harness=harness, path=cid, scope="user",
                                via_symlink=False, order=0)],
    )


def world_of(*contribs):
    return World(
        contributors={c.id: c for c in contribs},
        harnesses={"claude-code": HarnessDef(
            id="claude-code", display_name="Claude Code",
            paths_verified=True, precedence_verified=True)},
    )


SKILL_CHECKS = {
    "spec-name-mismatch", "spec-missing-description", "spec-description-too-long",
    "spec-invalid-frontmatter", "missing-activation", "generic-description",
    "opposing-imperatives", "budget-body-tokens", "exact-duplicate",
    "near-duplicate", "frontmatter-angle-brackets",
}


def test_skill_checks_ignore_tools():
    # a tool whose text would trip skill checks if it were treated as a skill
    t = tool("vague", "Helps.", "hash1:vague")  # generic + missing-activation bait
    findings = run_all(world_of(t), Config())
    assert not any(f.check_id in SKILL_CHECKS for f in findings)


def test_two_identical_tools_are_not_exact_duplicate():
    a = tool("search", "Search the web.", "h1:search")
    b = tool("search", "Search the web.", "h2:search")
    findings = run_all(world_of(a, b), Config())
    assert not any(f.check_id == "exact-duplicate" for f in findings)


def test_description_overlap_sees_tool_vs_skill():
    s = skill("web-search", "Use when the user wants to search the web for pages.",
              "/skills/web-search/SKILL.md")
    t = tool("search", "Use when the user wants to search the web for pages.",
             "h1:search")
    findings = run_all(world_of(s, t), Config())
    overlaps = [f for f in findings if f.check_id == "description-overlap"]
    assert overlaps and {"web-search", "search"} <= set(overlaps[0].contributor_names)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_tools.py -k "ignore_tools or identical_tools or tool_vs_skill" -v`
Expected: FAIL — `kind="mcp_tool"` rejected by the Literal, or (after the model change) description-overlap does not include the tool.

- [ ] **Step 3: Implement**

`src/drskill/models.py`:

```python
    kind: Literal["skill", "mcp_tool"] = "skill"
```

`src/drskill/checks/heuristics.py` — restrict `_skill_md` to skills, add the routing helper:

```python
def _skill_md(world: World) -> list[Contributor]:
    return [
        c
        for c in world.contributors.values()
        if c.kind == "skill" and Path(c.id).name == "SKILL.md" and c.frontmatter_valid
    ]


def _routing_contributors(world: World) -> list[Contributor]:
    """Contributors that inject a routing description: skills and MCP tools.
    description-overlap runs over both so a tool can collide with a skill."""
    return [
        c
        for c in world.contributors.values()
        if (c.kind == "skill" and Path(c.id).name == "SKILL.md" and c.frontmatter_valid)
        or c.kind == "mcp_tool"
    ]
```

In `description_overlap`, change the first line from `_skill_md(world)` to:

```python
    cs = [c for c in _routing_contributors(world) if c.routing_text.strip()]
```

`src/drskill/checks/spec.py`:

```python
def _skill_md_contributors(world: World):
    return [
        c for c in world.contributors.values()
        if c.kind == "skill" and Path(c.id).name == "SKILL.md"
    ]
```

`src/drskill/checks/budget.py` — exclude tools from the skill catalog and the body ceiling (the context bill counts tool tokens separately in Task 6):

```python
        total = sum(
            c.token_cost.catalog_tokens for c in contributors if c.kind == "skill"
        )
```
```python
        for c in world.contributors.values()
        if c.kind == "skill" and c.token_cost.body_tokens > config.budget.body_tokens_warn
```

`src/drskill/checks/duplicates.py` — both `exact_duplicate` and `near_duplicate` iterate all contributors; guard both:

```python
    for c in world.contributors.values():
        if c.kind != "skill":
            continue
        by_hash.setdefault(c.content_hash, []).append(c)
```
```python
    cs = [c for c in world.contributors.values() if c.kind == "skill"]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (existing skill fixtures are all `kind="skill"` by default)

- [ ] **Step 5: Commit**

```bash
git add src/drskill/models.py src/drskill/checks/heuristics.py src/drskill/checks/spec.py src/drskill/checks/budget.py src/drskill/checks/duplicates.py tests/test_mcp_tools.py
git commit -m "feat: mcp_tool contributor kind; skill checks skip tools, overlap includes them"
```

---

### Task 2: Snapshot model, read/write, and diff

**Files:**
- Create: `src/drskill/mcp_connect.py`
- Test: `tests/test_mcp_connect.py` (create)

**Interfaces:**
- Produces:
  - `class ToolInfo(BaseModel)`: `name: str`, `description: str`, `schema_tokens: int`
  - `class ServerSnapshot(BaseModel)`: `server: str`, `config_hash: str`, `date: str`, `tools: list[ToolInfo]`
  - `snapshot_dir(project_root, home, global_mode) -> Path` (`.drskill/cache/mcp-tools/`)
  - `load_snapshots(sdir: Path) -> dict[str, ServerSnapshot]` keyed by config_hash; corrupt files skipped
  - `save_snapshot(sdir: Path, snap: ServerSnapshot) -> None`
  - `changed_tools(old: ServerSnapshot | None, new: ServerSnapshot) -> list[str]` (tool names whose description differs, or that are added/removed)
  - `tool_fingerprint_base(snap: ServerSnapshot) -> list[str]` (sorted `name\ndescription` strings)

- [ ] **Step 1: Write the failing tests** — create `tests/test_mcp_connect.py`:

```python
from drskill import mcp_connect as mc


def snap(config_hash="abc", date="2026-07-21", tools=(("t", "desc", 3),)):
    return mc.ServerSnapshot(
        server="srv", config_hash=config_hash, date=date,
        tools=[mc.ToolInfo(name=n, description=d, schema_tokens=s) for n, d, s in tools],
    )


def test_snapshot_round_trip(tmp_path):
    sdir = tmp_path / "mcp-tools"
    s = snap()
    mc.save_snapshot(sdir, s)
    loaded = mc.load_snapshots(sdir)
    assert loaded == {"abc": s}


def test_load_snapshots_skips_corrupt(tmp_path):
    sdir = tmp_path / "mcp-tools"
    sdir.mkdir()
    (sdir / "bad.json").write_text("{nope")
    assert mc.load_snapshots(sdir) == {}


def test_changed_tools_detects_description_edit_add_remove():
    old = snap(tools=(("a", "old", 1), ("b", "keep", 1)))
    new = snap(tools=(("a", "new", 1), ("b", "keep", 1), ("c", "added", 1)))
    assert set(mc.changed_tools(old, new)) == {"a", "c"}
    assert mc.changed_tools(None, new) == []  # first snapshot: nothing to compare


def test_fingerprint_base_is_order_independent():
    a = snap(tools=(("x", "1", 1), ("y", "2", 1)))
    b = snap(tools=(("y", "2", 9), ("x", "1", 9)))  # token count differs, text same
    assert mc.tool_fingerprint_base(a) == mc.tool_fingerprint_base(b)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_connect.py -v`
Expected: collection error, `No module named 'drskill.mcp_connect'`

- [ ] **Step 3: Implement** — create `src/drskill/mcp_connect.py` (snapshot half only; the SDK half is Task 3):

```python
"""The MCP handshake: connect to configured servers, enumerate their tools,
and snapshot the results. Everything that speaks the protocol lives behind a
lazy `mcp` SDK import in `connect_server`. Snapshots are value-free JSON;
no env value or secret is ever written."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    name: str
    description: str
    schema_tokens: int


class ServerSnapshot(BaseModel):
    server: str
    config_hash: str
    date: str  # ISO date of the handshake
    tools: list[ToolInfo] = Field(default_factory=list)


def snapshot_dir(project_root: Path, home: Path, global_mode: bool) -> Path:
    base = home if global_mode else project_root
    return base / ".drskill" / "cache" / "mcp-tools"


def load_snapshots(sdir: Path) -> dict[str, ServerSnapshot]:
    out: dict[str, ServerSnapshot] = {}
    if not sdir.is_dir():
        return out
    for p in sorted(sdir.glob("*.json")):
        try:
            snap = ServerSnapshot(**json.loads(p.read_text()))
        except Exception:
            continue
        out[snap.config_hash] = snap
    return out


def save_snapshot(sdir: Path, snap: ServerSnapshot) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{snap.config_hash}.json").write_text(snap.model_dump_json(indent=2) + "\n")


def changed_tools(old: ServerSnapshot | None, new: ServerSnapshot) -> list[str]:
    if old is None:
        return []
    old_desc = {t.name: t.description for t in old.tools}
    new_desc = {t.name: t.description for t in new.tools}
    changed = [
        n for n in set(old_desc) | set(new_desc)
        if old_desc.get(n) != new_desc.get(n)
    ]
    return sorted(changed)


def tool_fingerprint_base(snap: ServerSnapshot) -> list[str]:
    return sorted(f"{t.name}\n{t.description}" for t in snap.tools)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_mcp_connect.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/mcp_connect.py tests/test_mcp_connect.py
git commit -m "feat: MCP tool snapshot model, cache IO, and diff"
```

---

### Task 3: The SDK-backed handshake

**Files:**
- Modify: `src/drskill/mcp_connect.py` (add `connect_server`, `ConnectUnavailableError`, `run_handshakes`)
- Modify: `pyproject.toml` (`connect` extra), `packaging/drskill/pyproject.toml` (metapackage deps)
- Test: `tests/test_mcp_connect.py` (uses an in-repo fake stdio server)
- Create: `tests/fixtures/fake_mcp_server.py`

**Interfaces:**
- Produces:
  - `class ConnectUnavailableError(Exception)`
  - `connect_server(server: MCPServer, timeout: float = 15.0) -> ServerSnapshot` — raises `ConnectUnavailableError` if the SDK is missing; on a connect/timeout/protocol failure raises `ConnectError(message)`; env values are read from `server` but never stored.
  - `class ConnectError(Exception)` with a `.message`
  - `run_handshakes(servers: list[MCPServer], sdir: Path, timeout=15.0) -> tuple[int, list[tuple[str, str, str]]]` returning `(connected_count, failures)` where a failure is `(server_name, harness, message)`; writes a snapshot per success.
- Consumes: `drskill.mcp.MCPServer` (Task-1 cycle: has `command`, `args`, `url`, `transport`, `env_names`, `config_hash`, `name`, `harness`, `source`).

Note: `MCPServer` from cycle 1 stores only env *names*, not values. `connect_server` re-reads the raw env values from `server.source` at call time via `mcp._raw_env(server)` (added below), so values never persist on the model.

- [ ] **Step 1: Create the fake server fixture** — `tests/fixtures/fake_mcp_server.py`:

```python
"""A minimal stdio MCP server for tests: answers initialize and tools/list
over newline-delimited JSON-RPC, then exits. With arg 'hang' it sleeps
forever after initialize, to exercise the timeout."""
import json
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    hang = len(sys.argv) > 1 and sys.argv[1] == "hang"
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "0"},
            }})
        elif method == "notifications/initialized":
            if hang:
                time.sleep(60)
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "echo", "description": "Echo text back.",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
                {"name": "ping", "description": "Ping the server.",
                 "inputSchema": {"type": "object"}},
            ]}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the failing tests** — append to `tests/test_mcp_connect.py`:

```python
import sys
from pathlib import Path

from drskill.mcp import MCPServer

FAKE = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


def stdio_server(name="fake", args=None, config_hash="cfg1"):
    return MCPServer(
        name=name, harness="claude-code", scope="user", source="/x/config.json",
        transport="stdio", command=sys.executable, args=[FAKE, *(args or [])],
        config_hash=config_hash,
    )


def test_connect_server_enumerates_tools():
    snap = mc.connect_server(stdio_server())
    names = {t.name for t in snap.tools}
    assert names == {"echo", "ping"}
    assert all(t.schema_tokens >= 0 for t in snap.tools)
    assert snap.config_hash == "cfg1"


def test_connect_server_times_out_and_is_killed():
    import pytest
    with pytest.raises(mc.ConnectError):
        mc.connect_server(stdio_server(args=["hang"]), timeout=1.0)


def test_run_handshakes_writes_snapshots_and_collects_failures(tmp_path):
    good = stdio_server(config_hash="good")
    bad = MCPServer(name="broken", harness="claude-code", scope="user",
                    source="/x", transport="stdio",
                    command="definitely-not-real-xyz", args=[], config_hash="bad")
    connected, failures = mc.run_handshakes([good, bad], tmp_path / "snaps", timeout=5.0)
    assert connected == 1
    assert [f[0] for f in failures] == ["broken"]
    saved = mc.load_snapshots(tmp_path / "snaps")
    assert "good" in saved and {t.name for t in saved["good"].tools} == {"echo", "ping"}
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_mcp_connect.py -k "enumerates or times_out or run_handshakes" -v`
Expected: FAIL — `connect_server` not defined.

- [ ] **Step 4: Implement**

In `src/drskill/mcp.py`, add a helper that re-reads raw env values without storing them (used only by the connector):

```python
def raw_server_env(server: "MCPServer") -> dict[str, str]:
    """Re-read a server's literal env map from its config file at connect
    time. Values are never stored on the model; the connector passes them
    straight to the child process."""
    try:
        if server.source.endswith(".toml"):
            data = tomllib.loads(Path(server.source).read_text())
            table = (data.get("mcp_servers") or {}).get(server.name) or {}
        else:
            data = json.loads(Path(server.source).read_text())
            table = (
                (data.get("mcpServers") or data.get("servers") or {}).get(server.name)
                or {}
            )
        env = table.get("env") or {}
        return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
    except Exception:
        return {}
```

In `src/drskill/mcp_connect.py`, add the SDK-backed half:

```python
import asyncio
import datetime as dt

from drskill.mcp import MCPServer, raw_server_env


class ConnectUnavailableError(Exception):
    """The mcp SDK is not installed; message shown to the user as-is."""


class ConnectError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _approx_tokens(obj) -> int:
    # schema token cost, approximate: 1 token per ~4 chars of compact JSON
    return max(0, len(json.dumps(obj, separators=(",", ":"))) // 4)


async def _enumerate(server: MCPServer):
    from mcp import ClientSession
    if server.transport == "http":
        from mcp.client.streamable_http import streamablehttp_client
        ctx = streamablehttp_client(server.url)
        async with ctx as (read, write, *_):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return (await s.list_tools()).tools
    else:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=server.command or "", args=server.args,
            env=raw_server_env(server) or None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return (await s.list_tools()).tools


def connect_server(server: MCPServer, timeout: float = 15.0) -> ServerSnapshot:
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        raise ConnectUnavailableError(
            "--mcp-connect needs the connect extra: "
            "uv tool install drskill (or pip install 'drskill-core[connect]')"
        ) from e

    async def _run():
        return await asyncio.wait_for(_enumerate(server), timeout)

    try:
        tools = asyncio.run(_run())
    except ConnectUnavailableError:
        raise
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise ConnectError(f"timed out after {timeout:.0f}s") from e
    except Exception as e:
        raise ConnectError(f"{type(e).__name__}: {e}") from e
    return ServerSnapshot(
        server=server.name, config_hash=server.config_hash,
        date=dt.date.today().isoformat(),
        tools=[
            ToolInfo(
                name=t.name, description=t.description or "",
                schema_tokens=_approx_tokens(getattr(t, "inputSchema", {}) or {}),
            )
            for t in tools
        ],
    )


def run_handshakes(
    servers: list[MCPServer], sdir: Path, timeout: float = 15.0
) -> tuple[int, list[tuple[str, str, str]]]:
    connected = 0
    failures: list[tuple[str, str, str]] = []
    for server in servers:
        try:
            snap = connect_server(server, timeout)
        except ConnectError as e:
            failures.append((server.name, server.harness, e.message))
            continue
        save_snapshot(sdir, snap)
        connected += 1
    return connected, failures
```

`pyproject.toml`, extend the extras:

```toml
[project.optional-dependencies]
deep = ["dspy>=3"]
connect = ["mcp>=1.2"]
```

`packaging/drskill/pyproject.toml`, the metapackage now pulls both:

```toml
dependencies = ["drskill-core[deep,connect]==<current version>"]
```

(Match the current version already in that file.)

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_mcp_connect.py -v`
Expected: all PASS (the fake server exercises the real SDK client path)

- [ ] **Step 6: Commit**

```bash
git add src/drskill/mcp_connect.py src/drskill/mcp.py pyproject.toml packaging/drskill/pyproject.toml tests/test_mcp_connect.py tests/fixtures/fake_mcp_server.py
git commit -m "feat: SDK-backed MCP handshake with timeout, behind the connect extra"
```

---

### Task 4: Tool contributors from snapshots in the pipeline

**Files:**
- Modify: `src/drskill/pipeline.py` (build tool contributors from snapshots; run handshakes under a flag)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces:
  - `run_scan(..., mcp_connect: bool = False)` — when true, connects to `world.mcp_servers` and refreshes snapshots before building tools; failures are stashed on `world.mcp_connect_failures: list[tuple[str,str,str]]`.
  - Tool contributors built from every snapshot whose `config_hash` matches a configured server, one contributor per tool, merged into `world.contributors`, with a `Deployment` per harness that configures the server.
  - `world.mcp_connect_failures: list = []` and `world.mcp_snapshot_dates: dict[str, str]` (config_hash -> date) for the "as of" label.
- Consumes: Task 2/3 snapshot IO and handshake; `MCPServer.config_hash`, `.harness`, `.scope`, `.name`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_mcp_tools.py`:

```python
import json
from drskill.mcp_connect import ServerSnapshot, ToolInfo, save_snapshot, snapshot_dir
from drskill.pipeline import run_scan


def _mcp_project(tmp_path, servers: dict):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    return proj, home


def test_snapshot_builds_tool_contributors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    # discover the server to learn its config_hash
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = next(s.config_hash for s in servers if s.name == "srv")
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=cfg, date="2026-07-21",
        tools=[ToolInfo(name="echo", description="Echo it.", schema_tokens=4)],
    ))
    world, _ = run_scan(proj, home, config=Config())
    tools = [c for c in world.contributors.values() if c.kind == "mcp_tool"]
    assert [c.name for c in tools] == ["echo"]
    assert tools[0].routing_text == "Echo it."
    assert tools[0].deployments[0].harness == "claude-code"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_tools.py -k snapshot_builds -v`
Expected: FAIL — no tool contributors built.

- [ ] **Step 3: Implement** — in `src/drskill/pipeline.py`, add imports and a builder, and wire into `run_scan`.

Add to `World` (in `resolution.py`):

```python
    mcp_connect_failures: list[tuple[str, str, str]] = Field(default_factory=list)
    mcp_snapshot_dates: dict[str, str] = Field(default_factory=dict)
```

In `pipeline.py`, after MCP discovery and before `run_all`:

```python
    from drskill import mcp_connect as mcpc

    sdir = mcpc.snapshot_dir(project_root, home, global_only)
    if mcp_connect:
        _, world.mcp_connect_failures = mcpc.run_handshakes(world.mcp_servers, sdir)
    snapshots = mcpc.load_snapshots(sdir)
    _add_tool_contributors(world, snapshots)
    findings = run_all(world, config)
```

and add the builder plus the flag in the signature (`mcp_connect: bool = False`):

```python
def _add_tool_contributors(world, snapshots) -> None:
    from drskill.models import Contributor, Deployment, TokenCost

    # servers configured now, grouped by config_hash -> the deployments they imply
    by_hash: dict[str, list] = {}
    for s in world.mcp_servers:
        by_hash.setdefault(s.config_hash, []).append(s)
    for cfg, snap in snapshots.items():
        servers = by_hash.get(cfg)
        if not servers:
            continue  # stale snapshot: no current server
        world.mcp_snapshot_dates[cfg] = snap.date
        deployments = [
            Deployment(harness=s.harness, path=s.source, scope=s.scope,
                       via_symlink=False, order=1_000_000)
            for s in servers
        ]
        for t in snap.tools:
            cid = f"{cfg}:{t.name}"
            world.contributors[cid] = Contributor(
                id=cid, name=t.name, kind="mcp_tool",
                scope=servers[0].scope, routing_text=t.description,
                token_cost=TokenCost(catalog_tokens=t.schema_tokens, body_tokens=0),
                content_hash="sha256:" + __import__("hashlib").sha256(
                    f"{t.name}\n{t.description}".encode()).hexdigest(),
                deployments=deployments,
            )
```

(order 1_000_000 keeps tools after skills in `harness_loads`; tools never shadow skills because ids differ and `shadowed_by` is only set among same-name skill deployments.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/pipeline.py src/drskill/resolution.py tests/test_mcp_tools.py
git commit -m "feat: build MCP tool contributors from snapshots in every scan"
```

---

### Task 5: The three checks

**Files:**
- Create: `src/drskill/checks/mcp_tools.py`
- Modify: `src/drskill/checks/__init__.py:66` (register `mcp_tools`)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `world.contributors` (mcp_tool kind), `world.mcp_connect_failures`, `world.mcp_servers`, snapshots via `world` (config_hash on tool ids), `mcp_connect.tool_fingerprint_base` and `changed_tools`.
- Produces: `mcp-connect-failed` (warning), `mcp-tool-collision` (warning), `mcp-tools-unreviewed` (warning). Fingerprint of `mcp-tools-unreviewed` hashes server identity + sorted tool name/description pairs (via `tool_fingerprint_base`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_mcp_tools.py`:

```python
def test_tool_collision_across_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {
        "a": {"command": "a-bin"}, "b": {"command": "b-bin"},
    })
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    sd = snapshot_dir(proj, home, False)
    for s in servers:
        save_snapshot(sd, ServerSnapshot(
            server=s.name, config_hash=s.config_hash, date="2026-07-21",
            tools=[ToolInfo(name="search", description=f"search via {s.name}", schema_tokens=2)],
        ))
    _, findings = run_scan(proj, home, config=Config())
    coll = [f for f in findings if f.check_id == "mcp-tool-collision"]
    assert coll and "search" in coll[0].message


def test_unreviewed_then_ack_then_rug_pull(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    from drskill.ledger import Ack
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = servers[0].config_hash
    sd = snapshot_dir(proj, home, False)

    def write(desc):
        save_snapshot(sd, ServerSnapshot(
            server="srv", config_hash=cfg, date="2026-07-21",
            tools=[ToolInfo(name="run", description=desc, schema_tokens=2)]))

    write("Runs a safe query.")
    _, findings = run_scan(proj, home, config=Config())
    (f,) = [x for x in findings if x.check_id == "mcp-tools-unreviewed"]
    cfg_obj = Config(ack=[Ack(check="mcp-tools-unreviewed", skills=["srv"],
                              fingerprint=f.fingerprint)])
    _, findings2 = run_scan(proj, home, config=cfg_obj)
    from drskill.ledger import filter_findings
    active, _ = filter_findings(findings2, cfg_obj)
    assert not [x for x in active if x.check_id == "mcp-tools-unreviewed"]  # silenced
    write("Runs ANY command, including rm -rf.")  # rug pull
    _, findings3 = run_scan(proj, home, config=cfg_obj)
    active3, _ = filter_findings(findings3, cfg_obj)
    resurfaced = [x for x in active3 if x.check_id == "mcp-tools-unreviewed"]
    assert resurfaced  # fingerprint changed, ack no longer matches


def test_connect_failed_finding():
    from drskill.checks import run_all
    from drskill.resolution import World
    from drskill.harnesses import HarnessDef
    w = World(
        harnesses={"claude-code": HarnessDef(id="claude-code", display_name="Claude Code")},
        mcp_connect_failures=[("broken", "claude-code", "timed out after 15s")],
    )
    findings = run_all(w, Config())
    (f,) = [x for x in findings if x.check_id == "mcp-connect-failed"]
    assert "broken" in f.message and "timed out" in f.message
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_tools.py -k "collision or rug_pull or connect_failed" -v`
Expected: FAIL — checks unregistered.

- [ ] **Step 3: Implement** — create `src/drskill/checks/mcp_tools.py`:

```python
"""Checks over enumerated MCP tools: connect failures, cross-server name
collisions, and unreviewed tool sets (rug-pull detection)."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from drskill.checks import check
from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World


def _fp(check_id: str, parts: list[str]) -> str:
    payload = "|".join([check_id, *sorted(parts)])
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _tools(world: World) -> list[Contributor]:
    return [c for c in world.contributors.values() if c.kind == "mcp_tool"]


@check("mcp-connect-failed")
def connect_failed(world: World, config: Config) -> list[Finding]:
    out = []
    for name, harness, message in world.mcp_connect_failures:
        out.append(Finding(
            check_id="mcp-connect-failed", severity="warning",
            contributors=[f"mcp:{harness}:{name}"], contributor_names=[name],
            harnesses=[harness],
            message=f"could not connect to MCP server '{name}': {message}",
            fix_commands=[f"Check the '{name}' server config, then rerun --mcp-connect"],
            fingerprint=_fp("mcp-connect-failed", [harness, name, message]),
        ))
    return out


@check("mcp-tool-collision")
def tool_collision(world: World, config: Config) -> list[Finding]:
    out = []
    # tool name -> harness -> set of owning config hashes
    per_name: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for c in _tools(world):
        cfg = c.id.split(":", 1)[0]
        for d in c.deployments:
            per_name[c.name][d.harness].add(cfg)
    for tname, per_harness in sorted(per_name.items()):
        clashing = sorted(h for h, cfgs in per_harness.items() if len(cfgs) > 1)
        if not clashing:
            continue
        out.append(Finding(
            check_id="mcp-tool-collision", severity="warning",
            contributors=sorted(
                c.id for c in _tools(world) if c.name == tname
            ),
            contributor_names=[tname], harnesses=clashing,
            message=(
                f"tool '{tname}' is exposed by more than one server in the same "
                f"set; which one the agent gets is client dependent"
            ),
            fix_commands=[f"Disable '{tname}' on all but one server"],
            fingerprint=_fp("mcp-tool-collision", [tname, *clashing]),
        ))
    return out


@check("mcp-tools-unreviewed")
def tools_unreviewed(world: World, config: Config) -> list[Finding]:
    from drskill import mcp_connect as mcpc

    out = []
    by_cfg: dict[str, list[Contributor]] = defaultdict(list)
    for c in _tools(world):
        by_cfg[c.id.split(":", 1)[0]].append(c)
    servers_by_cfg = {s.config_hash: s for s in world.mcp_servers}
    for cfg, tools in sorted(by_cfg.items()):
        server = servers_by_cfg.get(cfg)
        if server is None:
            continue
        pairs = sorted(f"{c.name}\n{c.routing_text}" for c in tools)
        lines = "".join(f"\n        {c.name}: {c.routing_text}" for c in sorted(tools, key=lambda c: c.name))
        date = world.mcp_snapshot_dates.get(cfg, "unknown")
        out.append(Finding(
            check_id="mcp-tools-unreviewed", severity="warning",
            contributors=[server.source], contributor_names=[server.name],
            harnesses=[server.harness],
            message=(
                f"server '{server.name}' exposes {len(tools)} unreviewed "
                f"tool{'s' if len(tools) != 1 else ''} (as of {date}); ack to "
                f"approve this exact tool set{lines}"
            ),
            fix_commands=[f"drskill ack mcp-tools-unreviewed {server.name}"],
            fingerprint=_fp("mcp-tools-unreviewed", [server.name, cfg, *pairs]),
        ))
    return out
```

Register in `src/drskill/checks/__init__.py`: add `mcp_tools` to the `run_all` import line.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/checks/mcp_tools.py src/drskill/checks/__init__.py tests/test_mcp_tools.py
git commit -m "feat: MCP tool checks — connect-failed, collision, unreviewed rug-pull"
```

---

### Task 6: The context bill and the --mcp-connect flag

**Files:**
- Modify: `src/drskill/cli.py` (scan `--mcp-connect` option; guard; error reporting)
- Modify: `src/drskill/report.py` (context-bill summary line; tool tokens in `render` and `list --tokens`)
- Test: `tests/test_mcp_tools.py`, `tests/test_report.py`

**Interfaces:**
- Produces: `drskill scan --mcp-connect` connects then scans; without the extra, one-line error and exit 1. The scan summary gains a "largest context bill" line when any tool exists. `list --tokens` counts MCP tool tokens.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_mcp_tools.py`:

```python
from typer.testing import CliRunner
from drskill.cli import app

_runner = CliRunner()


def test_context_bill_line(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=servers[0].config_hash, date="2026-07-21",
        tools=[ToolInfo(name="echo", description="Echo.", schema_tokens=800)]))
    r = _runner.invoke(app, ["scan", "--root", str(proj)],
                       env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert "context bill" in r.output and "MCP tool" in r.output


def test_mcp_connect_without_extra_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    import drskill.mcp_connect as mcpc
    def boom(*a, **k):
        raise mcpc.ConnectUnavailableError("--mcp-connect needs the connect extra")
    monkeypatch.setattr(mcpc, "run_handshakes", boom)
    r = _runner.invoke(app, ["scan", "--root", str(proj), "--mcp-connect"],
                       env={"DRSKILL_HOME": str(home)})
    assert r.exit_code == 1 and "connect extra" in r.output
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_tools.py -k "context_bill or without_extra" -v`
Expected: FAIL — no bill line; `--mcp-connect` unknown option.

- [ ] **Step 3: Implement**

`src/drskill/cli.py` — `scan` gains `mcp_connect: bool = typer.Option(False, "--mcp-connect", help="connect to configured MCP servers and enumerate their tools")`. Before `run_scan`, guard the extra:

```python
    if mcp_connect:
        from drskill import mcp_connect as mcpc
        try:
            mcpc.connect_server.__module__  # touch module; real guard is per-server
        except Exception:
            pass
```

Pass `mcp_connect=mcp_connect` to `run_scan`. The `ConnectUnavailableError` is raised inside `run_handshakes`→`connect_server`; catch it in the CLI by wrapping the `run_scan` call for the connect case:

```python
    try:
        world, findings = run_scan(
            root, home, global_mode, config, harness=harness,
            judge=judge, max_calls=budget, rewriter=rewriter, mcp_connect=mcp_connect,
        )
    except mcpc.ConnectUnavailableError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1)
```

(import `mcp_connect as mcpc` at the top of `scan` only when `mcp_connect` is set, mirroring the deep import; if not set, the normal call path stands.)

`src/drskill/report.py` — add a helper and a summary line. In `render`, after the summary is built:

```python
    bill = _context_bill(world)
    if bill:
        hid, skill_tok, tool_tok = bill
        console.print(
            f"largest context bill: {escape(hid)}, about "
            f"{skill_tok + tool_tok} tokens, {skill_tok} skill catalog and "
            f"{tool_tok} MCP tool definitions (approximate)"
        )
```

and the helper:

```python
def _context_bill(world: World):
    best = None
    for hid in world.harnesses:
        skill_tok = tool_tok = 0
        for c in world.effective(hid):
            if c.kind == "mcp_tool":
                tool_tok += c.token_cost.catalog_tokens
            else:
                skill_tok += c.token_cost.catalog_tokens
        if tool_tok == 0:
            continue
        if best is None or skill_tok + tool_tok > best[1] + best[2]:
            best = (hid, skill_tok, tool_tok)
    return best
```

`render_harness_tables` under `tokens`: tool contributors already appear via `harness_loads`; their `catalog_tokens` are the schema tokens, so the existing per-row and total logic counts them with no change. Add nothing unless a separate MCP column is wanted — leave as one catalog column for now.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/cli.py src/drskill/report.py tests/test_mcp_tools.py tests/test_report.py
git commit -m "feat: scan --mcp-connect and the context-bill headline"
```

---

### Task 7: Docs, prune, and verification

**Files:**
- Modify: `src/drskill/cli.py` (`cache prune` also removes stale tool snapshots)
- Modify: `README.md` (Checks table rows; MCP section handshake paragraph; install note)

- [ ] **Step 1: Extend `cache prune`** to delete snapshot files whose config_hash matches no configured server. In the prune branch, after the verdict-cache walk, add a walk over `snapshot_dir(...)`: keep files whose stem is in `{s.config_hash for s in world.mcp_servers}`, unlink the rest, and fold the count into the printed totals. Add a test in `tests/test_deep_cli.py` mirroring the existing corrupt-file prune test.

- [ ] **Step 2: README** in the plain style: add `mcp-connect-failed`, `mcp-tool-collision`, `mcp-tools-unreviewed` to the Checks table; extend the MCP section with a handshake paragraph (what `--mcp-connect` does, that it only enumerates and never calls a tool, the committed snapshots, the rug-pull ack, and the context bill); note the `connect` extra is in the full install.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/drskill/cli.py README.md tests/test_deep_cli.py
git commit -m "docs: MCP handshake documented; prune clears stale tool snapshots"
```

---

### Post-plan gates (run by the driver, not a subagent)

1. **Real machine gate:** `uv run drskill scan --mcp-connect` against the author's six servers (pencil, memory, playwright, computer-use, node_repl, openaiDeveloperDocs). Review tool findings, any collisions, and the context bill by hand. Confirm no secret env value appears in any snapshot under `.drskill/cache/mcp-tools/` or in output. Then a plain `drskill scan` reads the snapshots and shows the same tool findings labeled "as of".
2. **Timeout check:** confirm a deliberately hanging server is killed within the timeout during the real run or via the fixture.
3. **Code review pass**, then finishing-a-development-branch.
