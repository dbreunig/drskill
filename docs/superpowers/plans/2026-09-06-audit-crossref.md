# Audit Cross-Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill audit` reports scanned contributors that never appear in the covered trace history, with coverage and freshness guards, behind a configurable `unused_days` threshold.

**Architecture:** A pure `src/drskill/traces/crossref.py` joins the World against invocations. `ledger.py` gains a `[usage]` block. The `audit` CLI command additionally runs `run_scan` and `pins.resolve_pins`, computes the unused list, and renders it after the rollup (plus a `--json` key).

**Tech Stack:** Python 3.11+, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-audit-crossref-design.md` (committed with this plan).

## Global Constraints

- Work only in the worktree; every git/test command `cd`s into it. Never run git in the main checkout.
- Full suite before each commit: `uv run --extra connect --extra deep pytest`.
- Audit stays reporting-only: no findings, acks, ledger writes, or `--ci` effects. `scan` never reads traces.
- Existing audit output above the new section is unchanged; all existing tests pass unmodified.
- Transcribe docstrings and comments verbatim. Commit per task, conventional prefix, ending with:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

## Code facts

- `Invocation` (src/drskill/traces/model.py:11-24): `harness`, `timestamp: dt.datetime`, `kind: Literal["skill","mcp_tool"]`, `name`, `server: str | None`, `sidechain`, `detection`.
- `run_audit(home, root, global_mode, harness, since, last=False) -> AuditData` with `AuditData.invocations: list[Invocation]` (traces/pipeline.py:34-47).
- `Contributor`: `id` (realpath, or `f"{config_hash}:{tool_name}"` for mcp tools), `kind` in ("skill","mcp_tool","command"), `name`, `suite`, `system`, `deployments` (each with `.harness`).
- Server-name resolution precedent (src/drskill/suites.py:44-47): `{s.config_hash: s.name for s in world.mcp_servers}`, contributor's hash = `c.id.split(":", 1)[0]`.
- `pins.resolve_pins(project_root, home) -> dict[contributor_id, Pin]`, `Pin.installed_at` ISO date string ("" possible).
- `ledger.Config` holds non-merging blocks (`budget`, `thresholds`, `deep`, `queries`); `load_effective_config` merges only acks.
- The `audit` CLI command (cli.py ~727-817): parses `--since`/`--harness`/`--json`/`--file`/`--last`, calls `tpipeline.run_audit(...)`, then `treport.render(...)` or drilldown or json. Read it before editing; `root = Path.cwd()`, `home = _home()`, `global_mode` flag exist there (verify names).
- Audit CLI tests: tests/test_cli_audit.py with `_claude_trace(home, cwd, skill=...)` writing a synthetic Claude Code session; DRSKILL_HOME monkeypatched; runner pattern as everywhere.

---

### Task 1: The usage config block

**Files:**
- Modify: `src/drskill/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `class Usage(BaseModel): unused_days: int = 90` and `Config.usage: Usage = Usage()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py` (mirror the routing_margin tests added earlier in the file):

```python
def test_usage_block_default_and_override(tmp_path):
    p = tmp_path / "drskill.toml"
    assert load_config(p).usage.unused_days == 90
    p.write_text("[usage]\nunused_days = 30\n")
    assert load_config(p).usage.unused_days == 30
```

- [ ] **Step 2: Verify failure**, **Step 3: Implement** (add after the `Deep` class):

```python
class Usage(BaseModel):
    unused_days: int = 90
```

and `usage: Usage = Usage()` on `Config`.

- [ ] **Step 4: Verify pass**, **Step 5: Full suite, commit**

```bash
git add src/drskill/ledger.py tests/test_ledger.py
git commit -m "feat: add the usage config block"
```

---

### Task 2: The crossref module

**Files:**
- Create: `src/drskill/traces/crossref.py`
- Test: `tests/test_traces_crossref.py`

**Interfaces:**
- Produces:

```python
@dataclass
class Unused:
    kind: str            # skill | command | mcp tool (display form)
    name: str
    where: str           # comma-joined harness ids, or the server name for tools

def unused_contributors(world, invocations: list[Invocation],
                        pins: dict[str, Pin], unused_days: int,
                        today: dt.date) -> list[Unused] | None
```

Returns None when NO harness's coverage spans `unused_days` (the caller renders the "not enough trace coverage" line); else the sorted (kind, name) list of unused contributors. Semantics per the spec:

