# Audit `--file` and `--last` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--file <path>` (audit one explicit trace file) and `--last` (narrow the audit to the most recent session) to `drskill audit`, per `docs/superpowers/specs/2026-08-11-audit-file-last-design.md`.

**Architecture:** Each trace adapter exposes its discovery root via a new `trace_root(home)` function, which a new `pipeline.infer_adapter(path, home)` helper uses to map an explicit file to its parser. A new `pipeline.run_audit_file()` extracts one file directly (no cache, no project-scope filter). `run_audit()` gains a `last` keyword that, after the existing filters and sort, keeps only invocations from the newest session's `source_file`. `cli.py` wires the two options and the error paths.

**Tech Stack:** Python 3.11+, Typer CLI, pytest with `CliRunner`. No new dependencies.

## Global Constraints

- `--file` and `--last` together is an error, exit code 1 (matches audit's existing error style; do not use exit 2).
- A missing or unreadable `--file` is a hard error, exit code 1, never a row in the `unreadable` list.
- `--file` never reads or writes the audit cache.
- `--file` bypasses the project-scope filter; `--since`, the `name` drilldown, and `--json` still apply.
- `--last` runs the normal pipeline (cache, scope, `--global`, `--harness`, `--since`) and narrows afterward.
- Error messages use the existing `[red]error:[/red]` Rich style and `escape()` on user input.
- Run tests with `uv run pytest` from the repo root.

---

### Task 1: Adapter `trace_root()` and `pipeline.infer_adapter()`

**Files:**
- Modify: `src/drskill/traces/claude_code.py:32-36`
- Modify: `src/drskill/traces/codex.py:21-25`
- Modify: `src/drskill/traces/pi.py:21-25`
- Modify: `src/drskill/traces/copilot.py:21-26`
- Modify: `src/drskill/traces/pipeline.py`
- Test: `tests/test_traces_pipeline.py`

**Interfaces:**
- Consumes: existing `ADAPTERS` dict in `pipeline.py`; each adapter's existing `discover(home)`.
- Produces: `trace_root(home: Path) -> Path` on every adapter module; `pipeline.infer_adapter(path: Path, home: Path)` returning an adapter module; `pipeline.UnknownTraceLocation(Exception)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_traces_pipeline.py`. The file already imports `datetime as dt`, `json`, and `from drskill.traces import cache, pipeline`; add `import pytest` at the top.

```python
def test_infer_adapter_maps_known_roots(tmp_path):
    cases = {
        "claude-code": tmp_path / ".claude" / "projects" / "-a" / "s1.jsonl",
        "codex": (tmp_path / ".codex" / "sessions" / "2026" / "08" / "11"
                  / "rollout-1.jsonl"),
        "pi": tmp_path / ".pi" / "agent" / "sessions" / "-a" / "s1.jsonl",
        "copilot": (tmp_path / "Library" / "Application Support" / "Code"
                    / "User" / "workspaceStorage" / "w1" / "chatSessions"
                    / "s1.json"),
    }
    for harness, path in cases.items():
        assert pipeline.infer_adapter(path, tmp_path).HARNESS == harness


def test_infer_adapter_unknown_location_raises(tmp_path):
    with pytest.raises(pipeline.UnknownTraceLocation):
        pipeline.infer_adapter(tmp_path / "elsewhere" / "t.jsonl", tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_traces_pipeline.py::test_infer_adapter_maps_known_roots tests/test_traces_pipeline.py::test_infer_adapter_unknown_location_raises -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'infer_adapter'`

- [ ] **Step 3: Add `trace_root()` to each adapter and refactor `discover()` to use it**

In `src/drskill/traces/claude_code.py`, replace the existing `discover`:

```python
def trace_root(home: Path) -> Path:
    return home / ".claude" / "projects"


def discover(home: Path) -> list[Path]:
    root = trace_root(home)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"))
```

In `src/drskill/traces/codex.py`:

```python
def trace_root(home: Path) -> Path:
    return home / ".codex" / "sessions"


def discover(home: Path) -> list[Path]:
    root = trace_root(home)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*/*/rollout-*.jsonl"))
```

In `src/drskill/traces/pi.py`:

```python
def trace_root(home: Path) -> Path:
    return home / ".pi" / "agent" / "sessions"


def discover(home: Path) -> list[Path]:
    root = trace_root(home)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"))
```

In `src/drskill/traces/copilot.py`:

```python
def trace_root(home: Path) -> Path:
    return (home / "Library" / "Application Support" / "Code" / "User"
            / "workspaceStorage")


def discover(home: Path) -> list[Path]:
    root = trace_root(home)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/chatSessions/*.json"))
```

- [ ] **Step 4: Add `UnknownTraceLocation` and `infer_adapter` to `pipeline.py`**

Insert after the `ADAPTERS` dict in `src/drskill/traces/pipeline.py`:

```python
class UnknownTraceLocation(Exception):
    """A --file path outside every adapter's trace root."""


def infer_adapter(path: Path, home: Path):
    resolved = path.resolve()
    for adapter in ADAPTERS.values():
        if resolved.is_relative_to(adapter.trace_root(home).resolve()):
            return adapter
    raise UnknownTraceLocation(str(path))
```

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/test_traces_pipeline.py tests/test_traces_claude_code.py tests/test_traces_codex.py tests/test_traces_pi.py tests/test_traces_copilot.py -v`
Expected: all PASS (the `discover` refactor must not change behavior)

- [ ] **Step 6: Commit**

```bash
git add src/drskill/traces/ tests/test_traces_pipeline.py
git commit -m "feat(audit): adapter trace_root() and pipeline.infer_adapter()"
```

---

### Task 2: `pipeline.run_audit_file()`

**Files:**
- Modify: `src/drskill/traces/pipeline.py`
- Test: `tests/test_traces_pipeline.py`

**Interfaces:**
- Consumes: `ADAPTERS`, `infer_adapter(path, home)`, `UnknownTraceLocation` from Task 1; existing `AuditData` model; each adapter's `extract(path) -> ExtractResult` and `HARNESS`.
- Produces: `run_audit_file(home: Path, path: Path, harness: str | None, since: dt.datetime | None) -> AuditData`. Raises `UnknownTraceLocation` when the path is outside every root and no harness was given; lets extraction exceptions (OSError etc.) propagate to the caller.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_traces_pipeline.py`. The file's existing helpers `_claude_event(cwd, skill, ts=...)` and `_write_claude(home, project_dir, cwd, skill, session, **kw)` are reused.

```python
def test_run_audit_file_bypasses_project_scope(tmp_path):
    f = _write_claude(tmp_path, "-b", "/somewhere/else", skill="outproj")
    data = pipeline.run_audit_file(tmp_path, f, harness=None, since=None)
    assert [i.name for i in data.invocations] == ["outproj"]


def test_run_audit_file_applies_since(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-a"
    d.mkdir(parents=True)
    f = d / "s1.jsonl"
    events = [_claude_event("/p", "old", ts="2026-01-01T00:00:00.000Z"),
              _claude_event("/p", "new", ts="2026-07-20T00:00:00.000Z")]
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    cutoff = dt.datetime(2026, 6, 1, tzinfo=UTC)
    data = pipeline.run_audit_file(tmp_path, f, harness=None, since=cutoff)
    assert [i.name for i in data.invocations] == ["new"]


def test_run_audit_file_harness_overrides_inference(tmp_path):
    d = tmp_path / "exports"
    d.mkdir()
    f = d / "t.jsonl"
    f.write_text(json.dumps(_claude_event("/p", "moved")) + "\n")
    data = pipeline.run_audit_file(tmp_path, f, harness="claude-code",
                                   since=None)
    assert [i.name for i in data.invocations] == ["moved"]


def test_run_audit_file_writes_no_cache(tmp_path):
    f = _write_claude(tmp_path, "-a", "/p", skill="release")
    pipeline.run_audit_file(tmp_path, f, harness=None, since=None)
    cdir = cache.audit_cache_dir(tmp_path)
    assert not (cdir.is_dir() and list(cdir.glob("*.json")))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_traces_pipeline.py -k run_audit_file -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'run_audit_file'`

- [ ] **Step 3: Implement `run_audit_file`**

Add to `src/drskill/traces/pipeline.py`, after `run_audit`:

```python
def run_audit_file(
    home: Path,
    path: Path,
    harness: str | None,
    since: dt.datetime | None,
) -> AuditData:
    """Audit one explicit trace file: no cache, no project-scope filter."""
    adapter = ADAPTERS[harness] if harness else infer_adapter(path, home)
    result = adapter.extract(path)
    data = AuditData()
    data.invocations = [
        i for i in result.invocations
        if since is None or i.timestamp >= since
    ]
    if result.recognized == 0 and path.stat().st_size > 0:
        data.drifted[adapter.HARNESS] = 1
    data.invocations.sort(key=lambda i: i.timestamp)
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_traces_pipeline.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/traces/pipeline.py tests/test_traces_pipeline.py
git commit -m "feat(audit): run_audit_file() for explicit trace files"
```

---

### Task 3: `last` narrowing in `run_audit()`

**Files:**
- Modify: `src/drskill/traces/pipeline.py:28-66`
- Test: `tests/test_traces_pipeline.py`

**Interfaces:**
- Consumes: existing `run_audit(home, root, global_mode, harness, since)`.
- Produces: `run_audit(home, root, global_mode, harness, since, last=False)` — `last` is keyword-with-default so every existing call site keeps working. When `last=True`, only invocations sharing the newest invocation's `source_file` survive.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_traces_pipeline.py`:

```python
def test_last_keeps_only_newest_session(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_claude(tmp_path, "-a", str(root), skill="older", session="s1",
                  ts="2026-07-01T10:00:00.000Z")
    _write_claude(tmp_path, "-a", str(root), skill="newer", session="s2",
                  ts="2026-07-02T10:00:00.000Z")
    data = pipeline.run_audit(tmp_path, root, global_mode=False,
                              harness=None, since=None, last=True)
    assert [i.name for i in data.invocations] == ["newer"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_traces_pipeline.py::test_last_keeps_only_newest_session -v`
Expected: FAIL with `TypeError: run_audit() got an unexpected keyword argument 'last'`

- [ ] **Step 3: Implement the narrowing**

In `run_audit`, change the signature line to:

```python
def run_audit(
    home: Path,
    root: Path,
    global_mode: bool,
    harness: str | None,
    since: dt.datetime | None,
    last: bool = False,
) -> AuditData:
```

and replace the final two lines (`data.invocations.sort(...)` / `return data`) with:

```python
    data.invocations.sort(key=lambda i: i.timestamp)
    if last and data.invocations:
        newest = data.invocations[-1].source_file
        data.invocations = [
            i for i in data.invocations if i.source_file == newest
        ]
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_traces_pipeline.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/traces/pipeline.py tests/test_traces_pipeline.py
git commit -m "feat(audit): last=True narrows run_audit to the newest session"
```

---

### Task 4: CLI wiring for `--file` and `--last`

**Files:**
- Modify: `src/drskill/cli.py:640-696` (the `audit` command)
- Test: `tests/test_cli_audit.py`

**Interfaces:**
- Consumes: `tpipeline.run_audit(..., last=...)` (Task 3), `tpipeline.run_audit_file(...)` (Task 2), `tpipeline.UnknownTraceLocation`, `tpipeline.ADAPTERS`. `cli.py` already has `console`, `escape`, `typer`, and `_home()`.
- Produces: `drskill audit --file <path>` and `drskill audit --last`, with the error behavior in Global Constraints.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_audit.py` (reuses the module's `runner` and `_claude_trace(home, cwd, skill=...)` helper, which writes `home/.claude/projects/-a/s1.jsonl`):

```python
def test_audit_file_and_last_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", "x.jsonl", "--last"])
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_audit_file_missing_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 1
    assert "no such trace file" in result.output


def test_audit_file_bypasses_project_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, "/somewhere/else")  # other project's session
    trace = tmp_path / ".claude" / "projects" / "-a" / "s1.jsonl"
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(trace)])
    assert result.exit_code == 0
    assert "release" in result.output


