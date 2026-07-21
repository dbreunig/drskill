# The explain command

Date: 2026-07-21
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`, `docs/superpowers/specs/2026-07-20-report-triage-design.md`

This is the fourth and last cycle of the v0.2 release. The 0.2.0 release ships after this cycle.

## Why

When two skills have similar descriptions, the harness router can send a query to the wrong one. Today drskill reports that risk as a description-overlap finding, which is a claim about two texts. `drskill explain` makes the risk concrete. The user types a query they would actually ask, and the command shows which skills compete for it and how close the race is. A collision that used to take an argument to establish now takes five seconds to see.

A team can also save queries in the ledger. A new check replays the saved queries on every scan. A description edit that changes where a saved query routes then fails CI in the same way any other warning does.

## Command surface

```
drskill explain "<query>" [--global] [--harness <id>] [--json]
```

`explain` runs the discovery and resolution pipeline, scores the query against the description of every skill in each harness's effective set, and prints the top five matches per harness. Five is a display depth, not a judgment, so it is a constant and not a threshold.

The command is read only. It never writes seen state, ledgers, or any other file. `--harness` scopes the run to one harness with the same rules `scan --harness` uses, including the note for a valid but undetected harness.

## Scoring

Queries are short. A realistic query has about four content tokens, so it produces about three two word shingles. The existing description-overlap scorer compares two word shingle vectors, and with so few shingles most descriptions would score zero. A ranking over zeros is noise.

`text.py` gains a query scoring function that blends two cosine scores over content tokens:

- A cosine over single token counts. This part is robust for short queries and always produces a ranking.
- A cosine over two word shingle counts, reusing `shingle_vector`. This part rewards a description that contains the query's actual phrases, so an exact phrase match ranks above a match on scattered words.

The score is `0.7 * unigram + 0.3 * shingle`, in the range 0 to 1. The weights are module constants. They start at these values and get tuned against the downloaded corpora and the author's real loadout before merge. If tuning changes them, this spec is updated with the final values.

The scorer reuses `tokenize`, `content_tokens`, `shingle_vector`, and `cosine` from `text.py`, so `explain` and `description-overlap` share one vocabulary of similarity.

## Verdicts

Each harness's ranking gets one verdict:

- If the top score is below a floor of 0.1, the verdict is that no skill matches the query. The floor is a module constant because it states when a score is meaningful at all, which is not a matter of team taste.
- If the gap between the first and second scores is below the margin threshold, the verdict is that the routing is contested between the top skills.
- Otherwise the verdict is that the query routes to the top skill.

The margin threshold is `thresholds.routing_margin` in the ledger, next to `description_overlap`. The default is 0.1. It starts at that value and gets tuned against the corpora before merge, with the final value recorded here. It is a ledger threshold because teams that replay query sets in CI need to control how strict the contested verdict is.

If only one skill scores above zero, the routing is not contested. If no skill scores above zero, the verdict is that no skill matches.

## Output

Skills that score zero never print. Harnesses whose entire ranked result is identical are grouped, and each group prints one table. The group heading lists the harness ids, and a group containing every detected harness prints as a count, e.g. "all 7", following the collapse convention from the report triage spec. On a typical machine this means one table. A harness where shadowing changes the outcome gets its own table, which is exactly the case worth seeing.

Each table starts with its verdict line, then one row per match with the rank, the score to two decimals, the skill name, and the description. All skill controlled text is escaped for rich markup and passed through the report's sanitizer, the same as every other surface.

The output ends with one line stating that the scores are drskill's own text similarity and not the harness's real router. The command simulates routing. It must not claim to observe it.

`--json` emits the query, the floor and margin in effect, and the full per harness results before grouping, so tooling gets the complete map. A `--json` run writes nothing, the same as the human run.

## The query-routing check

The ledger can hold saved queries:

```toml
[[queries]]
query = "find coffee shops in Brooklyn"
expect = "overturemaps"
```

`expect` is optional and names the skill the team believes should win. Queries are strictly local, like budgets and thresholds. Project mode reads them only from the project ledger, global mode reads them only from the machine ledger, and the two lists never merge.

A new registry check named `query-routing` runs whenever the ledger has queries. For each query it computes the same rankings and verdicts `explain` would show, and it emits one warning finding per query that fails. A query fails when either of these holds in any harness:

- The routing is contested. This fires whether or not `expect` is set. An expectation that wins by less than the margin is a regression waiting to happen.
- `expect` is set and the expected skill does not win. This covers a different skill winning, where the message names the actual winner and the expected one with both scores, and it covers the verdict that no skill matches, where the message says so.

A query whose `expect` names a skill missing from every effective set also fails, with a message saying the expected skill was not found. That covers both a typo and a skill that was removed.

The finding lists only the harnesses where the query failed, and its evidence is the ranked table for those harnesses, grouped the same way `explain` groups them. The fingerprint hashes what the check judged, identity qualified by the check id and the query text. The base covers the query, the expectation if set, and each distinct ranked outcome, meaning the ordered names and descriptions of the printed matches. An acked finding therefore stays silent until a description, the ranking, or the expectation changes, and then it resurfaces.

Nothing else is new machinery. The finding gets a 4 hex id, prints with full evidence, sorts by the report triage rules, acks by id or by check id, shows in `show` and `review`, and makes `scan --ci` exit 2 while unacknowledged.

## Out of scope

- An `explain --save` flag that appends the current query to the ledger. Hand editing the TOML is enough for this cycle.
- Weighting tokens by how rare they are across the loadout. It would sharpen rankings but make the same query score differently in different loadouts, which complicates replay.
- Any LLM or network involvement.
- Windows specific work.
- Changes to other checks.

## Testing

- Scorer: unit tests for the blend, including a short query against a phrase match versus a scattered word match, an empty query, and a query that is all stopwords.
- Verdicts: fixtures producing each of the three verdicts, the single scorer case, and the zero scorer case.
- Output: grouping of identical rankings, the all harnesses collapse, per harness split when shadowing changes a ranking, escaping of hostile description text, and the simulation disclaimer line.
- Read only: an `explain` run and an `explain --json` run leave the seen state and ledgers untouched. Tests set `DRSKILL_HOME`.
- Check: a contested query fires, an `expect` mismatch fires with both names and scores, a missing `expect` skill fires, a passing query set is silent, an ack silences a finding, and a description edit resurfaces it. `scan --ci` exits 2 on an unacked finding.
- Scope: project mode ignores machine ledger queries and global mode ignores project queries.
- Conformance: a hand written query sheet runs against the downloaded corpora and the author's loadout. Clear verdicts are frozen as conformance cases under `tests/conformance/cases/`, with `LICENSE-NOTE.md` where skill text is copied.
- The real machine gate: before merge, `uv run drskill explain` runs with real queries on the author's actual loadout, and misleading rankings get fixed, not shipped.
