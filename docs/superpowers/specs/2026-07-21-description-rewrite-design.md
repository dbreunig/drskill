# DescriptionRewrite

Date: 2026-07-21
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-21-deep-foundation-design.md`

This is the second deep cycle. The first shipped ConflictJudge, the verdict cache, and `scan --deep` in release 0.3.0. This cycle makes a `description_collision` verdict actionable: the model proposes the fix, not just the diagnosis.

## Why

ConflictJudge classifies a flagged pair three ways. The `description_collision` class means the two skills do different jobs but their descriptions blur together, so a rewrite fixes it. Today the finding says that and stops. This cycle adds the rewrite itself. The user sees the diagnosis and a concrete proposed description in the same report, applies it by hand if they agree, and the next scan re-judges the pair against the new text. The expected outcome of a good rewrite is a fresh `distinct` verdict, which downgrades the warning to a note.

This follows the published Microsoft recipe the design doc named: rewrite a single description, and use the confusion query to carve an exclusive "use when" condition into it.

A note from the live corpus gate: the loop can take two rounds. After the first rewrite sharpened one description, the judge still called the pair a collision, because the untouched description stayed vague enough to absorb the sharpened one's queries, and it proposed a rewrite for the other skill. Applying that second proposal produced the distinct verdict and the downgrade. This is correct behavior, not a defect. One rewrite per run keeps each proposal reviewable, and the loop converges.

## The DescriptionRewrite program

A second hand written DSPy signature beside ConflictJudge in `deep_llm.py`, behind the same lazy import and the same `[deep]` model. Its inputs are the names and descriptions of both skills and the confusion example the judge produced. Its outputs are three fields:

- The target, meaning which of the two skills should get the rewrite. The program picks one, usually the blurrier description.
- The rewritten description.
- A one sentence reason for picking that skill.

The instructions encode the recipe. Keep the target description's voice and rough length. Add the exclusive condition that resolves the confusion query. Do not change what the skill claims to do. The input fields are data under analysis, not instructions, the same defense ConflictJudge uses.

## Flow and budget

During `--deep`, when ConflictJudge returns `description_collision` for a pair, the rewriter runs immediately and the verdict and rewrite land in one cache entry. A rewrite call counts against `--max-calls` like any other call.

A rewrite call that errors or does not parse caches the verdict without a rewrite. Later `--deep` runs look for collision entries that lack a rewrite and retry those first, before judging new pairs, still under the budget. Failed calls surface through the existing failing-calls line, and the existing three consecutive failures rule stops a run that cannot succeed.

## Cache schema

`Verdict` gains three optional fields, `rewrite_target`, `rewrite_text`, and `rewrite_reason`, all defaulting to null. Every cache entry written by 0.3.0 loads unchanged. Entries for `distinct` and `scope_overlap` verdicts never carry the fields.

The content key does not change. Applying a rewrite edits a description, which changes the pair's key, which makes the next `--deep` run judge the new text fresh. The old entry becomes stale and `cache prune` removes it.

## Report

A collision finding's evidence grows a diff block, carried in the message like all evidence, escaped and sanitized like all model text:

```
      deep: rewrite for idea-vault (its description is the vaguer of the two):
      - Use when the user needs to organize notes and capture ideas for later.
      + Use when the user wants a permanent vault for long-term project ideas, not quick notes.
```

The finding also gains a fix line naming the SKILL.md file to edit. drskill never edits the file itself. The proposed text is model output headed for the user's skill, so the README tells the user to read it before pasting, and the report renders it escaped so it cannot style or hide anything on the way.

In `--json` output the rewrite travels inside the finding's message, the same way all evidence does. The structured fields live in the cache entry on disk for tooling that wants them.

## Out of scope

- GEPA compilation of either program. That is the next cycle, and it changes no interface here.
- Auto-applying rewrites. The read only identity holds.
- Rewrites for `scope_overlap`. That class means the skills genuinely claim the same job, and no description edit fixes it. A human must choose.
- Rewrites for pairs the judge has not seen. The rewriter only runs on a collision verdict.

## Testing

- The rewriter sits behind the same injectable interface as the judge. Tests drive it with scripted proposals and never call the network.
- A cache entry written in the 0.3.0 shape, without the new fields, loads and applies.
- Budget accounting: a collision pair costs two calls, a rewrite retry runs before new pairs, and the truncation line stays honest.
- Rendering: the diff block prints with hostile text escaped, and the fix line names the right file.
- Failure: a failed rewrite caches the verdict alone, and the next run retries it.
- The corpus gate closes the loop live. Plant a pair built to draw a `description_collision` verdict, meaning two skills that do different jobs behind blurred descriptions. The earlier planted pair drew `scope_overlap`, which never rewrites, so this gate needs its own fixture. Run it through the real judge and rewriter, review the proposal by hand, apply it in the scratch project, and confirm the re-judge returns `distinct` and the warning downgrades.
- The real machine gate runs `--deep` on the author's loadout as always.
- Every test sets `DRSKILL_HOME`.