def test_audit_file_outside_roots_needs_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, str(repo))
    src = tmp_path / ".claude" / "projects" / "-a" / "s1.jsonl"
    moved = tmp_path / "export.jsonl"
    moved.write_text(src.read_text())
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(moved)])
    assert result.exit_code == 1
    assert "--harness" in result.output
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(moved),
                                 "--harness", "claude-code"])
    assert result.exit_code == 0
    assert "release" in result.output


def test_audit_last_narrows_to_newest_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    d = tmp_path / ".claude" / "projects" / "-a"
    d.mkdir(parents=True)
    for session, skill, ts in [
        ("s1", "olderskill", "2026-07-01T10:00:05.000Z"),
        ("s2", "newerskill", "2026-07-02T10:00:05.000Z"),
    ]:
        event = {
            "type": "assistant", "sessionId": session, "timestamp": ts,
            "cwd": str(repo), "isSidechain": False,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Skill",
                 "input": {"skill": skill}}]},
        }
        (d / f"{session}.jsonl").write_text(json.dumps(event) + "\n")
    result = runner.invoke(app, ["audit", "--root", str(repo), "--last"])
    assert result.exit_code == 0
    assert "newerskill" in result.output
    assert "olderskill" not in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_audit.py -v`
Expected: the five new tests FAIL (Typer exits with code 2 and "No such option: --file"); the existing tests still PASS.

- [ ] **Step 3: Implement the CLI wiring**

In `src/drskill/cli.py`, add two parameters to `def audit(...)` after the existing `since` option:

```python
    file: Path | None = typer.Option(
        None, "--file", help="audit one trace file (see also --harness)"
    ),
    last: bool = typer.Option(
        False, "--last", help="only the most recent session in scope"
    ),
