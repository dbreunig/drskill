# Loadout MCP Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the deferred gaps from the loadout MCP install work: hostile-manifest hardening, the drift-hold bypass, the MCP-only install header, Windows portability warnings, a Codex TOML writer, and `loadout update` refreshing drifted MCP entries.

**Architecture:** All changes extend code that landed in the 2026-09-05 loadout-mcp-install work. `mcp_write.py` gains metadata validation, a raw-name collision guard, and a codex-toml append writer. `cli.py`'s install command wires those in and fixes the header. `loadout_drift.py` returns the matched server on an `EntryStatus` so `update` can rebuild drifted MCP entries through `manifest_build.server_to_entry`.

**Tech Stack:** Python 3.11+, typer, pydantic, pytest, stdlib tomllib. No new dependencies.

**Spec:** The deferred items are recorded in `docs/superpowers/plans/2026-09-05-loadout-mcp-install.md` (Task 5 note) and the final-review minors from that branch. Decisions made for this plan:
- A malformed manifest entry fails that one entry with a plain message; it never crashes the command with a traceback.
- A same-named raw config entry that the parser skipped (non-dict value) must not be silently overwritten; it routes to the manual path.
- Codex TOML writing is append-only. Replacing an existing `[mcp_servers.<name>]` table in place would mean rewriting the user's TOML around comments, so a differing server is edited by hand even with `--force`. http entries stay manual for codex (its config format is stdio-oriented).
- Portability warnings add Windows absolute paths (`C:\...`, UNC `\\...`). Bare relative paths like `bin/server` stay unflagged: args such as `owner/repo` would be constant false positives.
- Once `update` can refresh MCP entries, `status` shows the standard update hint for MCP drift again (plus the reinstall hint for the restore direction).

## Global Constraints

- Run focused tests per task; run the full suite once before each commit with `uv run --extra connect --extra deep pytest` from the repo root (the bare command misses extras and fails unrelated tests).
- Env variable VALUES never appear in manifests or written configs; env vars are written with empty values (json) or empty strings (toml).
- Code comments state constraints the code cannot show. Transcribe the plan's docstrings and comments verbatim.
- Backward compatibility: entries with `source_type: "local"` keep today's behavior everywhere.
- Commit per task with a conventional prefix; end every commit message with:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

---

### Task 1: Windows portability warnings

**Files:**
- Modify: `src/drskill/manifest_build.py` (`server_portability_notes`, ~line 176)
- Test: `tests/test_manifest_build.py`

**Interfaces:**
- Produces: unchanged signature `server_portability_notes(server) -> list[str]`; only the detection set grows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_manifest_build.py` (it already has `make_server` from the previous plan):

```python
def test_portability_notes_flag_windows_paths():
    notes = manifest_build.server_portability_notes(
        make_server(command="C:\\tools\\server.exe", args=["\\\\share\\data"]))
    assert len(notes) == 2
    assert "C:\\tools\\server.exe" in notes[0]


def test_portability_notes_ignore_bare_relative_values():
    assert manifest_build.server_portability_notes(
        make_server(command="npx", args=["owner/repo", "bin/server"])) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_manifest_build.py -v -k portability`
Expected: `test_portability_notes_flag_windows_paths` FAILS (no notes produced); the ignore test passes already.

- [ ] **Step 3: Implement**

In `src/drskill/manifest_build.py`, add near the top with the other compiled patterns:

```python
# C:\..., C:/..., and UNC \\host\share paths. Bare relative values like
# "owner/repo" stay unflagged: they are routinely portable arguments.
_WINDOWS_PATH = re.compile(r"^([A-Za-z]:[\\/]|\\\\)")
```

and change the condition in `server_portability_notes`:

```python
        if value.startswith(("/", "~", "./", "../")) or _WINDOWS_PATH.match(value):
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_manifest_build.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/manifest_build.py tests/test_manifest_build.py
git commit -m "fix: flag Windows machine-local paths in MCP portability notes"
```

---

### Task 2: Metadata validation and the raw-name collision guard

**Files:**
- Modify: `src/drskill/mcp_write.py`
- Test: `tests/test_mcp_write.py`

**Interfaces:**
- Produces: `validate_metadata(metadata) -> str | None` (None when usable, else a plain error string), and `write_server(path, name, block, fmt="mcp-json", replace=False)` — the new keyword-only-by-position `replace` parameter makes overwriting an existing name explicit. Task 4 consumes both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_write.py`:

