# Tier 2 heuristic checks

Date: 2026-07-20
Status: approved
Parent documents: `initial_design_doc.md` (section 5, Tier 2), `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`

This is cycle 1 of 3 for the v0.2 release. Cycle 2 is the Tier 3 injection surface checks and cycle 3 is the `explain` command. Each cycle gets its own spec and plan and merges to main when done. One 0.2.0 release ships after all three.

## Why

Tier 1 catches problems with exact answers: duplicates, shadowing, spec violations, budgets. The problems that motivated this project are fuzzier: descriptions that overlap so the router cannot tell skills apart, descriptions that never say when to trigger, and instructions that contradict each other. Tier 2 covers these with deterministic heuristics. No LLM calls, no network, thresholds tunable in the ledger, and every finding ack-able.

The stated risk is noise. A doctor that cries wolf gets uninstalled. So the thresholds ship tuned against real corpora, the conformance suite carries false positive guards, and the success metric is repeat-run silence on a triaged machine.

## The four checks

All four are warnings, on by default, registered in the existing check registry. All fire only on contributors whose file is SKILL.md, matching the Tier 1 spec checks.

### `description-overlap`

Fires when two or more descriptions are so similar that a router could confuse them.

- Score every pair of contributors by cosine similarity between word shingle vectors of their `routing_text`, after stopword filtering (method below).
- Pairs at or above `[thresholds] description_overlap` are edges. Connected components with two or more members become clusters (union find).
- One finding per cluster. The message names the members and the shared trigger phrases, e.g. "4 skills all claim 'documentation'; none states an exclusive condition". Shared phrases are the longest word n-grams (n from 3 down to 1) that appear in every member's filtered description, longest first, with word-boundary substring dedup; the message shows at most 3, joined with semicolons.
- Duplicate groups collapse to one representative before clustering. Duplication is the stronger diagnosis; reporting the same skills twice is noise, and skipping only the direct edge is not enough because a bridging third skill can reunite a duplicate pair inside one cluster. Checks run independently, so the overlap check does not read another check's output. It re-derives the duplicate condition with the existing helpers: equal content hashes (exact) or a MinHash estimate at or above `[thresholds] near_duplicate` (near), both imported from `checks/duplicates.py`.
- Fingerprint: the standard formula, but hashed over each member's `routing_text` rather than whole-file content, because the check only judged descriptions. An ack survives body-only edits and resurfaces when any member's description changes. The same basis rule applies to all four checks: missing-activation and generic-description fingerprint the description, opposing-imperatives fingerprints the two bodies.

### `missing-activation`

Fires when a description never states when the skill should trigger.

- Detection is a hybrid, settled during corpus tuning: condition words ("when", "whenever", "trigger", "invoke" and their derived forms) are matched as whole tokens anywhere in `routing_text`, so a mid-sentence clause like "when building new UI" counts (a real description in anthropics/skills). Phrase patterns cover the rest: "for questions about", "if the user", "if you", "before ", "after ", "during ".
- No match and a non-empty description means the finding fires. Empty descriptions stay Tier 1's `spec-missing-description`.
- The lexicon lives in `text.py` as data. It is not user-tunable in v0.2; the ack ledger is the escape hatch.

### `generic-description`

Fires when a description contains too few distinctive words to route on.

- Tokenize `routing_text`, drop stopwords, drop tokens in a built-in generic vocabulary (help, helps, assist, assists, task, tasks, various, general, support, supports, work, works, handle, handles, manage, manages, use, uses, tool, tools, skill, skills, thing, things, stuff, item, items, way, ways).
- Fewer than `[thresholds] generic_min_distinct_tokens` distinct remaining tokens means the finding fires.
- "Helps with various tasks." fires. "Use when the user asks to rebase, squash, or bisect with git." does not.

### `opposing-imperatives`

Fires when two skills give opposite orders about the same thing.

- From each contributor's body, extract pairs with the regex `\b(always|never)\s+((?:\w+[ \t]){0,3}\w+)` , case-insensitive. Normalize the captured phrase to its first two non-glue tokens (verbs kept; articles, prepositions, and location adverbs dropped).
- Two contributors where one says "always" and the other says "never" about the same verb+object bigram produce a finding naming both skills and the phrase. This exact rule came from corpus tuning: looser set-intersection and set-containment variants fired 119 and 248 times on the hermes-agent loadout (179 skills), almost entirely on single shared verbs; the bigram rule fires zero times on all three corpora while still catching "always use tabs" against "never use tabs".
- This is deliberately low recall. It catches "always use tabs" against "never use tabs" and misses paraphrases. The check description and the report message do not pretend otherwise.