```

Then replace the single line `data = tpipeline.run_audit(home, root, global_mode, harness, cutoff)` with:

```python
    if file is not None and last:
        console.print("[red]error:[/red] --file and --last cannot be combined")
        raise typer.Exit(1)
    if file is not None:
        if not file.is_file():
            console.print(
                f"[red]error:[/red] no such trace file: {escape(str(file))}"
            )
            raise typer.Exit(1)
        try:
            data = tpipeline.run_audit_file(home, file, harness, cutoff)
        except tpipeline.UnknownTraceLocation:
            valid = ", ".join(sorted(tpipeline.ADAPTERS))
            console.print(
                f"[red]error:[/red] {escape(str(file))} is outside every "
                f"known trace location; pass --harness to pick the parser "
                f"(valid ids: {valid})"
            )
            raise typer.Exit(1)
        except Exception as exc:
            console.print(
                f"[red]error:[/red] could not read {escape(str(file))}: "
                f"{escape(str(exc))}"
            )
            raise typer.Exit(1)
    else:
        data = tpipeline.run_audit(
            home, root, global_mode, harness, cutoff, last=last
        )
```

Note the `except tpipeline.UnknownTraceLocation` clause must precede `except Exception`, and the `typer.Exit` raises live inside `except` blocks so they are not swallowed by `except Exception`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_audit.py tests/test_traces_pipeline.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/drskill/cli.py tests/test_cli_audit.py
git commit -m "feat(cli): audit --file and --last selectors"
```

---

### Task 5: README documentation

**Files:**
- Modify: `README.md` (the audit section, around lines 187-238)

**Interfaces:**
- Consumes: the shipped CLI behavior from Task 4.
- Produces: user-facing docs for both options.

- [ ] **Step 1: Add the docs**

In the audit section of `README.md`, after the existing example block that shows `drskill audit --global --since 30d` (line 206), add prose and examples in the README's existing plain style:

```markdown
To audit only your most recent session, pass `--last`. It applies the normal
scope filters first, so it means the newest session for this project, or the
newest session anywhere when combined with `--global`:

```bash
drskill audit --last
drskill audit --last --global --harness claude-code
```

To audit one specific trace file, pass `--file`. The parser is inferred from
the file's location, e.g., a path under `~/.claude/projects/` is read as a
Claude Code trace. For a file outside the known trace locations, add
`--harness` to name the parser. A `--file` audit reads the whole file even
when its sessions belong to another project, and it skips the audit cache:

```bash
drskill audit --file ~/.claude/projects/-Users-you-proj/abc123.jsonl
drskill audit --file ./exported-session.jsonl --harness claude-code
```
```

- [ ] **Step 2: Verify the full suite still passes**

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document audit --file and --last"
```