```python
def test_validate_metadata_accepts_the_real_shapes():
    assert mcp_write.validate_metadata(STDIO_METADATA) is None
    assert mcp_write.validate_metadata(HTTP_METADATA) is None


def test_validate_metadata_rejects_malformed_input():
    assert mcp_write.validate_metadata("not a dict") == "metadata is not an object"
    assert "transport" in mcp_write.validate_metadata({"transport": "carrier-pigeon"})
    assert "args" in mcp_write.validate_metadata(
        {"transport": "stdio", "command": "npx", "args": "not-a-list"})
    assert "env_names" in mcp_write.validate_metadata(
        {"transport": "stdio", "command": "npx", "args": [], "env_names": [1, 2]})
    assert "command" in mcp_write.validate_metadata(
        {"transport": "stdio", "command": ["npx"], "args": []})


def test_write_server_refuses_an_unparsed_same_name(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"Notion": "just-a-string"}}))
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA))


def test_write_server_replace_overwrites_a_parsed_name(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"Notion": {"command": "old"}}}))
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA),
                           replace=True)
    data = json.loads(path.read_text())
    assert data["mcpServers"]["Notion"]["command"] == "npx"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_write.py -v -k "validate or unparsed or replace"`
Expected: FAIL with `AttributeError: ... no attribute 'validate_metadata'` and a TypeError on the `replace` keyword.

- [ ] **Step 3: Implement**

In `src/drskill/mcp_write.py`, add after `read_servers`:

```python
def validate_metadata(metadata) -> str | None:
    """One plain error string for a malformed entry, None when usable.
    Manifests are remote input; a bad shape must fail one entry with a
    message, never crash the whole command."""
    if not isinstance(metadata, dict):
        return "metadata is not an object"
    if metadata.get("transport") not in ("stdio", "http"):
        return f"unknown transport {metadata.get('transport')!r}"
    if metadata.get("command") is not None and not isinstance(metadata.get("command"), str):
        return "command is not a string"
    if metadata.get("url") is not None and not isinstance(metadata.get("url"), str):
        return "url is not a string"
    if metadata.get("server_name") is not None and not isinstance(metadata.get("server_name"), str):
        return "server_name is not a string"
    args = metadata.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return "args is not a list of strings"
    env_names = metadata.get("env_names") or []
    if not isinstance(env_names, list) or not all(isinstance(n, str) for n in env_names):
        return "env_names is not a list of strings"
    return None
```

Change `write_server`'s signature and the mcp-json body:

```python
def write_server(path: Path, name: str, block: dict, fmt: str = "mcp-json",
                 replace: bool = False) -> None:
```

and after the `servers` non-dict check, before the assignment:

```python
    if name in servers and not replace:
        # A same-named entry the parser skipped (a non-dict value) is
        # invisible to the caller's drift check; refuse rather than
        # silently overwrite it.
        raise WriteUnsupportedError(
            f"{path} already has an entry named {name!r} that drskill "
            "cannot parse; edit it by hand")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_write.py -v`