- Coverage per harness = today minus the harness's earliest invocation date; a harness with no invocations has no coverage.
- Used names: for each invocation, add `inv.name` (skills/commands match on bare name or `suite:name`; commands are recorded as kind "skill" invocations with any detection, so match them the same way); for mcp_tool invocations add `(inv.server, inv.name)`.
- A skill/command contributor is unused when neither its `name` nor `f"{c.suite}:{c.name}"` (when suite is set) appears in the used-name set, AND at least one of its deployment harnesses has coverage ≥ `unused_days`.
- An mcp_tool contributor is unused when `(server_name, c.name)` is absent, where `server_name` resolves via `world.mcp_servers` by the id's hash prefix (unresolvable server → skip the contributor entirely: unknown, not unused). The coverage guard for tools uses any covered harness (tools deploy through configs whose harness fields exist on `c.deployments` — reuse the same any-deployment-harness-covered rule).
- Skip `c.system` contributors.
- Freshness guard: a pin for `c.id` with a parseable `installed_at` younger than `unused_days` skips the contributor.
- `where`: for skills/commands, comma-joined sorted deployment harness ids; for tools, the resolved server name.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_traces_crossref.py`:

```python
import datetime as dt

from drskill import pins as pins_mod
from drskill.harnesses import HarnessDef
from drskill.models import Deployment
from drskill.resolution import World
from drskill.traces import crossref
from drskill.traces.model import Invocation
from tests.test_models import make_contributor

TODAY = dt.date(2026, 9, 6)


def inv(name, days_ago, kind="skill", server=None, harness="claude-code"):
    ts = dt.datetime(2026, 9, 6, 12) - dt.timedelta(days=days_ago)
    return Invocation(harness=harness, session_id="s", timestamp=ts, kind=kind,
                      name=name, server=server, detection="explicit",
                      source_file="/t.jsonl")


def contributor(name, kind="skill", suite=None, harness="claude-code", id=None):
    c = make_contributor(id=id or f"/x/{name}/SKILL.md", name=name, kind=kind)
    c.suite = suite
    c.deployments.append(Deployment(harness=harness, path=c.id, scope="project",
                                    via_symlink=False, order=0))
    return c


def world_of(*cs, servers=()):
    w = World(contributors={c.id: c for c in cs},
              harnesses={"claude-code": HarnessDef(id="claude-code", display_name="CC")})
    w.mcp_servers = list(servers)
    return w


def test_unused_skill_is_reported_with_old_coverage():
    w = world_of(contributor("used"), contributor("dusty"))
    invs = [inv("used", days_ago=100), inv("used", days_ago=1)]
    out = crossref.unused_contributors(w, invs, {}, 90, TODAY)
    assert [(u.kind, u.name) for u in out] == [("skill", "dusty")]
    assert out[0].where == "claude-code"


def test_young_coverage_returns_none():
    w = world_of(contributor("dusty"))
    invs = [inv("whatever", days_ago=10)]
    assert crossref.unused_contributors(w, invs, {}, 90, TODAY) is None


def test_no_invocations_returns_none():
    w = world_of(contributor("dusty"))
    assert crossref.unused_contributors(w, [], {}, 90, TODAY) is None


def test_plugin_qualified_name_counts_as_used():
    w = world_of(contributor("brainstorming", suite="superpowers"))
    invs = [inv("superpowers:brainstorming", days_ago=100)]
    assert crossref.unused_contributors(w, invs, {}, 90, TODAY) == []


def test_fresh_pin_skips_the_contributor():
    c = contributor("newish")
    w = world_of(c, contributor("anchor"))
    invs = [inv("anchor", days_ago=100)]
    pin = pins_mod.Pin(loadout="d/p", selector="skill:newish", source_type="drskill",
                       content_hash="sha256:" + "ab" * 32,
                       installed_at=(TODAY - dt.timedelta(days=5)).isoformat())
    out = crossref.unused_contributors(w, invs, {c.id: pin}, 90, TODAY)
    assert [(u.kind, u.name) for u in out] == []


def test_mcp_tool_matches_by_server_and_name():
    from drskill.mcp import MCPServer

    cfg = "cc" * 32
    server = MCPServer(name="pencil", harness="claude-code", scope="project",
                       source="/x", transport="stdio", command="npx", args=[],
                       env_names=[], config_hash=cfg)
    used = contributor("get_screenshot", kind="mcp_tool", id=f"{cfg}:get_screenshot")
    dusty = contributor("export_html", kind="mcp_tool", id=f"{cfg}:export_html")
    w = world_of(used, dusty, servers=[server])
    invs = [inv("get_screenshot", days_ago=100, kind="mcp_tool", server="pencil")]
    out = crossref.unused_contributors(w, invs, {}, 90, TODAY)
    assert [(u.kind, u.name) for u in out] == [("mcp tool", "export_html")]
    assert out[0].where == "pencil"


