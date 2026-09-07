# Audit Cross-Reference — Design

Status: approved for implementation
Delivers the "natural v2" from 2026-07-23-audit-design.md: cross-referencing audit numbers with the scanned world

## The idea

`drskill audit` shows what runs. The scan shows what is installed. Neither answers "what is installed and never runs" — the skill paying a catalog-token tax on every turn while nobody invokes it. This cycle joins the two: the audit report gains an Unused section listing scanned contributors with zero invocations in the covered trace history.

## Principles kept from v1

Audit stays reporting-only: no findings, no acks, no `--ci` effect. Turning staleness into ackable findings is deliberately deferred until the join proves reliable — the same reasoning v1 used to defer this feature. The join runs only inside `drskill audit`; `scan` never reads trace files.

## The join

A pure function in `traces/crossref.py` takes the scanned world, the invocation list, resolved pins, a threshold, and today's date, and returns unused contributors. Matching rules:

- A skill or command contributor is used when any invocation's name equals its bare `name` or its plugin-qualified `suite:name` (Claude Code records plugin skills that way).
- An MCP tool contributor is used when an invocation's `(server, name)` matches: the contributor's server name comes from resolving its id's config-hash prefix through `world.mcp_servers`, the same way suites do.
- System contributors are skipped.

Two guards keep false positives out:

- Coverage guard: a contributor only counts as unused when at least one harness it is deployed to has trace coverage spanning at least `unused_days` (the harness's earliest invocation timestamp is at least that old). No traces, or a young trace window, means "unknown", not "unused". A contributor used only through a short-retention harness can still be flagged once a long-retention harness covers the window; the guard bounds staleness per covered harness, not per usage habit.
- Freshness guard: a contributor whose pin records an `installed_at` younger than `unused_days` is skipped — a fresh install has not had time to be used. Unpinned contributors rely on the coverage guard alone.

## Configuration and output

`drskill.toml` gains a non-merging block:

```toml
[usage]
unused_days = 90
```

`drskill audit` gains `--unused-days N` overriding the config. The report appends an Unused section after the rollup:

```
Unused (no invocations in the covered history; threshold 90 days):
  skill     old-helper    claude-code, codex
  mcp tool  create-page   pencil
```

with one line per contributor (kind, name, harness list or server). When the guards leave nothing checkable, the section says so instead of staying silent ("Unused: not enough trace coverage to judge (needs 90 days)."). `--json` gains an `"unused"` array; each entry is `{"kind", "name", "server"}` for an MCP tool or `{"kind", "name", "harnesses"}` (a list) for a skill or command. Drill-down and the rest of the report are unchanged.

## Out of scope

- Findings, acks, or CI gating on staleness (a later cycle, once the join has proven itself).
- Removing or suggesting removal commands.
- New trace adapters.
- Per-harness unused thresholds.