Expected: PASS, including all pre-existing tests (they never pre-seed the written name with `replace=False`, except via the parsed-overwrite path — if `test_write_server_preserves_other_keys` writes a name not already present, it still passes unchanged).

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS. (Task 4 wires `replace` into the CLI; until then `_install_one_mcp`'s forced-replace path calls `write_server` without `replace=True` and would now raise on a parsed same-name overwrite — if `tests/test_cli_install.py::test_mcp_drifted_server_needs_force` fails at this point for that reason, make the minimal CLI edit now: pass `replace=current is not None` at the `mcp_write.write_server(cfg_path, name, block)` call in `_install_one_mcp` and include `src/drskill/cli.py` in this commit.)

```bash
git add src/drskill/mcp_write.py tests/test_mcp_write.py
git commit -m "fix: validate MCP entry metadata and guard unparsed same-name overwrites"
```

---

### Task 3: Codex TOML writer

**Files:**
- Modify: `src/drskill/mcp_write.py` (including the module docstring, which currently claims codex-toml is unwritable)
- Test: `tests/test_mcp_write.py`

**Interfaces:**
- Produces: `write_server` accepts `fmt="codex-toml"` for stdio blocks (append-only); http blocks and same-name-present raise `WriteUnsupportedError`. `read_servers(path, "codex-toml")` already works via `parse_config`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_write.py`:

```python
def test_codex_write_appends_a_stdio_server(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.6-luna"\n')
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA),
                           fmt="codex-toml")
    text = path.read_text()
    assert text.startswith('model = "gpt-5.6-luna"\n')
    servers = mcp_write.read_servers(path, "codex-toml")
    assert len(servers) == 1
    assert servers[0].name == "Notion"
    assert servers[0].command == "npx"
    assert servers[0].args == ["-y", "notion-mcp"]
    assert servers[0].env_names == ["NOTION_TOKEN"]


def test_codex_write_creates_the_file(tmp_path):
    path = tmp_path / "config.toml"
    mcp_write.write_server(path, "papers", {"command": "uvx", "args": ["papers-mcp"]},
                           fmt="codex-toml")
    servers = mcp_write.read_servers(path, "codex-toml")
    assert [s.name for s in servers] == ["papers"]


def test_codex_write_quotes_awkward_names_and_values(tmp_path):
    path = tmp_path / "config.toml"
    mcp_write.write_server(path, "my server", {"command": 'echo "hi"', "args": []},
                           fmt="codex-toml")
    servers = mcp_write.read_servers(path, "codex-toml")
    assert servers[0].name == "my server"
    assert servers[0].command == 'echo "hi"'


def test_codex_write_refuses_an_existing_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[mcp_servers.Notion]\ncommand = "old"\n')
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", {"command": "npx", "args": []},
                               fmt="codex-toml", replace=True)


def test_codex_write_refuses_http_blocks(tmp_path):
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(tmp_path / "config.toml", "Linear",
                               mcp_write.server_block(HTTP_METADATA), fmt="codex-toml")


def test_codex_write_refuses_a_corrupt_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = valid = toml")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "x", {"command": "x", "args": []}, fmt="codex-toml")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_write.py -v -k codex`
Expected: FAIL — today every codex-toml call raises `WriteUnsupportedError`, so the append/create/quote tests fail.

- [ ] **Step 3: Implement**

In `src/drskill/mcp_write.py`: add `import re` and `import tomllib` to the imports. Replace the `if fmt != "mcp-json":` guard at the top of `write_server` with:

```python
    if fmt == "codex-toml":
        _write_codex_server(path, name, block)
        return
    if fmt != "mcp-json":
        raise WriteUnsupportedError(f"{fmt} config files are not writable; add the server by hand")
```

(`_write_codex_server` takes no `replace`: an existing table always refuses, per the append-only decision — `replace=True` from the caller changes nothing for codex.)

Add at the bottom of the module:

```python
def _toml_str(value: str) -> str:
    # JSON string escaping is a valid TOML basic string, so json.dumps
    # doubles as the TOML serializer for scalars.
    return json.dumps(value)


def _write_codex_server(path: Path, name: str, block: dict) -> None:
    """Append one [mcp_servers.<name>] table. Append-only: replacing a
    table in place would mean rewriting the user's TOML around comments,
    so a same-named server always refuses, --force included."""
    if "url" in block:
        raise WriteUnsupportedError(
            "codex-toml supports stdio servers only; add http servers by hand")
    text = ""
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
            existing = tomllib.loads(text).get("mcp_servers") or {}
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise WriteUnsupportedError(f"could not read {path}: {e}")
        if name in existing:
            raise WriteUnsupportedError(
                f"{path} already has [mcp_servers.{name}]; edit it by hand")
    key = name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_str(name)
    lines = [f"[mcp_servers.{key}]",
             f"command = {_toml_str(block.get('command') or '')}",
             "args = [" + ", ".join(_toml_str(a) for a in block.get("args") or []) + "]"]
    env = block.get("env") or {}
    if env:
        lines.append("env = {" + ", ".join(f'{_toml_str(k)} = ""' for k in env) + "}")
    prefix = text if not text or text.endswith("\n") else text + "\n"
    if prefix:
        prefix += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prefix + "\n".join(lines) + "\n", encoding="utf-8")
```

