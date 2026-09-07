# Explain Command — Reconciled Design

Status: approved for implementation
Supersedes the open decision in 2026-07-21-explain-command-design.md

## The reconciliation

The 2026-07-21 spec deferred explain "to the deep cycle" with this decision recorded: the routing judgment should come from a model reading the descriptions, with the lexical scorer available as a free fallback tier. The deep layer has existed since 0.3.0. This design implements both tiers.

- Tier 1, always available and offline: the lexical scorer exactly as the 2026-07-21 spec mechanics describe it. A query is scored against every effective skill and MCP tool description per harness by blending a unigram cosine (weight 0.7) and a 2-word shingle cosine (weight 0.3) over content tokens. Command-kind contributors are excluded: they never route.
- Tier 2, behind `--deep`: one QueryJudge model call per distinct ranking group. The judge reads the query plus the tier-1 top five names and descriptions and returns which one would route (or none), whether the call is contested, and a rationale. The judged verdict overrides the lexical verdict line in display; lexical scores remain visible. Query judgments are not cached in v1: queries are ad hoc and the cost is one call.

## Command

`drskill explain "<query>" [--global] [--harness <id>] [--json] [--deep]`

Read-only: never writes seen state, ledgers, caches, or baselines. Top five matches per harness effective set; zero-score rows never print. Harnesses whose entire ranking is identical group into one table using the "all N harnesses" display convention. Each table carries a verdict line:

- "no skill matches" when the top score is below the floor (module constant 0.1)
- "contested" when the gap between first and second is below `thresholds.routing_margin` (ledger-configurable, default 0.1)
- "routes to <name>" otherwise

All skill-controlled text is escaped through the report sanitizer (exposed as `report.sanitize`). Output ends with a fixed disclaimer that this is drskill's own similarity model (or, with `--deep`, the configured model's judgment), not the harness's real router. `--json` emits the query, floor, margin, per-harness pre-grouping rankings, and the judge verdicts when `--deep` ran.

## The query-routing check

A `[[queries]]` ledger table records queries that must keep routing predictably:

```toml
[[queries]]
query = "summarize this pdf"
expect = "pdf-tools"   # optional
```

Queries stay with the mode's own ledger, like budgets and thresholds; they do not merge across scopes. A new `query-routing` check runs tier 1 (never the model — checks stay offline) over each configured query per harness effective set and emits a warning when the query is contested or when `expect` is set and does not win. Findings fingerprint over the query plus the ranked names and descriptions, so acks survive unrelated edits and re-fire when routing-relevant text changes. The check gates `scan --ci` like any warning.

## Out of scope

- Caching or batching query judgments.
- IDF weighting, corpus tuning of the blend constants beyond the shipped defaults.
- A `--save` flag writing queries into the ledger from the command line.
