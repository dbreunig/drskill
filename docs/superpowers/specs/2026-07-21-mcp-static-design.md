# MCP static evaluation

Date: 2026-07-21
Status: approved
Parent documents: `initial_design_doc.md` (section 9), `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`, `docs/superpowers/specs/2026-07-20-granular-verification-design.md`, `docs/superpowers/specs/2026-07-20-report-triage-design.md`

This is the first of two MCP cycles. This cycle reads config files and never launches anything. The second cycle adds the handshake behind an explicit flag, enumerates each server's tools, and runs the description conflict machinery on tool descriptions, which do not exist in the config files and only come back from a live server. The user has moved MCP evaluation ahead of the remaining deep cycles.

## Why

MCP servers are the second half of an agent's loadout. They are configured per harness in formats that never see review together, so the same problems skills have show up with no doctor watching: the same server configured twice with drifted settings, a plaintext API key sitting in a committable JSON file, a server launched as `npx -y package@latest` that runs whatever publishes tomorrow, and entries pointing at commands that no longer exist. All of that is visible in the config files alone. This cycle makes drskill read them.

## Discovery

Each harness's data gains its MCP config locations, project scope and user scope, alongside the skill paths it already has:

- Claude Code: `.mcp.json` in the project, `~/.claude.json` for user scope.
- Cursor: `.cursor/mcp.json` in the project, `~/.cursor/mcp.json` for user scope.
- VS Code and Copilot: `.vscode/mcp.json`. Its schema differs, e.g. a `servers` key instead of `mcpServers`.
- Codex: `~/.codex/config.toml`, TOML `[mcp_servers.<name>]` tables.
- Gemini CLI: `.gemini/settings.json` in the project, `~/.gemini/settings.json` for user scope.
- Cline: its VS Code globalStorage settings file. The path is platform dependent, so this one is best effort.
- Claude Desktop: a new harness entry with no skill paths and one MCP config path, `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS. Desktop is where many machines keep their largest server list, so leaving it out would miss the point of a cross-surface doctor.

Every format we claim is verified against official docs during implementation. A new per-harness `mcp_verified` facet drives the existing `?` marker and legend, so an unverified format is labeled, not asserted. A config file that fails to parse produces one finding for that file, and the scan continues.

## Model

A new `MCPServer` model carries: server name, harness id, scope (project or user), source file path, transport (stdio or http), command and args or URL, the names of its env variables, and a normalized config hash. Servers live on `world.mcp_servers`, a separate list. They are deliberately not contributors this cycle. A server has no description to route on until the handshake cycle enumerates its tools, and keeping them separate means no existing skill check needs a kind guard.

Within one harness, a project entry and a user entry with the same name resolve by that harness's documented precedence, the same way skills shadow.

Findings are ordinary findings with `mcp-` check ids. Ack, review, show, seen state, `--ci` exit codes, and scope-routed ack writes all work on them unchanged. The scan header counts servers next to skills, e.g. "30 skills, 12 MCP servers". `drskill list --mcp` prints the per-harness server table: server, transport, scope, source file.

## The checks

| check id | severity | fires when |
|---|---|---|
| `mcp-config-invalid` | error | An MCP config file exists but does not parse. |
| `mcp-shadowed-server` | warning | One harness has the same server name in project and user scope with differing config. The message names the winner by that harness's rule. |
| `mcp-diverged-server` | warning | The same server name appears across harnesses with drifted command, args, or env names. The evidence lists the fields that differ. Identical entries across harnesses are normal and produce nothing. |
| `mcp-secret-in-config` | error in project scope, warning in user scope | An env block holds a literal value that looks like a credential: a known token prefix, a high entropy string, or any non-reference value under a name ending in KEY, TOKEN, or SECRET. A `${VAR}` reference is fine. Project scope is an error because the file is committable. |
| `mcp-unpinned-server` | warning | The server command is `npx -y <pkg>`, `<pkg>@latest`, a versionless package, or a git source with no pinned rev. The fix command shows the pinned form. |
| `mcp-insecure-url` | warning | A remote server URL uses `http://`. Localhost is excluded. |
| `mcp-dead-server` | error | A stdio server's command is not on PATH, or its absolute path does not exist. |

Evidence follows the house rule: quote the config file path, the server name, and the fields that triggered the check. For the secret check, evidence quotes variable names only. A secret value never appears in any output, any fingerprint, or any file drskill writes.

## Fingerprints

Each finding fingerprints the normalized server entry it judged, identity qualified by check id and server name, following the existing convention. The secret check is the exception with a reason: its base is the server identity plus the offending variable names, never the values. Rotating a key does not resurface an acked finding, because the judged fact, a plaintext literal in this env block, has not changed. Adding a new plaintext variable does resurface it.

## Structure

- `src/drskill/mcp.py`: the `MCPServer` model, one parser per config format, and per-harness resolution.
- `src/drskill/checks/mcp.py`: the seven checks, registered in the existing registry.
- Harness data files gain the MCP config path entries and the `mcp_verified` facet.

## Out of scope

- Launching or connecting to any server. No process execution, no network, no handshake.
- Tool enumeration, tool descriptions, and tool-vs-skill conflicts. That is cycle 2, and it rides the existing overlap and deep machinery once tools exist as contributors.
- Token cost for MCP tool definitions. Requires the handshake.
- Rug-pull detection. Requires the handshake and its own ledger semantics.
- Windows and Linux paths for Claude Desktop and Cline beyond a documented macOS-first stance.

## Testing

- One fixture config per claimed format, parsed into the expected `MCPServer` entries.
- Each check: a firing fixture, a clean fixture, and the ack round trip.
- Secret hygiene: a fixture with a real-shaped key asserts the value appears nowhere in report output, JSON output, or the ledger after an ack.
- Shadowing: project and user entries for one harness, winner named per precedence.
- The parse failure path: a malformed file produces `mcp-config-invalid` and the scan continues.
- Every test sets `DRSKILL_HOME`.
- The real machine gate: `uv run drskill scan` on the author's machine, which has `~/.claude.json` and a Claude Desktop config, and the findings reviewed by hand before merge.