Update the module docstring: it currently says codex-toml has no stdlib writer and goes to the paste path. Rewrite that paragraph to say mcp-json is read-write, codex-toml is append-only for stdio servers, and claude-user-json stays manual because it is Claude Code's whole user state file.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_write.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS. `tests/test_cli_install.py::test_mcp_codex_harness_prints_a_manual_block` still passes at this point because the CLI's `_install_one_mcp` routes `fmt != "mcp-json"` to the manual path before ever calling `write_server`; Task 4 changes that routing and updates the test.

```bash
git add src/drskill/mcp_write.py tests/test_mcp_write.py
git commit -m "feat: append MCP servers to codex config.toml"
```

---

### Task 4: Install wiring — codex flow, hostile metadata, header

**Files:**
- Modify: `src/drskill/cli.py` (install command's mcp listing and loop, `_install_one_mcp`)
- Test: `tests/test_cli_install.py`

**Interfaces:**
- Consumes: `validate_metadata`, `write_server(..., replace=...)`, codex-toml writing from Tasks 2-3.
- Produces: no new interfaces; behavior changes only.

Behavior changes:

1. `_install_one_mcp` validates metadata first; a problem prints `  <name>: invalid entry (<problem>)` and returns `"failed"`. The manual-resolution branch in the install loop validates too (before building the paste block).
2. `_install_one_mcp` treats `codex-toml` like `mcp-json` instead of routing it to the manual path: read existing servers, hash-compare, hold on drift. The write call becomes `mcp_write.write_server(cfg_path, name, block, fmt, replace=current is not None)`; a `WriteUnsupportedError` still falls back to `_echo_manual_mcp` and `"manual"` (which is how codex http entries, codex forced replaces, and unparsed-name collisions surface). Only `claude-user-json` (and unknown formats) keep the immediate manual route.
3. The listing header: when the manifest has no installable skills, print `Install N MCP server(s):` instead of `Install 0 skills into ... :`. With skills present, the header counts only the skills.
4. The listing line guards non-dict metadata: print `  <name>  (MCP server, invalid metadata)` for those.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_install.py`:

```python
def test_mcp_only_manifest_gets_a_server_header(env):
    _, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Install 1 MCP server:" in result.output
    assert "skills into" not in result.output


def test_mixed_manifest_header_counts_only_skills(env):
    _, project, state = env
    state["manifest"] = manifest([hosted_entry(), mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Install 1 skill into" in result.output


def test_malformed_mcp_metadata_fails_that_entry_only(env):
    _, project, state = env
    bad = mcp_entry()
    bad["metadata"] = "surprise"
    state["manifest"] = manifest([hosted_entry(), bad])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "invalid" in result.output
    assert "1 installed" in result.output
    assert "1 failed" in result.output
    assert not (project / ".mcp.json").exists()


def test_codex_harness_appends_to_config_toml(env):
    home, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    (project / ".codex").mkdir()
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    text = (home / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.Notion]" in text
    assert "1 installed" in result.output


def test_codex_http_entry_prints_a_manual_block(env):
    home, project, state = env
    entry = mcp_entry(transport="http", command=None, args=[],
                      url="https://mcp.example.com/sse", env_names=[])
    state["manifest"] = manifest([entry])
    (project / ".codex").mkdir()
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "1 manual" in result.output
    assert not (home / ".codex" / "config.toml").exists()
```

Delete `test_mcp_codex_harness_prints_a_manual_block` (its premise — codex is unwritable — is what this task removes; `test_codex_http_entry_prints_a_manual_block` keeps the manual path covered for codex).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_install.py -v -k "header or malformed or codex"`
Expected: the four new behavior tests FAIL (old header text, AttributeError-shaped crash or wrong counts, "1 manual" instead of a written config.toml).

- [ ] **Step 3: Implement**

In `src/drskill/cli.py`:

The header (currently `typer.echo(f"Install {installable} skill...")` right after `_install_target`):

```python
    n_skills = len(hosted) + len(github)
    if n_skills:
        typer.echo(f"Install {n_skills} skill{'s' if n_skills != 1 else ''} "
                   f"into {_display_path(target)} ({scope} store):")
    else:
        typer.echo(f"Install {len(mcp)} MCP server{'s' if len(mcp) != 1 else ''}:")
```

The mcp listing loop gains a metadata guard:

```python
    for entry in mcp:
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            typer.echo(f"  {entry['name']}  (MCP server, invalid metadata)")
            continue
        transport = metadata.get("transport", "?")
        detail = _mcp_install_detail(metadata)
        suffix = f": {detail}" if detail else ""
        typer.echo(f"  {entry['name']}  (MCP server, {transport}{suffix})")
```

The manual-resolution branch inside the install loop (where `target_or_reason` is a string) validates before building the block:

```python
            if isinstance(target_or_reason, str):
                metadata = entry.get("metadata")
                problem = mcp_write.validate_metadata(metadata or {})
                if problem:
                    typer.echo(f"  {entry['name']}: invalid entry ({problem})")
                    mcp_statuses.append("failed")
                    continue
                name = metadata.get("server_name") or entry["name"]
                _echo_manual_mcp(entry, name, mcp_write.server_block(metadata), target_or_reason)
                mcp_statuses.append("manual")
                continue
```

(move the `from drskill import mcp_write` import that currently sits inside the manual branch to just above the `if mcp:` block, so the validation call and the paste block share it; `_install_one_mcp` keeps its own local import since it is a separate function.)

`_install_one_mcp` becomes:

```python
def _install_one_mcp(entry: dict, cfg_path: Path, fmt: str, *, force: bool) -> str:
    from drskill import mcp_write

    metadata = entry.get("metadata")
    problem = mcp_write.validate_metadata(metadata or {})
    if problem:
        typer.echo(f"  {entry['name']}: invalid entry ({problem})")
        return "failed"
    name = metadata.get("server_name") or entry["name"]
    block = mcp_write.server_block(metadata)
    if fmt not in ("mcp-json", "codex-toml"):
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
        mcp_write.write_server(cfg_path, name, block, fmt, replace=current is not None)
    except mcp_write.WriteUnsupportedError as err:
        _echo_manual_mcp(entry, name, block, err.message)
        return "manual"
    typer.echo(f"  {entry['name']}: {'replaced' if current else 'installed'} "
               f"in {_display_path(cfg_path)}")
    env_names = metadata.get("env_names") or []
    if env_names:
        typer.echo(f"    fill in env values for: {', '.join(env_names)}")
    return "installed"
```

Note `read_servers(cfg_path, "codex-toml")` handles a missing file (returns `[]`) and a parseable file; a corrupt TOML file surfaces later as a `WriteUnsupportedError` from the writer, which lands in the manual path.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_install.py -v`
Expected: PASS, including all pre-existing tests (the stdio project-scope flow is untouched).

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/cli.py tests/test_cli_install.py
git commit -m "feat: write codex MCP installs, fail hostile entries cleanly, fix the header"
```

---

### Task 5: EntryStatus carries the matched server

**Files:**
- Modify: `src/drskill/loadout_drift.py`
- Test: `tests/test_loadout_drift.py`

**Interfaces:**
- Produces: `EntryStatus` gains `server: object | None = None` (the matched `MCPServer` for mcp entries in the `matches`/`changed` states). `_mcp_state` returns `(state, server)` — it is module-private, so only `classify_entries` changes with it. Task 6 consumes `st.server`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loadout_drift.py` (reusing its `drift_server`/`drift_mcp_entry` helpers):

```python
def test_changed_mcp_entry_carries_the_matched_server():
    server = drift_server(config_hash="dd" * 32)
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[server])
    assert statuses[0].state == "changed"
    assert statuses[0].server is server


def test_matched_mcp_entry_carries_the_server_too():
    server = drift_server()
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[server])
    assert statuses[0].state == "matches"
    assert statuses[0].server is server


def test_missing_mcp_entry_has_no_server():
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[])
    assert statuses[0].state == "missing"
    assert statuses[0].server is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loadout_drift.py -v -k server`
Expected: FAIL with `AttributeError: 'EntryStatus' object has no attribute 'server'`.

- [ ] **Step 3: Implement**

In `src/drskill/loadout_drift.py`:

```python
@dataclass
class EntryStatus:
    entry: dict
    contributor: Contributor | None
    state: str  # matches | changed | missing | unreadable | unchecked
    note: str | None = None
    server: object | None = None  # matched MCPServer for mcp entries
