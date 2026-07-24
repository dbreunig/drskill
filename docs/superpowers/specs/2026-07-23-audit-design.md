# drskill audit design

Date: 2026-07-23
Status: approved for planning

## What this feature does

`drskill audit` reads the session traces that coding agents already write to disk and reports how the user's skills and MCP tools actually get used. The rest of drskill analyzes the loadout as configured. Audit analyzes the loadout as used.

The command answers three questions:

- How often is each skill and each MCP tool invoked, and how do they rank against each other?
- What user queries led to each invocation?
- What did the agent reason right before invoking a skill or tool, on harnesses that record reasoning?

Audit is a reporting command in v1. It creates no findings, writes nothing to the ledger, and has no effect on `--ci`. Usage-based checks, e.g. a warning for a skill that has not fired in 90 days, can come later once the numbers have earned trust.

## Trace research

We inspected the machine of a user who runs many harnesses (2026-07-23). Four harnesses hold usable traces. The others were installed but had no recoverable transcripts.

### Claude Code

Traces live at `~/.claude/projects/<munged-cwd>/<session-id>.jsonl`, one JSONL file per session, one event per line. This is the richest source.

- Skill invocations are explicit. They appear as `tool_use` blocks with the name `Skill` and the skill name in `input.skill`. Slash commands appear as `<command-name>` markers inside user messages.
- MCP calls are explicit. They appear as `tool_use` blocks whose name has the form `mcp__<server>__<tool>`.
- Reasoning appears as `thinking` content blocks. Some are empty and carry only a signature, but plaintext is usually present.
- Each event carries a timestamp, a session id, and an `isSidechain` flag that marks subagent traffic. The directory name encodes the project path.

### Codex

Traces live at `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl`. A `session_index.jsonl` file lists sessions. The first line of each rollout is a `session_meta` event with the working directory, the CLI version, and a `thread_source` field that tells user sessions apart from subagent threads.

- MCP calls are explicit. `mcp_tool_call_end` events carry the server name, the tool name, and the arguments.
- Skill invocations have no dedicated event. Codex loads skills natively, so the observable signal is a shell command that reads a `SKILL.md` path inside a `function_call` or `custom_tool_call` payload. On the inspected machine 11 of 59 rollouts show this pattern.
- Reasoning is encrypted. `reasoning` payloads hold only `encrypted_content`, so audit cannot show reasoning for Codex.

### Pi

Traces live at `~/.pi/agent/sessions/<project-slug>/<timestamp>_<uuid>.jsonl`. The first line is a session header with the working directory.

- Tool calls appear as `toolCall` content blocks with a name and a structured argument object. Results arrive as separate messages with the role `toolResult`.
- Skill invocations have no dedicated event. The signal is a `read` or `bash` tool call on a `SKILL.md` path.
- Reasoning appears as plaintext `thinking` content blocks.
- No MCP calls were present on the inspected machine. Pi supports MCP, so the adapter reserves the pattern and records such calls when they appear.

### Copilot in VS Code

Traces live at `~/Library/Application Support/Code/User/workspaceStorage/<hash>/chatSessions/<uuid>.json`, one JSON object per session. The `workspace.json` file next to each storage directory maps the hash to a folder.

- Tool calls appear as response parts of kind `toolInvocationSerialized` with a tool id and a human readable `invocationMessage`. Structured arguments are not stored.
- No reasoning is recorded.
- No MCP tool call was present on the inspected machine, though MCP server startup events exist. The adapter records MCP calls by tool id prefix when they appear.

### Harnesses with no data

Gemini CLI, opencode, qwen-code, and Cursor were installed on the inspected machine but held no recoverable transcripts. Gemini's directory held only binary protobuf state from Antigravity. The qwen log file was an empty list. The Cursor SQLite records had empty conversation arrays. Audit skips a harness whose trace directory is absent or empty, and the report does not show it.

## Data model

The unit of analysis is one invocation:

```python
class Invocation(BaseModel):
    harness: str                  # claude-code | codex | pi | copilot
    session_id: str
    project: str | None           # cwd from trace metadata, None if unknowable
    timestamp: datetime
    kind: Literal["skill", "mcp_tool"]
    name: str                     # skill name, or tool name for MCP
    server: str | None            # MCP server, only when kind == "mcp_tool"
    query: str | None             # full text of the user message that opened the turn
    reasoning: str | None         # excerpt of the nearest preceding thinking text, about 200 characters
    sidechain: bool               # True when a subagent made the call
    detection: Literal["explicit", "skill-read", "command-marker"]
    source_file: Path             # the trace file, shown as evidence in drill-downs
```

Built-in tool calls such as Bash and Read are not recorded. Audit covers skills and MCP tools, which matches the scope of the rest of drskill.

Every count in the report is labeled with its detection basis. On Codex and Pi, "invoked" means "read its SKILL.md file". The report states this rather than presenting heuristic counts as exact, in the same spirit as the verification markers in scan.

## Adapters

One adapter per harness. Each adapter is a pure function from one trace file to a list of invocations.

- The claude-code adapter walks `~/.claude/projects/*/*.jsonl`. A `Skill` tool use becomes a skill invocation with detection `explicit`. A `mcp__<server>__<tool>` tool use becomes an MCP invocation. A `<command-name>` marker in a user message becomes a skill invocation with detection `command-marker`. The query is the most recent user message before the tool call in the same session, skipping tool results and sidechain messages. The reasoning is the nearest preceding thinking block in the same assistant message, or in the assistant message before it. The `isSidechain` flag maps to `sidechain`.
- The codex adapter walks `~/.codex/sessions/**/rollout-*.jsonl`. A `mcp_tool_call_end` event becomes an MCP invocation. A `SKILL.md` path inside a `function_call` or `custom_tool_call` input becomes a skill invocation with detection `skill-read`, with the skill name taken from the path. Reasoning is always None. When `session_meta` marks the thread as a subagent thread, every invocation in the file gets `sidechain = True`.
- The pi adapter walks `~/.pi/agent/sessions/*/*.jsonl`. A `read` or `bash` tool call on a `SKILL.md` path becomes a skill invocation with detection `skill-read`. MCP tool calls are recorded when they appear. Reasoning comes from thinking blocks. The project comes from the `cwd` field in the session header.
- The copilot adapter walks `~/Library/Application Support/Code/User/workspaceStorage/*/chatSessions/*.json`. A `toolInvocationSerialized` part with an MCP tool id becomes an MCP invocation. A skill tool id becomes a skill invocation. Reasoning is always None. The project comes from the neighboring `workspace.json`.

## Extraction cache

Parsing every trace on every run is too slow. The inspected machine held about 300 MB of traces across roughly 750 files. Audit therefore caches extracted invocations per trace file.

The cache lives at `~/.drskill/cache/audit/`, under `DRSKILL_HOME`. It is machine state and is never committed, because it contains excerpts of the user's prompts. This is the opposite of the verdict cache, which is committed by design.

Each cache entry is one JSON file named by the sha256 of the trace file's absolute path:

```
~/.drskill/cache/audit/<sha256(trace_path)>.json
  { "trace_path": ..., "mtime_ns": ..., "size": ...,
    "adapter": "claude-code", "adapter_version": 1,
    "invocations": [ ... ] }
```

A run works in five steps: discover trace files per harness, compare each file's `mtime_ns` and `size` against its cache entry, run the adapter on files that are new or changed, load the cached invocations, then aggregate and render. When a trace file has been deleted, its cache entry is pruned on sight. Each adapter has an `adapter_version`, and bumping it forces re-extraction, so improved heuristics never mix with stale extractions. The active session's own transcript changes constantly, so that one file re-extracts each run.

Only two kinds of trace text enter the cache: the full query text and the reasoning excerpt, the reasoning truncated to about 200 characters. Nothing else from a transcript is stored.

`drskill cache stats` and `drskill cache prune` cover this directory with the same rules as the existing caches.