def test_unresolvable_server_is_skipped():
    dusty = contributor("tool", kind="mcp_tool", id=("dd" * 32) + ":tool")
    anchor = contributor("anchor")
    w = world_of(dusty, anchor)  # no mcp_servers registered
    invs = [inv("anchor", days_ago=100)]
    assert crossref.unused_contributors(w, invs, {}, 90, TODAY) == []


def test_system_contributors_are_skipped():
    c = contributor("vendored")
    c.system = True
    w = world_of(c, contributor("anchor"))
    invs = [inv("anchor", days_ago=100)]
    assert crossref.unused_contributors(w, invs, {}, 90, TODAY) == []
```

- [ ] **Step 2: Verify failure** — ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `src/drskill/traces/crossref.py`:

```python
"""Join the scanned world against trace history: what is installed but
never invoked. Reporting-only, like the rest of audit — no findings, no
acks, no CI effect. Guards keep fresh installs and young trace windows
from reading as staleness."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from drskill.traces.model import Invocation

_KIND_LABEL = {"skill": "skill", "command": "command", "mcp_tool": "mcp tool"}


@dataclass
class Unused:
    kind: str
    name: str
    where: str


def _covered_harnesses(invocations: list[Invocation], unused_days: int,
                       today: dt.date) -> set[str]:
    earliest: dict[str, dt.date] = {}
    for inv in invocations:
        d = inv.timestamp.date()
        if inv.harness not in earliest or d < earliest[inv.harness]:
            earliest[inv.harness] = d
    return {h for h, d in earliest.items() if (today - d).days >= unused_days}


def unused_contributors(world, invocations: list[Invocation], pins: dict,
                        unused_days: int, today: dt.date) -> list[Unused] | None:
    covered = _covered_harnesses(invocations, unused_days, today)
    if not covered:
        return None

    used_names: set[str] = set()
    used_tools: set[tuple[str, str]] = set()
    for inv in invocations:
        if inv.kind == "mcp_tool":
            used_tools.add((inv.server or "", inv.name))
        else:
            used_names.add(inv.name)

    server_by_cfg = {s.config_hash: s.name for s in world.mcp_servers}
    out: list[Unused] = []
    for c in world.contributors.values():
        if c.system:
            continue
        harnesses = sorted({d.harness for d in c.deployments})
        if not any(h in covered for h in harnesses):
            continue  # unknown, not unused: no covered trace window applies
        pin = pins.get(c.id)
        if pin is not None and pin.installed_at:
            try:
                installed = dt.date.fromisoformat(pin.installed_at)
            except ValueError:
                installed = None
            if installed is not None and (today - installed).days < unused_days:
                continue  # a fresh install has not had time to be used
        if c.kind == "mcp_tool":
            server = server_by_cfg.get(c.id.split(":", 1)[0])
            if server is None:
                continue  # unresolvable server: unknown, not unused
            if (server, c.name) in used_tools:
                continue
            out.append(Unused(_KIND_LABEL[c.kind], c.name, server))
        else:
            names = {c.name}
            if c.suite:
                names.add(f"{c.suite}:{c.name}")
            if names & used_names:
                continue
            out.append(Unused(_KIND_LABEL[c.kind], c.name, ", ".join(harnesses)))
    out.sort(key=lambda u: (u.kind, u.name))
    return out
```

One subtlety the tests pin: `test_young_coverage_returns_none` has 10-day coverage against a 90-day threshold → `covered` empty → None. `test_unused_skill_...` has a 100-day-old invocation → covered. The mcp-tool coverage guard reuses deployment harnesses; MCP tool contributors' deployments carry the harness of their config (built in pipeline `_add_tool_contributors`) — the test's `contributor(...)` helper appends a claude-code deployment for tools too, matching reality.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_traces_crossref.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/traces/crossref.py tests/test_traces_crossref.py
git commit -m "feat: cross-reference the scanned world against trace history"
```

---

### Task 3: Audit renders the Unused section

**Files:**
- Modify: `src/drskill/cli.py` (the `audit` command)
- Test: `tests/test_cli_audit.py`

**Interfaces:**
- Consumes: `crossref.unused_contributors`, `pins.resolve_pins`, `run_scan`, `Config.usage.unused_days`.
- Produces: `drskill audit` gains `--unused-days N` (int option, default None → config value). After the standard report (NOT in drilldown, `--file`, or `--json`-early paths — see below), the command runs `run_scan` quietly, resolves pins, computes the unused list, and renders:

```
Unused (no invocations in the covered history; threshold 90 days):
  skill     dusty    claude-code
```

or, when the function returns None: `Unused: not enough trace coverage to judge (needs 90 days).` When it returns `[]`: `Unused: none — everything installed has been invoked.` The `--json` output gains an `"unused"` key: a list of `{"kind", "name", "where"}` dicts, `null` when coverage was insufficient. Drilldown (`audit <name>`) and `--file` runs skip the cross-reference entirely (single-entity/file views; the scan join belongs to the full report).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_audit.py` (reuse `_claude_trace`; add a helper writing a real skill into the project the CLI scans — the audit command runs from `Path.cwd()`, check how the fixture sets cwd; if it does not chdir, add `monkeypatch.chdir(tmp_path / "proj")` in the new tests and create the skill under that project):

```python
def _write_skill(root, name):
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")


def test_audit_reports_unused_skill(...):
    # trace with skill "used" invoked long ago (old coverage), project with
    # skills "used" and "dusty"
    ...
    result = runner.invoke(app, ["audit", "--unused-days", "30"])
    assert result.exit_code == 0, result.output
    assert "Unused" in result.output
    assert "dusty" in result.output
    assert "threshold 30 days" in result.output


def test_audit_unused_respects_config_default(...):
    # young coverage -> the not-enough-coverage line with the 90-day default
    ...
    assert "not enough trace coverage" in result.output
    assert "90 days" in result.output


def test_audit_json_gains_unused_key(...):
    ...
    data = json.loads(result.output)
    assert any(u["name"] == "dusty" for u in data["unused"])


def test_audit_drilldown_skips_crossref(...):
    result = runner.invoke(app, ["audit", "used"])
    assert "Unused" not in result.output
```

Flesh every `...` into real fixture code: `_claude_trace` needs a timestamp parameter for old coverage — read it; if it hardcodes a recent timestamp, extend it with an optional `ts=` argument (default preserving current behavior so existing tests stay green). The coverage guard needs the trace's invocation to be older than the threshold.

- [ ] **Step 2: Verify failure** — no `--unused-days` option / no Unused output.

- [ ] **Step 3: Implement**

In the `audit` command: add the option

```python
    unused_days: int | None = typer.Option(None, "--unused-days",
        help="flag installed-but-never-invoked entries older than this (default from drskill.toml [usage])"),
```

After the full-report render path (locate where the non-drilldown, non-file, non-json report finishes; also cover the json branch), add the cross-reference. Shape (adapt names to the command's real locals):

```python
    threshold = unused_days if unused_days is not None else config.usage.unused_days
```

(the command must load config — check whether it already does; if not, `config = load_effective_config(root, home, global_mode)` mirroring other commands), then for the full-report and json paths only:

```python
    from drskill import pins as pins_mod
    from drskill.traces import crossref

    world, _ = run_scan(root, home, global_mode)
    resolved = pins_mod.resolve_pins(root, home)
    unused = crossref.unused_contributors(world, data.invocations, resolved,
                                          threshold, dt.date.today())
```

Rendering (text path):

```python
    if unused is None:
        typer.echo(f"\nUnused: not enough trace coverage to judge (needs {threshold} days).")
    elif not unused:
        typer.echo("\nUnused: none — everything installed has been invoked.")
    else:
        typer.echo(f"\nUnused (no invocations in the covered history; threshold {threshold} days):")
        for u in unused:
            typer.echo(f"  {u.kind:<9} {u.name:<20} {u.where}")
```

JSON path: add `"unused": None if unused is None else [{"kind": u.kind, "name": u.name, "where": u.where} for u in unused]` to the emitted document (find how audit builds its json and extend it; keep every existing key).

`run_scan` progress: audit has no status spinner today for scanning — call `run_scan(root, home, global_mode)` plainly (no progress callback) to keep output clean; note that this also runs the scan checks whose findings are discarded (acceptable cost, same as `explain`).

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_cli_audit.py -v` (existing tests unmodified except any `_claude_trace` extension with a default-preserving parameter).
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/cli.py tests/test_cli_audit.py
git commit -m "feat: report installed-but-never-invoked entries in audit"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the docs**

In the README's audit section (grep "## Audit your usage"): add a short paragraph on the Unused section — what it means, the two guards (trace coverage must span the threshold; freshly pinned installs are skipped), the `[usage] unused_days` config and `--unused-days` flag, and that audit remains reporting-only. Keep the voice.

- [ ] **Step 2: Full suite, commit**

```bash
git add README.md
git commit -m "docs: describe the audit unused section"
```