```

The mcp branch in `classify_entries`:

```python
            if entry.get("source_type") == "mcp" and servers is not None:
                state, server = _mcp_state(entry, servers)
                out.append(EntryStatus(entry, None, state, server=server))
```

And `_mcp_state`:

```python
def _mcp_state(entry: dict, servers: list) -> tuple[str, object | None]:
    expected = entry.get("content_hash")
    for s in servers:
        if f"sha256:{s.config_hash}" == expected:
            return "matches", s
    metadata = entry.get("metadata") or {}
    wanted = metadata.get("server_name")
    for s in servers:
        if s.name == wanted or normalize_name(s.name) == entry.get("name"):
            return "changed", s
    return "missing", None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loadout_drift.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/loadout_drift.py tests/test_loadout_drift.py
git commit -m "feat: return the matched server on MCP entry statuses"
```

---

### Task 6: `loadout update` refreshes drifted MCP entries

**Files:**
- Modify: `src/drskill/cli.py` (the `update` command and the `status` command's hint logic)
- Test: `tests/test_cli_update.py`, `tests/test_cli_status.py`

**Interfaces:**
- Consumes: `st.server` from Task 5; `manifest_build.server_to_entry`; `_mcp_install_detail` (already in cli.py).

Behavior:

- `update` passes `servers=world.mcp_servers` to `classify_entries`. A changed mcp entry is rebuilt from the live server via `server_to_entry` (tool names from `world.mcp_snapshots` when a snapshot exists, else the entry's old `metadata["tools"]`), keeping the manifest's existing `selector` and `name`. No `_review_fetched` step: there are no files to review; the confirmation shows the new command/url line instead.
- The confirm prompt says "updated entries" instead of "updated skills".
- `status`: a changed mcp entry counts toward the update hint again (it sets `changed_here`), and the reinstall hint stays as the restore-direction alternative.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_update.py`. First extend its `env` fixture's `run_scan` mock the same way `tests/test_cli_status.py` does: build the `World` with `mcp_servers=state["mcp_servers"]` and `mcp_snapshots=state.get("mcp_snapshots", {})`, and initialize `state["mcp_servers"] = []` after the monkeypatch (mirror lines 86-88 of the current fixture; read the fixture before editing). Then:

