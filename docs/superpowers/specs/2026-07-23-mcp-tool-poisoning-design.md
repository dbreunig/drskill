# MCP tool poisoning and named rug pulls

Date: 2026-07-23
Status: approved
Parent documents: `initial_design_doc.md` (section 9), `docs/superpowers/specs/2026-07-21-mcp-handshake-design.md`, `docs/superpowers/specs/2026-07-20-tier3-injection-design.md`

This is the third MCP cycle. It adds two things. First, the Tier 3 injection checks now cover MCP tool text, which they skip today. Second, the rug-pull warning now names the tools that changed and shows the old and new text, instead of only reporting that the set changed.

## Why

A tool description is instruction text that the agent reads at session start, the same as a skill description. Published MCP attacks hide instructions in exactly this text, and they favor the parameter descriptions inside a tool's input schema because most clients never show those to the user. drskill scans every line of a skill for injection surfaces and scans tool text not at all. This cycle closes that gap.

The rug-pull warning has a related problem. When a server changes a tool after the user approved the set, the current warning says the set changed and asks for a re-ack. It cannot say which tool changed or what the text used to be, because each handshake overwrites the only snapshot. The user has repeatedly asked for findings that carry their evidence, and a warning about a changed tool should quote the change.

## Schema text in snapshots

`ToolInfo` gains a `schema_text` field, a list of strings with an empty default. Snapshots written by 0.6.0 load unchanged and scan with an empty schema surface until the user reconnects.

At handshake time, an extractor walks the tool's input schema and collects three kinds of strings. It collects property names, `description` values, and `title` values. It visits keys in sorted order at every level, so the output is deterministic. It skips `enum`, `const`, `default`, and `examples` values, so the snapshot stays free of data values and stays small.

`tool_fingerprint_base` and the fingerprint in `mcp-tools-unreviewed` extend to include the schema text. An edit that touches only the schema now resurfaces the finding, which it must, because the schema is part of what the user approved.

## The coverage upgrade is not a rug pull

Extending the fingerprint means every existing `mcp-tools-unreviewed` ack mismatches after the user upgrades and reconnects. That mismatch is drskill covering more text, not a server changing its tools, and it must not read like an attack or fail CI.

The check can tell the two apart. It recomputes the old fingerprint, the one over names and descriptions only, from the current snapshot. If that matches the ack, the descriptions are unchanged and only the coverage grew. In that case the finding is a note, and its message says that drskill now also fingerprints schema text and asks for one re-ack to extend the baseline. If the old fingerprint also mismatches, the server changed its text, and the finding is the warning described below.

## The poisoning check

A new check, `mcp-tool-poisoning`, lives in `checks/mcp_injection.py`. It runs on every scan and reads the loaded snapshots, so one person connects and the whole team gets the findings. For each tool it scans the name, the description, and each schema text string.

It reuses the lexicons from `checks/injection.py` by import. It applies the ones that make sense for pure text and adds one new one.

| category | severity | fires when |
|---|---|---|
| invisible unicode | error | Tool text contains invisible or bidirectional control characters. Same character set as `injection-unicode`. |
| credential read | error | Tool text references credential paths, e.g. `.ssh` or `.aws`. Same credential store patterns as `injection-credential-read`. The separate `.env` pattern is not applied to tool text, because tools that manage configuration mention `.env` legitimately. |
| override | warning | Tool text contains instruction-override phrasing, e.g. "do not tell the user". Same patterns as `injection-override`. |
| encoded blob | warning | Tool text contains long base64 or hex runs. Same patterns as `injection-encoded-blob`. |
| remote fetch | warning | Tool text tells the agent to fetch remote content and act on it. Same patterns as `injection-remote-fetch`. |
| cross-tool interference | warning | Tool text steers the agent away from other tools, e.g. "do not use the X tool", "instead of the X tool", "before using any other tool", "always use this tool first". New lexicon, a module constant in `checks/mcp_injection.py`. |

Two skill categories do not apply and are skipped. There is no bundled script, so `injection-mandatory-script` has no subject. MCP tools exist to talk to the outside world, so an egress check would fire on every server.