## Shared text utilities

New module `src/drskill/text.py`, used by the heuristics and available to later cycles:

- `tokenize(text) -> list[str]`: lowercase word tokens.
- `STOPWORDS: frozenset[str]`: a small built-in English list, including the SKILL.md boilerplate words (use, skill, this, when, the, user, ...). The activation lexicon matching runs before stopword removal, so lexicon phrases still match.
- `content_tokens(text) -> list[str]`: tokenize then drop stopwords.
- `shingle_vector(text, k=2) -> dict[str, int]`: counts of word k-shingles over content tokens.
- `cosine(a, b) -> float` over those vectors.
- `shared_phrases(texts, max_n=3) -> list[str]`: longest common word n-grams across all texts, for cluster messages.
- `ACTIVATION_PATTERNS` and `GENERIC_VOCAB` as module data.

`checks/duplicates.py` keeps its own MinHash. Different algorithm, different job; merging them buys nothing.

## Ledger

`[thresholds]` gains two keys with these provisional defaults:

```toml
[thresholds]
near_duplicate = 0.85
description_overlap = 0.6
generic_min_distinct_tokens = 2
```

Corpus tuning (below) has final say on the two new defaults before release; the spec numbers are starting points, not conclusions. `drskill init` writes the tuned values with comments.

## Corpus tuning

A dev-only script, `scripts/corpus.py` in the repo and excluded from the wheel, does the following:

1. Shallow-clones the corpora into a gitignored `.corpus/` directory: `anthropics/skills`, `vercel-labs/agent-skills`, and `NousResearch/hermes-agent` (its `skills/` tree, a real curated agent loadout).
2. Builds a scan world from each corpus tree and runs only the Tier 2 checks across a sweep of thresholds.
3. Emits a review sheet (markdown table per corpus): check, score, skills involved, description excerpts.

We hand-review the sheets, pick defaults that keep false positives rare on these real sets, and record the decision in the spec's ledger section and the plan.

Tuning outcome (2026-07-20, corpora: anthropics/skills at 18 skills, vercel-labs/agent-skills at 9, NousResearch/hermes-agent at 179): `description_overlap = 0.6` confirmed with zero pairs at or above 0.4 on any corpus; `generic_min_distinct_tokens = 2` confirmed with zero findings on any corpus; missing-activation flags 3, 0, and 147 skills respectively, which matches the ecosystem's known missing-condition baseline and is treated as signal, not noise; opposing-imperatives fires zero times on all corpora under the bigram rule. The tuning run also exposed and fixed a v0.1 crash: a real hermes skill contains a literal special-token string that tiktoken refused to encode. The clearest true positives and false positives get frozen as conformance cases, copying the skill text in with the upstream license noted in the case directory.

## Report and CLI

Nothing changes. Tier 2 findings flow through the existing severity sections, fix command and ack lines, `--ci` exit codes, and fingerprint merging. Cluster findings list every member, so `drskill ack description-overlap a b c` acks the cluster and resurfaces when any member changes.

## Out of scope for this cycle

- Tier 3 injection surfaces (cycle 2) and `explain` (cycle 3).
- User-tunable activation lexicon or generic vocabulary.
- Any LLM, embedding, or network dependency in these checks.
- Corpus results feeding DSPy compilation (v0.3 reuses the labeled cases; nothing to build for that now).

## Testing

- Unit tests for every `text.py` function with exact expected outputs.
- Unit tests per check: a planted pile-up cluster, a description with and without activation phrasing, a generic and a specific description, an always/never collision and a near-miss that must not fire.
- Conformance: one case per check, plus forbid entries guarding well-scoped descriptions (e.g. the existing clean-pair skills must fire none of the four).
- Corpus-derived conformance cases from the tuning pass, with licenses recorded.
- The success metric from the design doc, checked by hand before release: after one triage pass on this machine's real loadout, a rescan is quiet.