```python
def make_update_server(config_hash="dd" * 32, command="npx"):
    from drskill.mcp import MCPServer

    return MCPServer(name="Papers", harness="claude-code", scope="project",
                     source="/tmp/.mcp.json", transport="stdio",
                     command=command, args=["-y", "papers-mcp"],
                     env_names=[], config_hash=config_hash)


def update_mcp_entry():
    return {"kind": "mcp", "selector": "mcp:papers", "name": "papers",
            "source_type": "mcp", "source_reference": "npx -y old-papers",
            "content_hash": "sha256:" + "cc" * 32, "local_only": False,
            "metadata": {"server_name": "Papers", "transport": "stdio",
                         "command": "npx", "args": ["-y", "old-papers"],
                         "url": None, "env_names": [], "tools": ["search"]}}


def test_changed_mcp_entry_republishes_from_the_live_server(env):
    state = env
    state["manifest"]["entries"] = [update_mcp_entry()]
    state["mcp_servers"] = [make_update_server()]
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    published = state["published"]["entries"][0]
    assert published["content_hash"] == "sha256:" + "dd" * 32
    assert published["metadata"]["args"] == ["-y", "papers-mcp"]
    assert published["selector"] == "mcp:papers"
    assert published["metadata"]["tools"] == ["search"]
    assert "Published revision" in result.output


def test_matching_mcp_entry_is_up_to_date(env):
    state = env
    entry = update_mcp_entry()
    entry["content_hash"] = "sha256:" + "dd" * 32
    state["manifest"]["entries"] = [entry]
    state["mcp_servers"] = [make_update_server()]
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Already up to date." in result.output
```