Findings aggregate per server and category. Each finding quotes up to three evidence lines in the form `tool-name: "snippet"`, plus a count of the rest, using `text.one_line` for truncation. The report already sanitizes invisible characters at render time, so quoted attack text displays safely. The fingerprint hashes the full text of the tools that hit, with the server name as the extra key, so an ack survives edits to tools without hits. The fix command is to remove or disable the server. There is no file to delete, and the existing refusal for names that start with a dash carries over.

The check is orthogonal to `mcp-tools-unreviewed`. A poisoning finding does not block acking the baseline, and each is acked on its own.

## Naming the changed tools

Acking `mcp-tools-unreviewed` for a server now also copies that server's current snapshot to `.drskill/cache/mcp-tools/approved/<config_hash>.json`. The copy goes in the same scope, project or global, as the snapshots the scan read. Both ack paths do this, the `drskill ack` command and the review loop, because they share the same ack write path. The approved copy is a committed artifact, so a teammate's scan can diff against it even though the approval lives in someone else's ledger scope.

When the rug-pull warning fires and an approved copy exists, the check diffs the current snapshot against the approved one. `changed_tools` in `mcp_connect.py`, which is currently unused, does the comparison, extended to cover schema text. The warning names each difference:

- A changed description shows the tool name with a `- old` line and a `+ new` line, each truncated to one line. This is the same diff form the description-rewrite findings use.
- A change only in schema text names the tool, says the schema text changed, and quotes up to three changed strings.
- An added tool shows a `+` line with its name and description.
- A removed tool shows a `-` line with its name.

The warning lists up to five tools, plus a count of the rest. When no approved copy exists, e.g. the ack predates this cycle, the warning keeps its current wording.

## Cache commands

`cache stats` counts the approved snapshots alongside the regular ones. `cache prune` removes an approved snapshot when its config hash matches no configured server, the same rule regular snapshots follow. An approved copy for a still-configured server whose ack was removed is left alone. It is harmless, and the next ack overwrites it.

## Structure

- `src/drskill/mcp_connect.py` gains the schema text extractor, the `schema_text` field, the approved snapshot save and load helpers, and the extended `changed_tools`.
- `src/drskill/checks/mcp_injection.py` is new and holds `mcp-tool-poisoning` and the cross-tool interference lexicon.
- `src/drskill/checks/mcp_tools.py` gains the coverage-upgrade note and the named diff in the rug-pull warning.
- The pipeline exposes the loaded snapshots on the world, so the checks can read schema text. Tool contributors do not change shape.
- `src/drskill/cli.py` hooks the approved copy into the ack path and extends `cache stats` and `cache prune`.

Ships as 0.7.0.

## Out of scope

- Enumerating resources or prompts. Still deferred.
- Egress and mandatory-script checks on tool text. Skipped by design, see above.
- Scanning tool call results. drskill never calls a tool.
- Any automatic reconnect. A refresh is always an explicit `--mcp-connect`.

## Testing

- Extractor tests. The same schema always yields the same strings, in the same order. Enum, const, default, and examples values never appear in the output.
- Compatibility. A 0.6.0 snapshot without `schema_text` loads and scans, and the poisoning check runs on its descriptions.
- Poisoning. One fixture per category fires, the severities split as specified, evidence quotes the tool name and snippet, and an ack survives an edit to a tool without hits.
- Coverage upgrade. An ack made against the old fingerprint plus an unchanged snapshot yields a note, not a warning.
- Rug pull. Both ack paths write the approved copy. A changed description, a schema-only change, an added tool, and a removed tool each render the specified diff line. A missing approved copy falls back to the current wording.
- Prune. An approved snapshot for a dropped server is removed, and one for a configured server survives.
- False positives. The cross-tool lexicon and the reused lexicons run over the author's live server snapshots and the skill corpora text, with a regression test per accepted pattern, the same discipline as Tier 3.
- The real machine gate. Reconnect the author's six servers, confirm the one-time coverage note reads sanely, and confirm the poisoning check is silent on the live set.
