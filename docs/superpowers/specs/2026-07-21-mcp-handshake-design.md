# MCP handshake

Date: 2026-07-21
Status: approved
Parent documents: `initial_design_doc.md` (section 9), `docs/superpowers/specs/2026-07-21-mcp-static-design.md`, `docs/superpowers/specs/2026-07-21-deep-foundation-design.md`, `docs/superpowers/specs/2026-07-20-tier2-heuristics-design.md`

This is the second MCP cycle. The first read config files and never launched anything. This cycle connects to the configured servers, enumerates their tools, and runs the description conflict machinery, the token bill, tool-name collisions, and rug-pull detection on the results. Connecting is opt-in behind `--mcp-connect`, because launching third-party server processes is a real change to drskill's read-only identity and the user has to ask for it.

## Why

A config file lists servers. It does not say what tools those servers inject into the context window, and the tools are where the real problems live: two servers exposing the same tool name, a tool description that collides with a skill's routing, a server that silently rewrites a tool's description after you approved it, and the raw token cost of every tool definition that loads before the user types anything. All of that needs the live server. This cycle asks the servers.

## Connection model

A new module `mcp_connect.py` holds everything that speaks the protocol, behind a lazy import of the official `mcp` SDK. The SDK is a `connect` extra on `drskill-core`, and the `drskill` metapackage depends on `drskill-core[deep,connect]`, so a full install has it from the start and a `drskill-core` install does not. This mirrors the `[deep]` pattern exactly.

`drskill scan --mcp-connect` connects to every discovered server, one at a time:

- stdio servers are spawned by argv exec, never through a shell.
- http and streamable-http servers are connected over the network.
- The only protocol operations are `initialize`, the `initialized` notification, and `tools/list`. drskill never calls a tool, and never enumerates resources or prompts.
- Each server has a 15 second timeout. A stdio child that overruns is killed. A server that fails to connect, times out, or errors produces an `mcp-connect-failed` warning carrying the error text, and the run moves to the next server.

Env values and http headers are read from the config at connect time and passed to the child process or request, never stored. The parse-time secret discipline from cycle 1 holds: no secret value is written to a snapshot, a finding, a fingerprint, or any output.

The guard mirrors `--deep`. `--mcp-connect` without the extra installed prints one line naming the install and exits 1.

## Snapshots

Each successful handshake writes one snapshot file, `.drskill/cache/mcp-tools/<config_hash>.json`, keyed by the server's cycle-1 config hash. The snapshot holds the server name, the date, and for each tool its name, its description, and the token count of its input schema. It holds no env value and no secret. The intent is that the team commits it, the same as the deep verdict cache.

Every scan, connected or not, loads the snapshots whose config hash matches a currently configured server. Tool findings, the token bill, and the conflict checks all read from snapshots, so a teammate or a CI run gets them without connecting, labeled "as of \<date\>". A snapshot whose config hash no longer matches any configured server is stale, and `cache prune` removes it alongside the verdict entries.

When `--mcp-connect` refreshes a server that already had a snapshot, the new snapshot is compared against the old one, and the list of tools whose description changed is recorded so drift evidence can name them.

## Tools as contributors

`Contributor.kind` gains the value `mcp_tool`, reserved for this since v0.1. A tool contributor carries the tool description as its `routing_text`, an id of `<config_hash>:<tool name>`, and the harnesses and scope of the server that owns it. It has no body, no bundled files, and no frontmatter.

The skill checks filter to `kind == "skill"`: spec checks, budgets, missing-activation, generic-description, opposing-imperatives, injection, and the duplicate checks all skip tool contributors, because a tool description does not follow skill conventions and would produce false findings. This filter is added to each skill check and covered by an audit test.

`description-overlap` is the exception and the point. It runs over both kinds, so a tool description that collides with another tool or with a skill falls out of the existing clustering. The deep `ConflictJudge` and `DescriptionRewrite` then work on those pairs unchanged, because they already judge a pair by its names and descriptions.

## New checks

| check id | severity | fires when |
|---|---|---|
| `mcp-connect-failed` | warning | A server did not connect, timed out, or errored during the handshake. Evidence carries the error. |
| `mcp-tool-collision` | warning | Two servers expose the same tool name into one harness's effective set. The message names both servers and the tool. |
| `mcp-tools-unreviewed` | warning | A server's tool set has not been acknowledged. One finding per server, listing each tool with its description. |

`mcp-tool-collision` and `mcp-tools-unreviewed` read the tool layer, so they fire in any scan that has snapshots, connected or not. `mcp-connect-failed` is the exception: it describes a live handshake and fires only during a `--mcp-connect` run.

`mcp-tools-unreviewed` is the rug-pull check. Its fingerprint hashes the server identity plus the sorted tool name and description pairs. Acking it is the approval: it records that exact tool set as reviewed. Any later change to a tool description changes the fingerprint, so the finding resurfaces on the next scan, and its evidence names the tools that changed using the snapshot diff. This is the existing fingerprint mechanism applied to a threat skills do not have, the server rewriting its own tools after approval.

## The context bill

`list --tokens` gains a column for each harness's MCP tool tokens. The scan summary gains one headline line naming the harness with the largest starting context and its split, for example "largest context bill: claude-code, about 14.3K tokens, 6.1K skill catalog and 8.2K MCP tool definitions". Every count is labeled approximate, the same as the existing token counts.

## Structure

- `src/drskill/mcp_connect.py`: the SDK-backed client, the per-server handshake with timeout, snapshot read and write, and the snapshot diff.
- `src/drskill/checks/mcp_tools.py`: the three new checks.
- Tool contributors are built in the pipeline from loaded snapshots and merged into `world.contributors`, so every existing consumer sees them.

## Out of scope

- Calling tools. Enumeration only.
- Enumerating resources or prompts.
- Injection heuristics on tool description text. That is a follow-up once the tool layer exists.
- Auto-refreshing stale snapshots. A refresh is always an explicit `--mcp-connect`.
- Windows support for spawning stdio servers beyond a documented macOS and Linux stance.

## Testing

- A small in-repo fake stdio server, a Python script that speaks just enough JSON-RPC to answer `initialize` and `tools/list`, drives the connect tests with no third-party process. A second fixture hangs, to prove the timeout kills it.
- Snapshot round trip: a handshake writes a snapshot, a later plain scan reads it and builds tool contributors, and the findings match.
- Kind filter audit: a fixture with one skill and one tool of deliberately skill-shaped and skill-colliding text asserts that every skill check ignores the tool, and that description-overlap does not.
- Collision: two servers with a shared tool name produce one `mcp-tool-collision`.
- Review and rug-pull: an unreviewed server fires `mcp-tools-unreviewed`, acking silences it, a changed tool description resurfaces it, and the evidence names the changed tool.
- Secret hygiene: a server whose config carries a secret env value is connected with a fake client, and the value appears in no snapshot, finding, or output.
- Connect failure and timeout: a failing server and a hanging server each produce `mcp-connect-failed` and do not stop the run.
- Every test sets `DRSKILL_HOME`, and no test launches a real third-party server.
- The real machine gate: `drskill scan --mcp-connect` against the author's six configured servers, with the tool findings, the collisions, and the context bill reviewed by hand before merge.