The fixture must capture the published manifest: if it does not already, record the POSTed `json_body["manifest"]` into `state["published"]` inside its `fake_api_request` for the revisions path (read the fixture; it already returns a revision dict there). Match the fixture's actual state-dict shape — the helpers above assume `env` yields the state dict, mirror how existing tests unpack it.

In `tests/test_cli_status.py`, update `test_changed_mcp_entry_hints_reinstall_not_update`: rename it `test_changed_mcp_entry_hints_update_and_reinstall`, and assert BOTH `"drskill loadout update drew/pack"` and the reinstall hint appear, with exit code still 1.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_update.py tests/test_cli_status.py -v -k mcp`
Expected: the update tests FAIL (today a changed mcp entry is "unchecked", so update says "Already up to date." and publishes nothing — the first test's `state["published"]` assertion fails); the status test FAILS on the missing update hint.

- [ ] **Step 3: Implement**

In `src/drskill/cli.py`, `update` command:

```python
    statuses = loadout_drift.classify_entries(
        manifest.get("entries", []), list(world.contributors.values()),
        servers=world.mcp_servers)
```

The changed loop:

```python
        for st in changed:
            entry = next(e for e in manifest["entries"]
                         if e.get("selector") == st.entry.get("selector"))
            if st.entry.get("kind") == "mcp":
                _refresh_mcp_entry(entry, st.server, world)
                continue
            files = content.collect_files(st.contributor)
            if not _review_fetched(files, home, manifest=manifest,
                                   selector=st.entry.get("selector"),
                                   name=st.entry["name"]):
                typer.echo("Update aborted.")
                raise typer.Exit(1)
            _refresh_entry(entry, files, st.contributor, creds, base)
```

(the `entry = next(...)` lookup moves above the branch; it was previously below the review call.)

The confirm prompt:

```python
            f"{len(changed)} updated entr{'ies' if len(changed) != 1 else 'y'}?",
```

New helper next to `_refresh_entry`:

```python
def _refresh_mcp_entry(entry: dict, server, world) -> None:
    """Rebuild a drifted mcp entry from the live server config. The
    selector and name stay as published so the revision diff reads as an
    update, not a remove-and-add."""
    from drskill import manifest_build

    snap = world.mcp_snapshots.get(server.config_hash)
    tools = [t.name for t in snap.tools] if snap else \
        (entry.get("metadata") or {}).get("tools") or []
    fresh = manifest_build.server_to_entry(server, tools)
    fresh["selector"] = entry["selector"]
    fresh["name"] = entry["name"]
    entry.clear()
    entry.update(fresh)
    typer.echo(f"  {entry['name']}: server config updated "
               f"({_mcp_install_detail(fresh['metadata'])})")
```

In the `status` command, the drift accounting becomes:

```python
            if line in ("changed locally since publish", "upstream has changed"):
                changed_here = True
                if st.entry.get("kind") != "skill":
                    mcp_changed_here = True
```

so `changed_here` (and the update hint when `mine`) covers mcp drift again, while `mcp_changed_here` keeps printing the reinstall alternative. Leave the reinstall echo and the `drifted` line as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_update.py tests/test_cli_status.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run --extra connect --extra deep pytest`
Expected: PASS.

```bash
git add src/drskill/cli.py tests/test_cli_update.py tests/test_cli_status.py
git commit -m "feat: republish drifted MCP entries from loadout update"
```