Scoping happens after loading, not during extraction. Project mode keeps invocations whose project equals the current working directory. For Claude Code the munged project directory name is matched as well. `--global` skips the filter. Because the cache is always machine wide, switching scopes never re-parses anything.

## CLI surface

```
drskill audit [--global] [--harness <h>] [--since 30d] [--json]
drskill audit <name> [--global] [--harness <h>] [--since 30d] [--json]
```

The default scope is the current project, and `--global` widens to the whole machine, consistent with scan and list. The default window is all recorded history. `--since` accepts forms like `7d`, `30d`, and `2026-06-01`.

### The report

A bare `drskill audit` renders one table per harness, with skills and MCP tools in the same table, like the unified list command. Columns: name, kind, server for MCP tools, invocation count, share of that harness's invocations, sessions touched, and last used. Heuristic rows carry a `~` marker with a legend line, the same idiom as the `?` verification markers.

Sidechain calls are counted but shown separately, e.g. `12 (+9 subagent)`, so subagent traffic never inflates the headline number.

Each harness section header carries a coverage line, e.g. `coverage: 2026-05-12 to 2026-07-23 · 59 sessions · 214 invocations`. The dates come from the earliest and latest trace timestamps actually seen after `--since` filtering, so the line shows the effective window rather than the requested one. Coverage lines exist because retention differs per harness. Claude Code prunes old transcripts after a configurable period while Codex keeps everything, so a raw count comparison across harnesses can mislead.

A closing rollup ranks skills and tools across all harnesses. The rollup ranks by invocations per week within each harness's own coverage window, not by raw totals, and the raw counts stay visible in the per-harness tables. When harness windows differ by more than a factor of two, the rollup says so, e.g. `windows differ (claude-code 90d, copilot 12d); ranks compare rates, not raw counts`.

### The drill-down

`drskill audit <name>` takes a skill name or an MCP tool name, with the `server:tool` form available to disambiguate MCP tools. It prints total counts per harness, then a list of invocation contexts in reverse chronological order. Each entry shows the timestamp, the harness, the project, the query excerpt, and the reasoning excerpt when the harness recorded one. Each entry cites its source trace file, following the repo convention that findings and reports carry evidence.

When a skill and an MCP tool share a name, the drill-down prints both groups rather than guessing.

### JSON output

`--json` emits the aggregates and the underlying invocation records for tooling, in the same role scan `--json` plays.

## Error handling

Audit follows the tolerant posture of scan:

- A malformed JSONL line is skipped.
- A malformed or unreadable trace file is skipped and counted. The report footer states, e.g., `3 trace files unreadable (--json lists them)`.
- Adapters match known event shapes and ignore everything else, so a harness format change degrades to undercounting plus a visible note that the adapter saw unrecognized session files. It never crashes the run.
- A trace being appended mid-read parses up to the last complete line, which is safe for JSONL.
- A harness whose trace directory does not exist is simply absent from the report.

## Testing

Adapters are pure functions over files, so each gets fixture traces distilled from the formats sampled during research:

- A Claude Code session with Skill calls, `mcp__` calls, command markers, sidechain events, and an empty thinking block.
- A Codex rollout with an `mcp_tool_call_end` event, a SKILL.md read inside a `custom_tool_call`, and a subagent `session_meta`.
- A Pi session with thinking blocks and a SKILL.md read.
- A Copilot session JSON with a tool invocation.

Cache tests cover a hit, a miss, an mtime change, an adapter version bump, and pruning of a vanished file. Report tests cover project and global scoping, `--since`, coverage lines, the rollup ranked by rate, detection markers, and the sidechain breakout. All tests run under an isolated `DRSKILL_HOME`, per the existing suite convention.

## Out of scope for v1

- Findings, acks, and `--ci` effects. Audit reports and nothing else.
- Built-in tool calls such as Bash and Read.
- Adapters for harnesses with no observed trace data. Gemini CLI, opencode, qwen-code, and Cursor wait until real traces exist to test against.
- Cross-referencing audit numbers with scan findings, e.g. flagging a never-used skill. This is the natural v2 once the counts have proven reliable.
